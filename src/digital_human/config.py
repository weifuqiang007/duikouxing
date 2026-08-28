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


@dataclass(frozen=True)
class MouthROI:
    center_x: float
    center_y: float
    width: float
    height: float
    feather_pixels: int




@dataclass(frozen=True)
class PolygonRegion:
    """A named polygon region in normalized coordinates [0, 1]."""
    name: str
    points: list[tuple[float, float]]


@dataclass(frozen=True)
class IdCardConfig:
    """Configuration for ID card / document region replacement."""
    source_image: Path
    input_video: Path
    output_video: Path
    corners: list[tuple[float, float]]
    protect_polygons: list[PolygonRegion]
    feather_pixels: int = 2
    color_match: dict[str, Any] | None = None
    auto_detect_fingers: bool = True
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
    if config.primary_lipsync_engine not in {"musetalk_1_5", "latentsync_1_6"}:
        raise ConfigurationError(
            "primary_lipsync_engine 只能是 musetalk_1_5 或 latentsync_1_6"
        )
    local_paths = (
        config.orchestrator_env,
        config.dots_env,
        config.musetalk_env,
        config.latentsync_env,
        config.musetalk_repo,
        config.latentsync_repo,
        config.latentsync_checkpoint,
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
    if mode not in {"native", "dynamic_texture", "fixed_roi"}:
        raise ConfigurationError(
            "composite.mode 只能是 native、dynamic_texture 或 fixed_roi"
        )
    if not 0.0 <= float(composite.get("texture_strength", 0.55)) <= 1.5:
        raise ConfigurationError("composite.texture_strength 必须在 0～1.5 之间")
    if float(composite.get("detail_sigma", 1.2)) <= 0:
        raise ConfigurationError("composite.detail_sigma 必须大于 0")
    if not 0.0 <= float(composite.get("temporal_ema", 0.8)) < 1.0:
        raise ConfigurationError("composite.temporal_ema 必须在 [0, 1) 范围")
    if int(composite.get("mask_feather_pixels", 6)) < 0:
        raise ConfigurationError("composite.mask_feather_pixels 不能为负数")
    engine = str(job.lipsync.get("engine", "musetalk_1_5"))
    if engine not in {"musetalk_1_5", "latentsync_1_6"}:
        raise ConfigurationError(
            "lipsync.engine 只能是 musetalk_1_5 或 latentsync_1_6"
        )
    if engine == "latentsync_1_6":
        steps = int(job.lipsync.get("inference_steps", 30))
        guidance = float(job.lipsync.get("guidance_scale", 1.3))
        if not 20 <= steps <= 50:
            raise ConfigurationError("LatentSync inference_steps 必须在 20～50 之间")
        if not 1.0 <= guidance <= 3.0:
            raise ConfigurationError("LatentSync guidance_scale 必须在 1.0～3.0 之间")



def load_id_card_config(path: Path) -> IdCardConfig | None:
    """Load id_card_replacement section from job yaml.

    Returns None if section is missing or enabled=false.
    """
    data = _read_yaml(path)
    section = data.get("id_card_replacement")
    if not section or not section.get("enabled", False):
        return None
    base = path.parent
    try:
        cm = section.get("color_match")
        if cm is None:
            cm = {"mode": "lab_local"}
        corners_raw = section["corners"]
        if not isinstance(corners_raw, list) or len(corners_raw) != 4:
            raise ConfigurationError(
                f"id_card_replacement.corners must have exactly 4 points"
            )
        corners = [(float(p[0]), float(p[1])) for p in corners_raw]
        polys_raw = section.get("protect_polygons", [])
        polys = []
        for p in polys_raw:
            polys.append(PolygonRegion(
                name=str(p["name"]),
                points=[(float(pt[0]), float(pt[1])) for pt in p["points"]],
            ))
        config = IdCardConfig(
            source_image=_resolve(str(section["source_image"]), base),
            input_video=_resolve(str(section["input_video"]), base),
            output_video=_resolve(str(section["output_video"]), base),
            corners=corners,
            protect_polygons=polys,
            feather_pixels=int(section.get("feather_pixels", 2)),
            color_match=cm,
            auto_detect_fingers=bool(section.get("auto_detect_fingers", True)),
        )
    except KeyError as exc:
        raise ConfigurationError(
            f"id_card_replacement missing field: {exc}"
        ) from exc
    validate_id_card_config(config)
    return config


def validate_id_card_config(config: IdCardConfig) -> None:
    if not config.source_image.is_file():
        raise ConfigurationError(
            f"id_card_replacement.source_image does not exist: {config.source_image}"
        )
    if not config.input_video.is_file():
        raise ConfigurationError(
            f"id_card_replacement.input_video does not exist: {config.input_video}"
        )
    if not config.output_video.parent.exists():
        try:
            config.output_video.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigurationError(
                f"id_card_replacement.output_video parent cannot be created: {exc}"
            ) from exc
    # Corners: exactly 4, each in [0,1], non-degenerate.
    if len(config.corners) != 4:
        raise ConfigurationError(
            f"id_card_replacement.corners must have exactly 4 points, got {len(config.corners)}"
        )
    for i, (x, y) in enumerate(config.corners):
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ConfigurationError(
                f"id_card_replacement.corners[{i}] = ({x}, {y}) out of [0, 1] range"
            )
    # Shoelace area check.
    n = len(config.corners)
    area = 0.0
    for i in range(n):
        x1, y1 = config.corners[i]
        x2, y2 = config.corners[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    area /= 2.0
    if abs(area) < 1e-6:
        raise ConfigurationError(
            "id_card_replacement.corners form a degenerate quadrilateral"
        )
    # Protect polygons: unique names, each >= 3 points in [0,1].
    names_seen: set[str] = set()
    for poly in config.protect_polygons:
        if poly.name in names_seen:
            raise ConfigurationError(
                f"duplicate protect_polygon name: {poly.name}"
            )
        names_seen.add(poly.name)
        if len(poly.points) < 3:
            raise ConfigurationError(
                f"protect_polygon '{poly.name}' must have at least 3 points, got {len(poly.points)}"
            )
        for j, (x, y) in enumerate(poly.points):
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise ConfigurationError(
                    f"protect_polygon '{poly.name}' point {j} = ({x}, {y}) out of [0, 1] range"
                )
    # feather_pixels in [0, 8].
    if not 0 <= config.feather_pixels <= 8:
        raise ConfigurationError(
            f"id_card_replacement.feather_pixels must be in [0, 8], got {config.feather_pixels}"
        )
    # Color match params.
    cm = config.color_match or {}
    exposure_clip = int(cm.get("exposure_clip", 25))
    chroma_clip = int(cm.get("chroma_clip", 10))
    if not 0 <= exposure_clip <= 60:
        raise ConfigurationError(
            f"color_match.exposure_clip must be in [0, 60], got {exposure_clip}"
        )
    if not 0 <= chroma_clip <= 40:
        raise ConfigurationError(
            f"color_match.chroma_clip must be in [0, 40], got {chroma_clip}"
        )
    # Tracking mode: v1 only supports fixed.
    tracking = cm.get("tracking", {"mode": "fixed"})
    if isinstance(tracking, dict):
        mode = tracking.get("mode", "fixed")
    else:
        mode = "fixed"
    if mode != "fixed":
        raise ConfigurationError(
            f"id_card_replacement tracking.mode must be 'fixed' in v1, got '{mode}'"
        )
