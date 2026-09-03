from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from digital_human.adapters.latentsync import LATENTSYNC_COMMIT
from digital_human.config import ConfigurationError, MouthROI, load_local_config
from digital_human.process import conda_run, run_command

PROJECT_ROOT = Path(__file__).resolve().parents[3]


# ── ① 环境与路径 ──────────────────────────────────────────────────────────


def configure_project_local_storage() -> None:
    """创建 .cache / .tmp 目录，将 HF/Torch/pip 缓存重定向到项目内。"""
    cache_root = PROJECT_ROOT / ".cache"
    temp_root = PROJECT_ROOT / ".tmp"
    for directory in (
        cache_root,
        cache_root / "huggingface" / "hub",
        cache_root / "torch",
        cache_root / "pip",
        temp_root,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    # Always override user-level defaults so model files and download caches never fall on C:.
    os.environ["HF_HOME"] = str(cache_root / "huggingface")
    os.environ["HF_HUB_CACHE"] = str(cache_root / "huggingface" / "hub")
    os.environ["TORCH_HOME"] = str(cache_root / "torch")
    os.environ["PIP_CACHE_DIR"] = str(cache_root / "pip")
    os.environ["XDG_CACHE_HOME"] = str(cache_root)
    os.environ["TEMP"] = str(temp_root)
    os.environ["TMP"] = str(temp_root)


def profile_path(profile: str) -> Path:
    """根据 profile 名返回 local 配置文件路径。"""
    if profile not in {"office", "home", "cloud"}:
        raise ConfigurationError("profile 只能是 office、home 或 cloud")
    return PROJECT_ROOT / "config" / f"local.{profile}.yaml"


# 返回path（配置文件）的绝对路径
def resolve_local_config_path(config: Path | None, profile: str) -> Path:
    """优先使用 --config 指定路径，否则按 profile 推导。
    返回的是path的绝对路径。
    """
    return config.resolve() if config else profile_path(profile)


# ── ② 健康检查 ────────────────────────────────────────────────────────────


def run_doctor(local_path: Path) -> int:
    """检查环境依赖、GPU、权重文件和仓库 commit，返回 0=通过 / 1=失败。"""
    local = load_local_config(local_path)
    checks = [
        ("FFmpeg", [local.ffmpeg, "-version"]),
        ("FFprobe", [local.ffprobe, "-version"]),
        (
            "dots.tts CUDA",
            conda_run(
                local.conda,
                local.dots_env,
                [
                    "python",
                    "-c",
                    "import torch; assert torch.cuda.is_available(); print(torch.__version__)",
                ],
            ),
        ),
    ]
    if local.primary_lipsync_engine == "latentsync_1_6":
        checks.append(
            (
                "LatentSync CUDA",
                conda_run(
                    local.conda,
                    local.latentsync_env,
                    [
                        "python",
                        "-c",
                        "import torch; assert torch.cuda.is_available(); "
                        "print(torch.__version__, torch.cuda.get_device_name(0))",
                    ],
                ),
            )
        )
    else:
        checks.append(
            (
                "MuseTalk CUDA",
                conda_run(
                    local.conda,
                    local.musetalk_env,
                    [
                        "python",
                        "-c",
                        "import torch; assert torch.cuda.is_available(); print(torch.__version__)",
                    ],
                ),
            )
        )
    failed = False
    for name, command in checks:
        try:
            result = run_command(command)
            print(f"[OK] {name}: {result.stdout.splitlines()[-1] if result.stdout else ''}")
        except Exception as exc:  # doctor must report all checks
            failed = True
            print(f"[FAIL] {name}: {exc}")
    try:
        gpu_names = run_command(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"]
        ).stdout
        if local.expected_gpu.lower() in gpu_names.lower():
            print(f"[OK] GPU profile {local.profile}: {gpu_names.strip()}")
        else:
            failed = True
            print(
                f"[FAIL] 当前 GPU 与 {local.profile} 配置不符: "
                f"预期包含 {local.expected_gpu!r}，实际 {gpu_names.strip()!r}"
            )
    except Exception as exc:
        failed = True
        print(f"[FAIL] 无法读取 GPU 型号: {exc}")
    if local.primary_lipsync_engine == "latentsync_1_6":
        required = [
            local.latentsync_checkpoint,
            local.latentsync_repo / "checkpoints" / "whisper" / "tiny.pt",
            local.latentsync_repo / "configs" / "unet" / "stage2_512.yaml",
            local.latentsync_repo
            / "checkpoints"
            / "auxiliary"
            / "models"
            / "buffalo_l"
            / "det_10g.onnx",
            local.latentsync_repo
            / "checkpoints"
            / "auxiliary"
            / "models"
            / "buffalo_l"
            / "2d106det.onnx",
        ]
    else:
        required = [
            local.musetalk_repo / "models" / "musetalkV15" / "unet.pth",
            local.musetalk_repo / "models" / "musetalkV15" / "musetalk.json",
            local.musetalk_repo / "models" / "syncnet" / "latentsync_syncnet.pt",
            local.musetalk_repo / "models" / "dwpose" / "dw-ll_ucoco_384.pth",
            local.musetalk_repo / "models" / "sd-vae" / "config.json",
            local.musetalk_repo / "models" / "sd-vae" / "diffusion_pytorch_model.bin",
            local.musetalk_repo / "models" / "whisper" / "config.json",
            local.musetalk_repo / "models" / "whisper" / "pytorch_model.bin",
            local.musetalk_repo / "models" / "whisper" / "preprocessor_config.json",
            local.musetalk_repo / "models" / "face-parse-bisent" / "79999_iter.pth",
            local.musetalk_repo
            / "models"
            / "face-parse-bisent"
            / "resnet18-5c106cde.pth",
        ]
    for path in required:
        if path.is_file():
            print(f"[OK] 权重: {path}")
        else:
            failed = True
            print(f"[FAIL] 缺少权重: {path}")
    for name, model_value in (
        ("dots.tts SOAR", local.dots_quality_model),
        ("dots.tts MF", local.dots_fast_model),
    ):
        model_path = Path(model_value)
        if model_path.is_absolute():
            if model_path.is_dir():
                print(f"[OK] {name}: {model_path}")
            else:
                failed = True
                print(f"[FAIL] {name} 本地目录不存在: {model_path}")
        else:
            print(f"[WARN] {name} 使用远程 ID，不能保证断网运行: {model_value}")
    if local.primary_lipsync_engine == "latentsync_1_6":
        repo = local.latentsync_repo
        expected_commit = LATENTSYNC_COMMIT
        repo_name = "LatentSync"
    else:
        repo = local.musetalk_repo
        expected_commit = "0a89dec45a0192b824e3cf4daf96c239440c5ed8"
        repo_name = "MuseTalk"
    try:
        actual_commit = run_command(
            ["git", "-C", repo, "rev-parse", "HEAD"]
        ).stdout.strip()
        if actual_commit == expected_commit:
            print(f"[OK] {repo_name} commit: {actual_commit}")
        else:
            failed = True
            print(
                f"[FAIL] {repo_name} commit 不匹配: {actual_commit}，预期 {expected_commit}"
            )
    except Exception as exc:
        failed = True
        print(f"[FAIL] 无法检查 {repo_name} commit: {exc}")
    return 1 if failed else 0


# ── ③ YAML 写回 ───────────────────────────────────────────────────────────


def write_back_mouth_roi(path: Path, roi: MouthROI) -> None:
    """只替换 mouth_roi 块里的数值行，保留文件中所有注释和其他内容。"""
    text = path.read_text(encoding="utf-8")
    for key, value in (
        ("center_x", roi.center_x),
        ("center_y", roi.center_y),
        ("width", roi.width),
        ("height", roi.height),
    ):
        text, replaced = re.subn(
            rf"(?m)^(\s*{key}:\s*)[0-9.]+", rf"\g<1>{value}", text, count=1
        )
        if replaced != 1:
            raise ConfigurationError(f"任务配置中找不到 mouth_roi.{key} 字段: {path}")
    path.write_text(text, encoding="utf-8")


def write_back_id_card_corners(path: Path, corners: list) -> None:
    """将证件四角坐标写回 YAML 配置文件。"""
    text = path.read_text(encoding="utf-8")
    lines = []
    for x, y in corners:
        lines.append(f"      - [{x:.4f}, {y:.4f}]")
    # Replace between 'corners:' and the next non-item line
    pattern = r"(corners:\n)(  - \[.*?\]\n)+(  \S)"
    if not re.search(pattern, text):
        raise ConfigurationError(f"id_card_replacement.corners not found: {path}")
    block = "corners:\n" + "\n".join(lines) + "\n"
    text = re.sub(pattern, r"\g<1>" + block.rstrip() + "\n\3", text)
    path.write_text(text, encoding="utf-8")


def write_back_protect_polygon(path: Path, name: str, points: list) -> None:
    """将手指遮挡保护区多边形写回 YAML 配置文件。"""
    text = path.read_text(encoding="utf-8")
    pt_lines = f"    - name: {name}\n      points:\n"
    for x, y in points:
        pt_lines += f"        - [{x:.4f}, {y:.4f}]\n"
    # Check if polygon with this name already exists
    pat = rf"(    - name: {re.escape(name)}\n      points:\n)(        - \[.*?\]\n)+"
    if re.search(pat, text):
        text = re.sub(pat, pt_lines, text, count=1)
    else:
        insert_at = "  color_match:"
        if insert_at in text:
            text = text.replace(insert_at, pt_lines + insert_at)
        else:
            text = text.rstrip() + "\n" + pt_lines
    path.write_text(text, encoding="utf-8")


# ── ④ 参数解析 ────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="本地口型数字人流水线")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="检查环境和权重")
    doctor.add_argument("--config", type=Path)
    doctor.add_argument(
        "--profile",
        choices=["office", "home", "cloud"],
        default=os.environ.get("DIGITAL_HUMAN_PROFILE", "office"),
    )
    preview = sub.add_parser("preview-roi", help="生成嘴部 ROI 首帧预览")
    preview.add_argument("--job", type=Path, required=True)
    preview.add_argument("--output", type=Path, default=Path("roi-preview.jpg"))
    annotate = sub.add_parser(
        "annotate-roi", help="交互式拖拽标注嘴部 ROI 并写回任务配置"
    )
    annotate.add_argument("--job", type=Path, required=True)
    annotate.add_argument(
        "--at-seconds", type=float, default=0.0, help="抓取该时间点的视频帧（默认首帧）"
    )
    annotate.add_argument(
        "--output", type=Path, default=Path("roi-annotated.jpg"), help="标注结果复核图"
    )
    run = sub.add_parser("run", help="执行完整任务")
    run.add_argument("--job", type=Path, required=True)
    run.add_argument("--config", type=Path)
    run.add_argument(
        "--profile",
        choices=["office", "home", "cloud"],
        default=os.environ.get("DIGITAL_HUMAN_PROFILE", "office"),
    )
    run.add_argument("--force", action="store_true")
    refine = sub.add_parser(
        "refine", help="复用已有 MuseTalk 结果，只重跑纹理合成和音轨封装"
    )
    refine.add_argument("--job", type=Path, required=True)
    refine.add_argument("--config", type=Path)
    refine.add_argument(
        "--profile",
        choices=["office", "home", "cloud"],
        default=os.environ.get("DIGITAL_HUMAN_PROFILE", "office"),
    )
    refine.add_argument("--output", type=Path)
    ann_id = sub.add_parser("annotate-id-card", help="标注证件四角并写回配置")
    ann_id.add_argument("--job", type=Path, required=True)
    ann_id.add_argument("--at-seconds", type=float, default=0.0)
    ann_id.add_argument("--output", type=Path, default=Path("id-card-preview.jpg"))

    ann_prot = sub.add_parser("annotate-id-card-protect", help="标注手指遮挡保护区")
    ann_prot.add_argument("--job", type=Path, required=True)
    ann_prot.add_argument("--at-seconds", type=float, default=0.0)
    ann_prot.add_argument("--name", required=True, help="保护区名称")
    ann_prot.add_argument("--output", type=Path, default=Path("id-card-protect-preview.jpg"))

    rep_id = sub.add_parser("replace-id-card", help="执行证件区域替换")
    rep_id.add_argument("--job", type=Path, required=True)
    return parser
