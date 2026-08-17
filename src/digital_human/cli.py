from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .composite import preview_roi
from .config import ConfigurationError, load_job_config, load_local_config
from .pipeline import Pipeline
from .process import CommandError, conda_run, run_command


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _configure_project_local_storage() -> None:
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


def _profile_path(profile: str) -> Path:
    if profile not in {"office", "home"}:
        raise ConfigurationError("profile 只能是 office 或 home")
    return PROJECT_ROOT / "config" / f"local.{profile}.yaml"


def _local_path(args: argparse.Namespace) -> Path:
    return args.config.resolve() if args.config else _profile_path(args.profile)


def _doctor(local_path: Path) -> int:
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
        ),
    ]
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
    expected_commit = "0a89dec45a0192b824e3cf4daf96c239440c5ed8"
    try:
        actual_commit = run_command(
            ["git", "-C", local.musetalk_repo, "rev-parse", "HEAD"]
        ).stdout.strip()
        if actual_commit == expected_commit:
            print(f"[OK] MuseTalk commit: {actual_commit}")
        else:
            failed = True
            print(
                f"[FAIL] MuseTalk commit 不匹配: {actual_commit}，预期 {expected_commit}"
            )
    except Exception as exc:
        failed = True
        print(f"[FAIL] 无法检查 MuseTalk commit: {exc}")
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="本地口型数字人流水线")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="检查环境和权重")
    doctor.add_argument("--config", type=Path)
    doctor.add_argument(
        "--profile",
        choices=["office", "home"],
        default=os.environ.get("DIGITAL_HUMAN_PROFILE", "office"),
    )
    preview = sub.add_parser("preview-roi", help="生成嘴部 ROI 首帧预览")
    preview.add_argument("--job", type=Path, required=True)
    preview.add_argument("--output", type=Path, default=Path("roi-preview.jpg"))
    run = sub.add_parser("run", help="执行完整任务")
    run.add_argument("--job", type=Path, required=True)
    run.add_argument("--config", type=Path)
    run.add_argument(
        "--profile",
        choices=["office", "home"],
        default=os.environ.get("DIGITAL_HUMAN_PROFILE", "office"),
    )
    run.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_project_local_storage()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            return _doctor(_local_path(args))
        job = load_job_config(args.job.resolve())
        if args.command == "preview-roi":
            preview_roi(job.source_video, job.mouth_roi, args.output.resolve())
            print(args.output.resolve())
            return 0
        if args.command == "run":
            local = load_local_config(_local_path(args))
            output = Pipeline(local, job, force=args.force).run()
            print(output)
            return 0
    except (ConfigurationError, CommandError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
