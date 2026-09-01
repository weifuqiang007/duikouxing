"""整头替换流水线编排入口（运行在编排环境）。

用法：
    python -m headswap.cli run --job config/headswap.hs-p1-0001.yaml --profile home
    python -m headswap.cli run --job ... --stage composite --force   # 只重跑某阶段

阶段：prepare -> reenact -> segment -> plate -> composite -> finalize
每个阶段的产物存在即跳过（--force 强制重跑），便于参数调优时只重跑后段。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import yaml

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGES = ("prepare", "reenact", "segment", "plate", "composite", "finalize")


class HeadswapError(RuntimeError):
    pass


def load_headswap_job(path: Path) -> dict:
    if not path.is_file():
        raise HeadswapError(f"任务配置不存在: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise HeadswapError(f"任务配置顶层必须是对象: {path}")
    base = path.parent
    for key in ("source_video", "source_portrait"):
        value = data.get(key)
        if not value:
            raise HeadswapError(f"任务配置缺少 {key}")
        resolved = Path(value).expanduser()
        data[key] = resolved.resolve() if resolved.is_absolute() else (base / resolved).resolve()
        if not data[key].is_file():
            raise HeadswapError(f"{key} 不存在: {data[key]}")
    sides = []
    for value in data.get("side_portraits") or []:
        resolved = Path(value).expanduser()
        p = resolved.resolve() if resolved.is_absolute() else (base / resolved).resolve()
        if p.is_file():
            sides.append(p)
    data["side_portraits"] = sides
    if not data.get("job_id") or any(ch in str(data["job_id"]) for ch in '\/:*?"<>|'):
        raise HeadswapError("job_id 为空或包含 Windows 非法字符")
    if not data.get("consent_confirmed", False):
        raise HeadswapError("未确认人物肖像授权，任务拒绝运行")
    lp = data.setdefault("liveportrait", {})
    motion_mode = str(lp.get("motion_mode", lp.get("animation_region", "all")))
    if motion_mode not in {"all", "exp", "rotation_exp", "rotation_lip"}:
        raise HeadswapError("liveportrait.motion_mode 只能是 all、exp、rotation_exp 或 rotation_lip")
    if motion_mode in {"rotation_exp", "rotation_lip"}:
        if bool(lp.get("transfer_translation", False)):
            raise HeadswapError("rotation_exp 禁止 transfer_translation=true")
        if bool(lp.get("transfer_scale", False)):
            raise HeadswapError("rotation_exp 禁止 transfer_scale=true")
        win = int(lp.get("pose_smooth_window", 7))
        if win < 1 or win % 2 == 0:
            raise HeadswapError("liveportrait.pose_smooth_window 必须为正奇数")
    comp = data.setdefault("composite", {})
    if comp.get("neck_pivot_enabled"):
        if float(comp.get("external_rotation_gain", 0.0)) != 0.0:
            raise HeadswapError("neck_pivot 模式禁止外部逐帧旋转：external_rotation_gain 必须为 0")
        if str(comp.get("scale_mode", "const")) != "const":
            raise HeadswapError("neck_pivot 模式要求 composite.scale_mode=const")
        if comp.get("freeze_head_motion"):
            raise HeadswapError("neck_pivot_enabled 与 freeze_head_motion 互斥")
    return data


def _conda_run(local, env_name: str, command: list) -> list:
    from digital_human.process import conda_run

    env = {
        "liveportrait": local.liveportrait_env,
        "orchestrator": local.orchestrator_env,
    }[env_name]
    return conda_run(local.conda, env, command)


def _run(command, log_file: Path | None = None, cwd: Path | None = None) -> None:
    from digital_human.process import run_command

    run_command([str(c) for c in command], cwd=cwd, log_file=log_file)


def _ensure_paths(job: dict, dirs: dict) -> None:
    """单阶段重跑时，从标准目录布局恢复 _paths（不执行任何前置阶段）。"""
    work, inp = dirs["work"], dirs["input"]
    p = job.setdefault("_paths", {})
    silent = job.get("composite", {}).get("silent_name", "composite_silent")
    candidates = {
        "base_video": work / "base_upright.mp4",
        "audio": work / "original_audio.wav",
        "portrait": inp / ("portrait" + Path(job["source_portrait"]).suffix),
        "animated_head": work / "animated_head.mp4",
        "plate": work / "background_plate.png",
        "composite": work / f"{silent}.mp4",
    }
    for key, path in candidates.items():
        if not p.get(key) and path.is_file():
            p[key] = str(path)
    if not p.get("segment_dir") and (work / "segment" / "meta.json").is_file():
        p["segment_dir"] = str(work / "segment")


def _require(job: dict, *keys: str) -> None:
    missing = [k for k in keys if not job["_paths"].get(k)]
    if missing:
        raise HeadswapError(
            f"缺少前置产物: {', '.join(missing)}；请先运行完整流水线或对应的前置 --stage"
        )


def stage_prepare(job: dict, local, dirs: dict) -> None:
    inp, work = dirs["input"], dirs["work"]
    src_video = work / "base_upright.mp4"
    src_audio = work / "original_audio.wav"
    copied_video = inp / job["source_video"].name
    if not copied_video.is_file():
        shutil.copy2(job["source_video"], copied_video)
    copied_portrait = inp / ("portrait" + job["source_portrait"].suffix)
    if not copied_portrait.is_file():
        shutil.copy2(job["source_portrait"], copied_portrait)
    for side in job["side_portraits"]:
        target = inp / ("side_" + side.name)
        if not target.is_file():
            shutil.copy2(side, target)

    force = bool(job.get("_force_current"))
    max_seconds = float(job.get("video", {}).get("max_seconds", 0.0))
    duration_args = ["-t", f"{max_seconds:.6f}"] if max_seconds > 0 else []
    # 烘焙旋转元数据 + 固定 30fps CFR，后续所有读取方（LivePortrait/cv2）拿到的是像素已转正的视频
    if not src_video.is_file() or force:
        _run(
            [
                local.ffmpeg, "-y", "-i", copied_video,
                "-c:v", "libx264", "-crf", "12", "-preset", "fast",
                "-pix_fmt", "yuv420p", "-r", "30", "-an", *duration_args, src_video,
            ],
            dirs["logs"] / "prepare_video.log",
        )
    # 原声单独抽成 wav；无声视频时 ffmpeg 会失败，容错跳过（finalize 检查存在性）
    if not src_audio.is_file() or force:
        try:
            _run(
                [
                    local.ffmpeg, "-y", "-i", copied_video,
                    "-vn", "-c:a", "pcm_s16le", "-ar", "48000", *duration_args, src_audio,
                ],
                dirs["logs"] / "prepare_audio.log",
            )
        except RuntimeError:
            print("[WARN] 未抽取到原声音轨，成片将为无声")
    job["_paths"] = job.get("_paths", {})
    job["_paths"].update(
        {
            "base_video": str(src_video),
            "audio": str(src_audio) if src_audio.is_file() else None,
            "portrait": str(copied_portrait),
        }
    )


def stage_reenact(job: dict, local, dirs: dict) -> None:
    from headswap.liveportrait_reenact import run_reenact

    _require(job, "portrait", "base_video")
    lp = job.get("liveportrait", {})
    output = dirs["work"] / "animated_head.mp4"
    if output.is_file() and not job.get("_force_current"):
        job["_paths"]["animated_head"] = str(output)
        return
    run_reenact(
        conda=local.conda,
        liveportrait_env=local.liveportrait_env,
        liveportrait_repo=local.liveportrait_repo,
        gpu_id=local.gpu_id,
        settings=lp,
        source=Path(job["_paths"]["portrait"]),
        driving=Path(job["_paths"]["base_video"]),
        output=output,
        work_dir=dirs["work"],
        log_file=dirs["logs"] / "reenact.log",
        use_half_precision=bool(lp.get("use_half_precision", local.use_float16)),
    )
    job["_paths"]["animated_head"] = str(output)


def stage_segment(job: dict, local, dirs: dict) -> None:
    _require(job, "base_video")
    seg = job.get("segmentation", {})
    seg_dir = dirs["work"] / "segment"
    if (seg_dir / "meta.json").is_file() and not job.get("_force_current"):
        job["_paths"]["segment_dir"] = str(seg_dir)
        return
    command = _conda_run(
        local,
        "liveportrait",
        [
            "python", PROJECT_ROOT / "src" / "headswap" / "segment_head.py",
            "--video", job["_paths"]["base_video"],
            "--output-dir", seg_dir,
            "--bisenet", PROJECT_ROOT / "models" / "facefusion" / "bisenet_resnet_34.onnx",
            "--insightface-root", local.liveportrait_repo / "pretrained_weights" / "insightface",
            "--roi-ratio", str(float(seg.get("roi_ratio", 2.6))),
            "--dilate-px", str(int(seg.get("mask_dilate_px", 8))),
            "--erode-px", str(int(seg.get("mask_erode_px", 2))),
            "--temporal-ema", str(float(seg.get("temporal_ema", 0.6))),
            "--max-fail-ratio", str(float(seg.get("max_fail_ratio", 0.05))),
            *( ["--output-raw-skins"] if seg.get("output_raw_skins") else [] ),
        ],
    )
    _run(command, dirs["logs"] / "segment.log", cwd=PROJECT_ROOT)
    job["_paths"]["segment_dir"] = str(seg_dir)


def stage_plate(job: dict, local, dirs: dict) -> None:
    from headswap.build_plate import build_plate

    _require(job, "base_video", "segment_dir")
    plate_cfg = job.get("plate", {})
    plate = dirs["work"] / "background_plate.png"
    if not plate.is_file() or job.get("_force_current"):
        stats = build_plate(
            Path(job["_paths"]["base_video"]),
            Path(job["_paths"]["segment_dir"]) / "masks",
            plate,
            sample_frames=int(plate_cfg.get("sample_frames", 50)),
            min_samples=int(plate_cfg.get("min_samples", 8)),
            mask_expand_px=int(plate_cfg.get("mask_expand_for_plate", 12)),
            inpaint_radius=int(plate_cfg.get("inpaint_radius", 5)),
        )
        (dirs["logs"] / "plate.json").write_text(
            json.dumps(stats, ensure_ascii=False), encoding="utf-8"
        )
    job["_paths"]["plate"] = str(plate)


def stage_composite(job: dict, local, dirs: dict) -> None:
    _require(job, "base_video", "animated_head", "segment_dir")
    if job.get("composite", {}).get("fill_mode", "residual") == "plate":
        _require(job, "plate")  # 仅旧版整块底板对照模式需要 plate 产物
    comp = job.get("composite", {})
    color = job.get("color", {})
    silent_name = comp.get("silent_name", "composite_silent")
    output = dirs["work"] / f"{silent_name}.mp4"
    if output.is_file() and not job.get("_force_current"):
        job["_paths"]["composite"] = str(output)
        return
    seg_dir = Path(job["_paths"]["segment_dir"])
    command = _conda_run(
        local,
        "liveportrait",
        [
            "python", PROJECT_ROOT / "src" / "headswap" / "composite_head.py",
            "--base-video", job["_paths"]["base_video"],
            "--head-video", job["_paths"]["animated_head"],
            "--masks-dir", seg_dir / "masks",
            "--skins-dir", seg_dir / "skins",
            "--meta-json", seg_dir / "meta.json",
            "--plate", job["_paths"].get("plate") or "",
            "--output", output,
            "--bisenet", PROJECT_ROOT / "models" / "facefusion" / "bisenet_resnet_34.onnx",
            "--insightface-root", local.liveportrait_repo / "pretrained_weights" / "insightface",
            "--scale-bias", str(float(comp.get("scale_bias", 1.0))),
            "--x-offset", str(float(comp.get("x_offset_px", 0.0))),
            "--y-offset", str(float(comp.get("y_offset_px", 0.0))),
            "--transform-mode", str(comp.get("transform_mode", "eyes")),
            *( ["--neck-pivot-enabled"] if comp.get("neck_pivot_enabled") else [] ),
            "--neck-pivot-smooth-window", str(int(comp.get("neck_pivot_smooth_window", 7))),
            "--neck-pivot-max-gap", str(int(comp.get("neck_pivot_max_gap", 5))),
            "--attachment-offset-x", str(float(comp.get("attachment_offset_x", 0.0))),
            "--attachment-offset-y", str(float(comp.get("attachment_offset_y", 0.0))),
            "--external-rotation-gain", str(float(comp.get("external_rotation_gain", 0.0))),
            "--max-attachment-drift-px", str(float(comp.get("max_attachment_drift_px", 3.0))),
            *( ["--freeze-head-motion"] if comp.get("freeze_head_motion") else [] ),
            "--freeze-reference-frames", str(int(comp.get("freeze_reference_frames", 30))),
            "--filter-mode", str(comp.get("filter_mode", "offline")),
            "--hampel-window", str(int(comp.get("hampel_window", 7))),
            "--filter-window", str(int(comp.get("filter_window", 11))),
            "--scale-mode", str(comp.get("scale_mode", "smooth")),
            "--angle-window", str(int(comp.get("angle_window", 0))),
            "--transform-window", str(int(comp.get("transform_window", 9))),
            "--rot-smooth", str(float(comp.get("rotation_smooth", 0.8))),
            "--trans-smooth", str(float(comp.get("translation_smooth", 0.8))),
            "--alpha-mode", str(comp.get("alpha_mode", "inner")),
            "--alpha-feather-px", str(float(comp.get("alpha_feather_px", 6))),
            "--alpha-erode-px", str(int(comp.get("alpha_erode_px", 4))),
            "--b-mask-erode-px", str(int(comp.get("b_mask_erode_px", 0))),
            "--head-side-feather-px", str(float(comp.get("head_side_feather_px", 4))),
            "--jaw-feather-px", str(float(comp.get("jaw_feather_px", 8))),
            "--jaw-start-ratio", str(float(comp.get("jaw_start_ratio", 0.68))),
            "--jaw-full-ratio", str(float(comp.get("jaw_full_ratio", 0.82))),
            "--jaw-color-strength", str(float(comp.get("jaw_color_strength", 0.0))),
            *( ["--neck-collar-enabled"] if comp.get("neck_collar_enabled") else [] ),
            "--neck-collar-ratio", str(float(comp.get("neck_collar_ratio", 0.12))),
            "--neck-collar-soft-px", str(float(comp.get("neck_collar_soft_px", 14))),
            "--neck-color-strength", str(float(comp.get("neck_color_strength", 0.0))),
            *( ["--a-neck-preserve-enabled"] if comp.get("a_neck_preserve_enabled") else [] ),
            "--necks-dir", seg_dir / "necks",
            "--a-neck-upward-px", str(int(comp.get("a_neck_upward_px", 3))),
            *( ["--jaw-underlay-enabled"] if comp.get("jaw_underlay_enabled") else [] ),
            "--jaw-underlay-px", str(int(comp.get("jaw_underlay_px", 10))),
            "--neck-taper-height-px", str(int(comp.get("neck_taper_height_px", 16))),
            "--neck-taper-side-px", str(int(comp.get("neck_taper_side_px", 2))),
            "--junction-bridge-max-gap-px", str(int(comp.get("junction_bridge_max_gap_px", 6))),
            *( ["--raw-skins-dir", seg_dir / "raw_skins", "--raw-necks-dir", seg_dir / "raw_necks"]
               if comp.get("skin_bridge_enabled") else [] ),
            "--skin-bridge-max-gap-px", str(int(comp.get("skin_bridge_max_gap_px", 14))),
            *( ["--skin-bridge-no-cap"] if comp.get("skin_bridge_no_cap") else [] ),
            "--wall-max-texture", str(float(comp.get("wall_max_texture", 0.0))),
            "--jaw-gradient-strength", str(float(comp.get("jaw_gradient_strength", 0.0))),
            "--jaw-gradient-band-px", str(int(comp.get("jaw_gradient_band_px", 28))),
            "--fill-mode", str(comp.get("fill_mode", "wall_residual")),
            "--ring-width-px", str(int(comp.get("ring_width_px", 30))),
            "--wall-delta-e", str(float(comp.get("wall_delta_e", 10.0))),
            "--fill-outer-feather-px", str(int(comp.get("fill_outer_feather_px", 0))),
            "--neck-keep-ratio", str(float(comp.get("neck_keep_ratio", 0.05))),
            "--start-frame", str(int(comp.get("start_frame", 0))),
            "--max-frames", str(int(comp.get("max_frames", 0))),
            "--plate-expand-px", str(int(comp.get("plate_expand_px", 10))),
            "--mask-union", str(comp.get("mask_union", "motion_safe")),
            "--safe-margin-px", str(int(comp.get("safe_margin_px", 8))),
            "--head-ema", str(float(comp.get("head_ema", 0.5))),
            "--b-track-gap-mode", str(comp.get("b_track_gap_mode", "hold")),
            "--neck-cut-ratio", str(float(comp.get("neck_cut_y_ratio", 1.35))),
            "--color-strength", str(float(color.get("color_strength", 0.55))),
            "--max-delta-l", str(float(color.get("max_delta_l", 20))),
            "--max-delta-ab", str(float(color.get("max_delta_ab", 12))),
            "--color-ema", str(float(color.get("color_ema", 0.9))),
            "--crf", str(int(job.get("video", {}).get("output_crf", 14))),
            "--debug-dir", dirs["previews"] / comp.get("debug_dir_name", "composite_debug"),
            "--debug-every", "50",
            *( ["--alpha-diagnostic-output", dirs["previews"] / str(comp["alpha_diagnostic_name"])]
               if comp.get("alpha_diagnostic_name") else [] ),
        ],
    )
    _run(command, dirs["logs"] / "composite.log", cwd=PROJECT_ROOT)
    job["_paths"]["composite"] = str(output)


def _make_mask_preview(job: dict, dirs: dict) -> Path | None:
    """分割 mask 红色叠加预览（cv2 生成，mp4v 编码）。"""
    import cv2

    seg_dir = Path(job["_paths"]["segment_dir"])
    meta = json.loads((seg_dir / "meta.json").read_text(encoding="utf-8"))
    total, fps = meta["frames"], meta.get("fps") or 30.0
    cap = cv2.VideoCapture(job["_paths"]["base_video"])
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) // 2
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) // 2
    out_path = dirs["previews"] / "mask_preview.mp4"
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    try:
        for i in range(total):
            ok, frame = cap.read()
            if not ok:
                break
            mask = cv2.imread(str(seg_dir / "masks" / f"mask_{i:06d}.png"), cv2.IMREAD_GRAYSCALE)
            if mask is not None:
                red = np.zeros_like(frame)
                red[..., 2] = 255
                frame = np.where(mask[..., None] > 0, (0.45 * red + 0.55 * frame).astype(np.uint8), frame)
            writer.write(cv2.resize(frame, (width, height)))
    finally:
        cap.release()
        writer.release()
    return out_path


def stage_finalize(job: dict, local, dirs: dict) -> None:
    _require(job, "base_video", "animated_head", "composite")
    video_cfg = job.get("video", {})
    final = dirs["output"] / f"{video_cfg.get('final_name', 'final')}.mp4"
    audio = Path(job["_paths"]["audio"]) if job["_paths"].get("audio") else None
    command = [local.ffmpeg, "-y", "-i", job["_paths"]["composite"]]
    if audio is not None and audio.is_file() and video_cfg.get("keep_original_audio", True):
        command += [
            "-i", audio,
            "-c:v", "libx264", "-crf", str(int(video_cfg.get("output_crf", 14))),
            "-preset", "slow", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest", final,
        ]
    else:
        command += [
            "-c:v", "libx264", "-crf", str(int(video_cfg.get("output_crf", 14))),
            "-preset", "slow", "-pix_fmt", "yuv420p", "-an", final,
        ]
    _run(command, dirs["logs"] / "finalize.log")

    # 并排对比：原片 | LivePortrait 再演 | 成片
    sbs = dirs["previews"] / f"{video_cfg.get('side_by_side_name', 'side_by_side')}.mp4"
    _run(
        [
            local.ffmpeg, "-y",
            "-i", job["_paths"]["base_video"],
            "-i", job["_paths"]["animated_head"],
            "-i", str(final),
            "-filter_complex",
            "[0:v]scale=540:960,setsar=1[a];[1:v]scale=540:960,setsar=1[b];"
            "[2:v]scale=540:960,setsar=1[c];[a][b][c]hstack=3",
            "-r", "30", "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-an", sbs,
        ],
        dirs["logs"] / "side_by_side.log",
    )
    job["_paths"]["final"] = str(final)
    job["_paths"]["side_by_side"] = str(sbs)
    try:
        job["_paths"]["mask_preview"] = str(_make_mask_preview(job, dirs))
    except Exception as exc:  # 预览失败不阻塞交付
        print(f"[WARN] mask 预览生成失败: {exc}")


def write_manifest(job: dict, local, dirs: dict, timings: dict) -> Path:
    seg_dir = Path(job["_paths"].get("segment_dir", "")) if job["_paths"].get("segment_dir") else None
    manifest = {
        "job_id": job["job_id"],
        "profile": local.profile,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "inputs": {
            "source_video": str(job["source_video"]),
            "source_portrait": str(job["source_portrait"]),
            "side_portraits": [str(p) for p in job["side_portraits"]],
        },
        "settings": {
            k: job.get(k, {}) for k in ("liveportrait", "segmentation", "plate", "composite", "color", "video")
        },
        "outputs": {k: v for k, v in job.get("_paths", {}).items() if v},
        "timings_seconds": {k: round(v, 1) for k, v in timings.items()},
    }
    if seg_dir is not None and (seg_dir / "meta.json").is_file():
        seg_meta = json.loads((seg_dir / "meta.json").read_text(encoding="utf-8"))
        manifest["diagnostics"] = {
            "segment": {"frames": seg_meta["frames"], "fail_frames": seg_meta["fail_frames"]},
        }
    composite_diag = dirs["work"] / f"{job.get('composite', {}).get('silent_name', 'composite_silent')}.diag.json"
    if composite_diag.is_file():
        manifest.setdefault("diagnostics", {})["composite"] = json.loads(
            composite_diag.read_text(encoding="utf-8")
        )
    path = dirs["root"] / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="整头替换流水线（LivePortrait 再演 + 回贴合成）")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--job", required=True, type=Path)
    run.add_argument("--config", type=Path)
    run.add_argument("--profile", choices=["office", "home"], default="office")
    run.add_argument("--stage", choices=STAGES, default=None)
    run.add_argument("--force", action="store_true")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        local_path = (
            args.config.resolve()
            if args.config
            else PROJECT_ROOT / "config" / f"local.{args.profile}.yaml"
        )
        try:
            from digital_human.config import load_local_config

            local = load_local_config(local_path)
            job = load_headswap_job(args.job.resolve())
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

        root = local.jobs_root / job["job_id"]
        dirs = {
            "root": root,
            "input": root / "input",
            "work": root / "work",
            "output": root / "output",
            "previews": root / "previews",
            "logs": root / "logs",
        }
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        _ensure_paths(job, dirs)

        handlers = {
            "prepare": stage_prepare,
            "reenact": stage_reenact,
            "segment": stage_segment,
            "plate": stage_plate,
            "composite": stage_composite,
            "finalize": stage_finalize,
        }
        timings: dict[str, float] = {}
        try:
            for stage in STAGES:
                if args.stage and stage != args.stage:
                    continue
                # 显式指定 --stage 时总是执行该阶段；全量运行时产物存在即跳过
                job["_force_current"] = bool(args.force or (args.stage == stage))
                start = time.time()
                handlers[stage](job, local, dirs)
                timings[stage] = time.time() - start
                print(f"[stage {stage}] done in {timings[stage]:.1f}s")
            manifest = write_manifest(job, local, dirs, timings)
            print(manifest)
            print(job["_paths"].get("final", ""))
            return 0
        except (HeadswapError, RuntimeError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
