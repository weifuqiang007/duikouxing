"""LivePortrait 再演编排（运行在编排环境，通过 conda 调 liveportrait 环境）。

source = 照片 B 正脸肖像，driving = 视频 A（已烘焙旋转的转正版）。
与 digital_human.adapters.liveportrait 的区别：source 是静态肖像、输出贴回 B 的
画布（flag_pasteback），animation_region 允许 all（整头替换需要 A 的头部姿态）。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from digital_human.process import conda_run, run_command


def _gpu_env(liveportrait_env: Path) -> dict[str, str]:
    """把 pip 安装的 NVIDIA DLL 目录前置到 PATH（对 torch 经典加载路径的兜底）。"""
    env = dict(os.environ)
    site_packages = liveportrait_env / "Lib" / "site-packages"
    candidates = [
        site_packages / "nvidia" / "cudnn" / "bin",
        site_packages / "nvidia" / "cublas" / "bin",
        site_packages / "torch" / "lib",
    ]
    extra = [str(path) for path in candidates if path.is_dir()]
    if extra:
        env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    return env


def run_reenact(
    *,
    conda: str,
    liveportrait_env: Path,
    liveportrait_repo: Path,
    gpu_id: int,
    settings: dict,
    source: Path,
    driving: Path,
    output: Path,
    work_dir: Path,
    log_file: Path,
    use_half_precision: bool = True,
) -> Path:
    if not (liveportrait_repo / "inference.py").is_file():
        raise RuntimeError(f"LivePortrait 官方仓库未安装: {liveportrait_repo}")
    result_dir = work_dir / "liveportrait_results"
    result_dir.mkdir(parents=True, exist_ok=True)
    runner = liveportrait_repo.parents[1] / "scripts" / "liveportrait_runner.py"
    if not runner.is_file():
        raise RuntimeError(f"缺少 LivePortrait 启动包装脚本: {runner}")

    motion_mode = str(settings.get("motion_mode", settings.get("animation_region", "all")))
    if motion_mode not in {"all", "exp", "rotation_exp", "rotation_lip"}:
        raise ValueError(f"不支持的 LivePortrait motion_mode: {motion_mode}")
    official_region = "all" if motion_mode in {"rotation_exp", "rotation_lip"} else str(
        settings.get("animation_region", motion_mode)
    )
    command = conda_run(
        conda,
        liveportrait_env,
        [
            "python", runner, liveportrait_repo / "inference.py",
            "--source", source,
            "--driving", driving,
            "--output_dir", result_dir,
            "--device_id", str(gpu_id),
            "--animation_region", official_region,
            "--driving_option", str(
                settings.get(
                    "driving_option",
                    "pose-friendly" if motion_mode in {"rotation_exp", "rotation_lip"} else "expression-friendly",
                )
            ),
            "--driving_multiplier", str(float(settings.get("driving_multiplier", 1.0))),
            "--driving_smooth_observation_variance",
            str(float(settings.get("smooth_variance", 3e-7))),
            "--source_max_dim", str(int(settings.get("source_max_dim", 1280))),
            "--scale", str(float(settings.get("source_crop_scale", 2.3))),
            "--vx_ratio", str(float(settings.get("source_crop_vx", 0.0))),
            "--vy_ratio", str(float(settings.get("source_crop_vy", -0.125))),
            "--scale_crop_driving_video", str(float(settings.get("driving_crop_scale", 2.2))),
            "--vx_ratio_crop_driving_video", str(float(settings.get("driving_crop_vx", 0.0))),
            "--vy_ratio_crop_driving_video", str(float(settings.get("driving_crop_vy", -0.1))),
            "--audio_priority", "source",
        ],
    )
    if motion_mode in {"rotation_exp", "rotation_lip"}:
        report = work_dir / str(settings.get("motion_report_name", "motion10-poses"))
        command.extend(
            [
                "--headswap-motion-mode", motion_mode,
                "--headswap-pose-gain-pitch", str(float(settings.get("pose_gain_pitch", 0.65))),
                "--headswap-pose-gain-yaw", str(float(settings.get("pose_gain_yaw", 0.75))),
                "--headswap-pose-gain-roll", str(float(settings.get("pose_gain_roll", 0.65))),
                "--headswap-pose-limit-pitch", str(float(settings.get("pose_limit_pitch_deg", 3.0))),
                "--headswap-pose-limit-yaw", str(float(settings.get("pose_limit_yaw_deg", 5.0))),
                "--headswap-pose-limit-roll", str(float(settings.get("pose_limit_roll_deg", 3.0))),
                "--headswap-pose-smooth-window", str(int(settings.get("pose_smooth_window", 7))),
                "--headswap-motion-report", report,
            ]
        )
    command.extend(
        [
            "--flag_relative_motion",
            "--flag_stitching",
            "--flag_pasteback",
            "--flag_do_crop",
            "--flag_crop_driving_video",
        ]
    )
    command.append("--flag_use_half_precision" if use_half_precision else "--no_flag_use_half_precision")

    run_command(command, cwd=liveportrait_repo, log_file=log_file, env=_gpu_env(liveportrait_env))
    produced = result_dir / f"{source.stem}--{driving.stem}.mp4"
    if not produced.is_file():
        candidates = sorted(
            path for path in result_dir.glob("*.mp4") if not path.stem.endswith("_concat")
        )
        if len(candidates) != 1:
            raise RuntimeError(f"LivePortrait 未生成唯一成片，预期 {produced}，实际 {candidates}")
        produced = candidates[0]
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(produced, output)
    return output
