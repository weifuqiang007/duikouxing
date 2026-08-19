from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigurationError(ValueError):
    """Raised when a local or job configuration is invalid."""


# 项目根目录（src/digital_human/config.py 的上两级），环境、模型、任务必须位于此目录下。
PROJECT_STORAGE_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class LocalConfig:
    profile: str
    conda: str
    ffmpeg: str
    ffprobe: str
    orchestrator_env: Path
    dots_env: Path
    musetalk_env: Path
    liveportrait_env: Path
    dots_quality_model: str
    dots_fast_model: str
    musetalk_repo: Path
    liveportrait_repo: Path
    jobs_root: Path
    expected_gpu: str
    gpu_id: int
    musetalk_batch_size: int
    use_float16: bool
    tts_profile: str


@dataclass(frozen=True)
class MouthROI:
    center_x: float
    center_y: float
    width: float
    height: float
    feather_pixels: int


@dataclass(frozen=True)
class JobConfig:
    job_id: str
    consent_confirmed: bool
    local_only: bool
    source_video: Path
    driving_video: Path | None
    reference_audio: Path | None
    reference_start_seconds: float
    reference_duration_seconds: float
    reference_text: str
    script: str
    tts: dict[str, Any]
    video: dict[str, Any]
    lipsync: dict[str, Any]
    performance_drive: dict[str, Any]
    backend: str
    composite: dict[str, Any]
    mouth_roi: MouthROI


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigurationError(f"配置文件不存在: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigurationError(f"配置文件顶层必须是对象: {path}")
    return data


def _resolve(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def load_local_config(path: Path) -> LocalConfig:
    data = _read_yaml(path)
    try:
        envs = data["environments"]
        executables = data["executables"]
        models = data["models"]
        paths = data["paths"]
        runtime = data["runtime"]
        base = path.parent
        config = LocalConfig(
            profile=str(data["profile"]),
            conda=str(executables["conda"]),
            ffmpeg=str(executables["ffmpeg"]),
            ffprobe=str(executables["ffprobe"]),
            orchestrator_env=_resolve(str(envs["orchestrator_prefix"]), base),
            dots_env=_resolve(str(envs["dots_tts_prefix"]), base),
            musetalk_env=_resolve(str(envs["musetalk_prefix"]), base),
            liveportrait_env=_resolve(str(envs["liveportrait_prefix"]), base),
            dots_quality_model=str(_resolve(str(models["dots_quality"]), base)),
            dots_fast_model=str(_resolve(str(models["dots_fast"]), base)),
            musetalk_repo=_resolve(str(paths["musetalk_repo"]), base),
            liveportrait_repo=_resolve(str(paths["liveportrait_repo"]), base),
            jobs_root=_resolve(str(paths["jobs_root"]), base),
            expected_gpu=str(runtime["expected_gpu"]),
            gpu_id=int(runtime.get("gpu_id", 0)),
            musetalk_batch_size=int(runtime["musetalk_batch_size"]),
            use_float16=bool(runtime.get("use_float16", True)),
            tts_profile=str(runtime.get("tts_profile", "quality")),
        )
        validate_local_config(config)
        return config
    except KeyError as exc:
        raise ConfigurationError(f"local 配置缺少字段: {exc}") from exc


def validate_local_config(config: LocalConfig) -> None:
    if config.profile not in {"office", "home"}:
        raise ConfigurationError("profile 只能是 office 或 home")
    if config.musetalk_batch_size < 1:
        raise ConfigurationError("musetalk_batch_size 必须大于 0")
    if config.tts_profile not in {"quality", "fast"}:
        raise ConfigurationError("tts_profile 只能是 quality 或 fast")
    local_paths = (
        config.orchestrator_env,
        config.dots_env,
        config.musetalk_env,
        config.liveportrait_env,
        config.musetalk_repo,
        config.liveportrait_repo,
        config.jobs_root,
    )
    for path in local_paths:
        if not path.is_relative_to(PROJECT_STORAGE_ROOT):
            raise ConfigurationError(
                f"环境、模型和任务路径必须位于 {PROJECT_STORAGE_ROOT} 下: {path}"
            )
    for model in (config.dots_quality_model, config.dots_fast_model):
        model_path = Path(model)
        if model_path.is_absolute() and not model_path.resolve().is_relative_to(
            PROJECT_STORAGE_ROOT
        ):
            raise ConfigurationError(f"本地模型路径必须位于 {PROJECT_STORAGE_ROOT} 下: {model}")


def load_job_config(path: Path) -> JobConfig:
    data = _read_yaml(path)
    base = path.parent
    try:
        roi_data = data["mouth_roi"]
        roi = MouthROI(
            center_x=float(roi_data["center_x"]),
            center_y=float(roi_data["center_y"]),
            width=float(roi_data["width"]),
            height=float(roi_data["height"]),
            feather_pixels=int(roi_data["feather_pixels"]),
        )
        reference_value = data.get("reference_audio")
        driving_value = data.get("driving_video")
        job = JobConfig(
            job_id=str(data["job_id"]),
            consent_confirmed=bool(data.get("consent_confirmed", False)),
            local_only=bool(data.get("local_only", True)),
            source_video=_resolve(str(data["source_video"]), base),
            driving_video=(_resolve(str(driving_value), base) if driving_value else None),
            reference_audio=(_resolve(str(reference_value), base) if reference_value else None),
            reference_start_seconds=float(data.get("reference_start_seconds", 0.0)),
            reference_duration_seconds=float(data.get("reference_duration_seconds", 15.0)),
            reference_text=str(data["reference_text"]).strip(),
            script=str(data["script"]).strip(),
            tts=dict(data["tts"]),
            video=dict(data["video"]),
            lipsync=dict(data["lipsync"]),
            performance_drive=dict(data.get("performance_drive", {})),
            backend=str(data.get("backend", "musetalk")),
            composite=dict(data.get("composite", {"mode": "dynamic_texture"})),
            mouth_roi=roi,
        )
    except KeyError as exc:
        raise ConfigurationError(f"任务配置缺少字段: {exc}") from exc
    validate_job(job)
    return job


def validate_job(job: JobConfig) -> None:
    if not job.consent_confirmed:
        raise ConfigurationError("未确认人物肖像和声音授权，任务拒绝运行")
    if not job.local_only:
        raise ConfigurationError("MVP 只允许 local_only=true")
    if not job.source_video.is_file():
        raise ConfigurationError(f"源视频不存在: {job.source_video}")
    if job.reference_audio is not None and not job.reference_audio.is_file():
        raise ConfigurationError(f"参考音频不存在: {job.reference_audio}")
    if job.driving_video is not None and not job.driving_video.is_file():
        raise ConfigurationError(f"真人驱动视频不存在: {job.driving_video}")
    if job.backend not in {"musetalk", "liveportrait"}:
        raise ConfigurationError("backend 只能是 musetalk 或 liveportrait")
    if not job.reference_text:
        raise ConfigurationError("reference_text 不能为空")
    if not job.script:
        raise ConfigurationError("script 不能为空")
    if not job.job_id or any(ch in job.job_id for ch in '\\/:*?"<>|'):
        raise ConfigurationError("job_id 为空或包含 Windows 非法文件名字符")
    roi = job.mouth_roi
    for name, value in (
        ("center_x", roi.center_x),
        ("center_y", roi.center_y),
        ("width", roi.width),
        ("height", roi.height),
    ):
        if not 0.0 < value <= 1.0:
            raise ConfigurationError(f"mouth_roi.{name} 必须在 (0, 1] 范围")
    if roi.width / 2 > min(roi.center_x, 1 - roi.center_x):
        raise ConfigurationError("嘴部 ROI 横向超出画面")
    if roi.height / 2 > min(roi.center_y, 1 - roi.center_y):
        raise ConfigurationError("嘴部 ROI 纵向超出画面")
    if roi.feather_pixels < 0:
        raise ConfigurationError("feather_pixels 不能为负数")
    performance = job.performance_drive
    region = str(performance.get("animation_region", "exp"))
    if region not in {"exp", "lip"}:
        raise ConfigurationError("performance_drive.animation_region 只能是 exp 或 lip")
    multiplier = float(performance.get("driving_multiplier", 0.85))
    if not 0.0 < multiplier <= 2.0:
        raise ConfigurationError("performance_drive.driving_multiplier 必须在 (0, 2] 范围")
    tolerance = float(performance.get("duration_tolerance_ratio", 0.12))
    if not 0.0 <= tolerance <= 0.5:
        raise ConfigurationError("performance_drive.duration_tolerance_ratio 必须在 [0, 0.5] 范围")
    sync_envelope = float(performance.get("min_sync_envelope", 0.3))
    if not 0.0 <= sync_envelope <= 1.0:
        raise ConfigurationError(
            "performance_drive.min_sync_envelope 必须在 [0, 1] 范围；0 表示关闭同步预检"
        )
    composite = job.composite
    mode = str(composite.get("mode", "dynamic_texture"))
    if mode not in {"dynamic_texture", "fixed_roi"}:
        raise ConfigurationError("composite.mode 只能是 dynamic_texture 或 fixed_roi")
    if not 0.0 <= float(composite.get("texture_strength", 0.55)) <= 1.5:
        raise ConfigurationError("composite.texture_strength 必须在 0～1.5 之间")
    if float(composite.get("detail_sigma", 1.2)) <= 0:
        raise ConfigurationError("composite.detail_sigma 必须大于 0")
    if not 0.0 <= float(composite.get("temporal_ema", 0.8)) < 1.0:
        raise ConfigurationError("composite.temporal_ema 必须在 [0, 1) 范围")
    if int(composite.get("mask_feather_pixels", 6)) < 0:
        raise ConfigurationError("composite.mask_feather_pixels 不能为负数")
