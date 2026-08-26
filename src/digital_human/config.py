from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
    latentsync_env: Path
    dots_quality_model: str
    dots_fast_model: str
    musetalk_repo: Path
    latentsync_repo: Path
    latentsync_checkpoint: Path
    jobs_root: Path
    expected_gpu: str
    gpu_id: int
    musetalk_batch_size: int
    use_float16: bool
    tts_profile: str
    primary_lipsync_engine: str
    heygem_base_url: str
    heygem_shared_root: Path
    heygem_timeout_seconds: int
    heygem_poll_interval_seconds: float
    heygem_file_server_port: int
    heygem_container_host: str
    heygem_cleanup_stage: bool


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
    reference_audio: Path | None
    reference_start_seconds: float
    reference_duration_seconds: float
    reference_text: str
    script: str
    tts: dict[str, Any]
    video: dict[str, Any]
    lipsync: dict[str, Any]
    composite: dict[str, Any]
    mouth_roi: MouthROI
    protected_regions: list[dict[str, Any]] = field(default_factory=list)


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
        heygem = data.get("heygem") or {}
        config = LocalConfig(
            profile=str(data["profile"]),
            conda=str(executables["conda"]),
            ffmpeg=str(executables["ffmpeg"]),
            ffprobe=str(executables["ffprobe"]),
            orchestrator_env=_resolve(str(envs["orchestrator_prefix"]), base),
            dots_env=_resolve(str(envs["dots_tts_prefix"]), base),
            musetalk_env=_resolve(str(envs["musetalk_prefix"]), base),
            latentsync_env=_resolve(str(envs["latentsync_prefix"]), base),
            dots_quality_model=str(_resolve(str(models["dots_quality"]), base)),
            dots_fast_model=str(_resolve(str(models["dots_fast"]), base)),
            musetalk_repo=_resolve(str(paths["musetalk_repo"]), base),
            latentsync_repo=_resolve(str(paths["latentsync_repo"]), base),
            latentsync_checkpoint=_resolve(str(models["latentsync_1_6"]), base),
            jobs_root=_resolve(str(paths["jobs_root"]), base),
            expected_gpu=str(runtime["expected_gpu"]),
            gpu_id=int(runtime.get("gpu_id", 0)),
            musetalk_batch_size=int(runtime["musetalk_batch_size"]),
            use_float16=bool(runtime.get("use_float16", True)),
            tts_profile=str(runtime.get("tts_profile", "quality")),
            primary_lipsync_engine=str(
                runtime.get("primary_lipsync_engine", "musetalk_1_5")
            ),
            heygem_base_url=str(
                heygem.get("base_url", "http://127.0.0.1:8383/easy")
            ),
            heygem_shared_root=_resolve(
                str(heygem.get("shared_root", "../runtime/heygem/data/face2face")),
                base,
            ),
            heygem_timeout_seconds=int(heygem.get("timeout_seconds", 7200)),
            heygem_poll_interval_seconds=float(
                heygem.get("poll_interval_seconds", 2.0)
            ),
            heygem_file_server_port=int(heygem.get("file_server_port", 8123)),
            heygem_container_host=str(
                heygem.get("container_host", "host.docker.internal")
            ),
            heygem_cleanup_stage=bool(heygem.get("cleanup_stage", False)),
        )
        validate_local_config(config)
        return config
    except KeyError as exc:
        raise ConfigurationError(f"local 配置缺少字段: {exc}") from exc


