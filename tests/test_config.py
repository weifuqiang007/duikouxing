from pathlib import Path

import pytest

from digital_human.config import (
    ConfigurationError,
    JobConfig,
    MouthROI,
    load_local_config,
    validate_job,
)


def _job(tmp_path: Path) -> JobConfig:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    return JobConfig(
        job_id="test-job",
        consent_confirmed=True,
        local_only=True,
        source_video=source,
        reference_audio=None,
        reference_start_seconds=0,
        reference_duration_seconds=10,
        reference_text="参考文字",
        script="新话术",
        tts={},
        video={},
        lipsync={},
        mouth_roi=MouthROI(0.5, 0.5, 0.2, 0.1, 10),
    )


def test_consent_is_required(tmp_path: Path) -> None:
    job = _job(tmp_path)
    invalid = JobConfig(**{**job.__dict__, "consent_confirmed": False})
    with pytest.raises(ConfigurationError, match="授权"):
        validate_job(invalid)


def test_valid_job(tmp_path: Path) -> None:
    validate_job(_job(tmp_path))


def test_machine_profiles_are_separate() -> None:
    root = Path(__file__).resolve().parents[1]
    office = load_local_config(root / "config" / "local.office.yaml")
    home = load_local_config(root / "config" / "local.home.yaml")
    assert office.profile == "office"
    assert office.expected_gpu == "RTX 3060"
    assert office.musetalk_batch_size == 2
    assert home.profile == "home"
    assert home.expected_gpu == "RTX 4070"
    assert home.musetalk_batch_size == 4
    assert office.jobs_root != home.jobs_root