def validate_local_config(config: LocalConfig) -> None:
    if config.profile not in {"office", "home", "cloud"}:
        raise ConfigurationError("profile 只能是 office、home 或 cloud")
    if config.musetalk_batch_size < 1:
        raise ConfigurationError("musetalk_batch_size 必须大于 0")
    if config.tts_profile not in {"quality", "fast"}:
        raise ConfigurationError("tts_profile 只能是 quality 或 fast")
    if config.primary_lipsync_engine not in {
        "musetalk_1_5",
        "latentsync_1_6",
        "heygem_local",
    }:
        raise ConfigurationError(
            "primary_lipsync_engine 只能是 musetalk_1_5、latentsync_1_6 或 heygem_local"
        )
    parsed = urlparse(config.heygem_base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ConfigurationError(
            "heygem.base_url 只允许 http://127.0.0.1 或 http://localhost（不得暴露局域网）"
        )
    if config.heygem_timeout_seconds < 60:
        raise ConfigurationError("heygem.timeout_seconds 不能小于 60 秒")
    if not 1.0 <= config.heygem_poll_interval_seconds <= 60.0:
        raise ConfigurationError("heygem.poll_interval_seconds 必须在 1～60 秒之间")
    if not 1 <= config.heygem_file_server_port <= 65535:
        raise ConfigurationError("heygem.file_server_port 必须在 1～65535 之间")
    if not config.heygem_container_host.strip():
        raise ConfigurationError("heygem.container_host 不能为空")
    local_paths = (
        config.orchestrator_env,
        config.dots_env,
        config.musetalk_env,
        config.latentsync_env,
        config.musetalk_repo,
        config.latentsync_repo,
        config.latentsync_checkpoint,
        config.jobs_root,
        config.heygem_shared_root,
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
            raise ConfigurationError(
                f"本地模型路径必须位于 {PROJECT_STORAGE_ROOT} 下: {model}"
            )


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
        job = JobConfig(
            job_id=str(data["job_id"]),
            consent_confirmed=bool(data.get("consent_confirmed", False)),
            local_only=bool(data.get("local_only", True)),
            source_video=_resolve(str(data["source_video"]), base),
            reference_audio=(
                _resolve(str(reference_value), base) if reference_value else None
            ),
            reference_start_seconds=float(data.get("reference_start_seconds", 0.0)),
            reference_duration_seconds=float(data.get("reference_duration_seconds", 15.0)),
            reference_text=str(data["reference_text"]).strip(),
            script=str(data["script"]).strip(),
            tts=dict(data["tts"]),
            video=dict(data["video"]),
            lipsync=dict(data["lipsync"]),
            composite=dict(data.get("composite", {"mode": "dynamic_texture"})),
            mouth_roi=roi,
            protected_regions=list(data.get("protected_regions", [])),
        )
    except KeyError as exc:
        raise ConfigurationError(f"任务配置缺少字段: {exc}") from exc
    validate_job(job)
    return job


def validate_job(job: JobConfig) -> None:
    if not job.consent_confirmed:
        raise ConfigurationError("未确认人物肖像和声音授权，任务拒绝运行")
    if not job.source_video.is_file():
        raise ConfigurationError(f"源视频不存在: {job.source_video}")
    if job.reference_audio is not None and not job.reference_audio.is_file():
        raise ConfigurationError(f"参考音频不存在: {job.reference_audio}")
    if not job.reference_text:
        raise ConfigurationError("reference_text 不能为空")
    if not job.script:
        raise ConfigurationError("script 不能为空")
    if not job.job_id or any(ch in job.job_id for ch in "\\/:*?\"<>|"):
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
    composite = job.composite
    mode = str(composite.get("mode", "dynamic_texture"))
    if mode not in {"native", "dynamic_texture", "fixed_roi", "restore_protected_regions"}:
        raise ConfigurationError(
            "composite.mode 只能是 native、dynamic_texture、fixed_roi 或 "
            "restore_protected_regions"
        )
    engine = str(job.lipsync.get("engine", "musetalk_1_5"))
    if engine not in {"musetalk_1_5", "latentsync_1_6", "heygem_local"}:
        raise ConfigurationError(
            "lipsync.engine 只能是 musetalk_1_5、latentsync_1_6 或 heygem_local"
        )
    if mode == "restore_protected_regions":
        if engine != "heygem_local":
            raise ConfigurationError(
                "restore_protected_regions 只能与 lipsync.engine=heygem_local 搭配"
            )
        if not job.protected_regions:
            raise ConfigurationError(
                "composite.mode=restore_protected_regions 需要至少一个 protected_regions"
            )
    _validate_protected_regions(job.protected_regions)
    if not 0.0 <= float(composite.get("texture_strength", 0.55)) <= 1.5:
        raise ConfigurationError("composite.texture_strength 必须在 0～1.5 之间")
    if float(composite.get("detail_sigma", 1.2)) <= 0:
        raise ConfigurationError("composite.detail_sigma 必须大于 0")
    if not 0.0 <= float(composite.get("temporal_ema", 0.8)) < 1.0:
        raise ConfigurationError("composite.temporal_ema 必须在 [0, 1) 范围")
    if int(composite.get("mask_feather_pixels", 6)) < 0:
        raise ConfigurationError("composite.mask_feather_pixels 不能为负数")
    tone_ema = float(composite.get("face_tone_ema", 0.9))
    if not 0.0 <= tone_ema < 1.0:
        raise ConfigurationError("composite.face_tone_ema 必须在 [0,1) 范围（0 表示关闭）")
    motion_frames = int(composite.get("face_motion_smooth_frames", 15))
    if not 0 <= motion_frames <= 121:
        raise ConfigurationError(
            "composite.face_motion_smooth_frames 必须在 0～121 帧（0/1 表示关闭）"
        )
    max_shift = float(composite.get("face_motion_max_shift", 3.0))
    if not 0.0 < max_shift <= 20.0:
        raise ConfigurationError("composite.face_motion_max_shift 必须在 (0,20] 像素")
    if engine == "latentsync_1_6":
        steps = int(job.lipsync.get("inference_steps", 30))
        guidance = float(job.lipsync.get("guidance_scale", 1.3))
        if not 20 <= steps <= 50:
            raise ConfigurationError("LatentSync inference_steps 必须在 20～50 之间")
        if not 1.0 <= guidance <= 3.0:
            raise ConfigurationError("LatentSync guidance_scale 必须在 1.0～3.0 之间")


def _validate_protected_regions(regions: list[dict[str, Any]]) -> None:
    """固定多边形保护区校验：名称唯一、坐标归一化、面积非零。"""
    names = [str(region.get("name", "")) for region in regions]
    if any(not name for name in names):
        raise ConfigurationError("protected_regions 名称不能为空")
    if len(set(names)) != len(names):
        raise ConfigurationError("protected_regions 名称必须唯一")
    for region in regions:
        name = str(region.get("name"))
        if str(region.get("type", "polygon")) != "polygon":
            raise ConfigurationError(f"保护区 {name} 首版只支持 polygon 类型")
        points = region.get("points") or []
        if len(points) < 3:
            raise ConfigurationError(f"保护区 {name} 至少需要三个点")
        xs: list[float] = []
        ys: list[float] = []
        for point in points:
            x, y = float(point[0]), float(point[1])
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise ConfigurationError(f"保护区 {name} 坐标必须在 [0, 1] 范围")
            xs.append(x)
            ys.append(y)
        area = abs(
            sum(
                xs[i] * ys[(i + 1) % len(xs)] - xs[(i + 1) % len(xs)] * ys[i]
                for i in range(len(xs))
            )
        ) / 2
        if area <= 1e-9:
            raise ConfigurationError(f"保护区 {name} 面积必须大于零")
        if int(region.get("margin_pixels", 0)) < 0:
            raise ConfigurationError(f"保护区 {name} margin_pixels 不能为负数")
