from pathlib import Path

from digital_human.adapters.latentsync import LatentSyncAdapter
from digital_human.config import JobConfig, MouthROI, load_local_config


def _job(tmp_path: Path) -> JobConfig:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    return JobConfig(
        job_id="latentsync-test",
        consent_confirmed=True,
        local_only=False,
        source_video=source,
        reference_audio=None,
        reference_start_seconds=0,
        reference_duration_seconds=10,
        reference_text="参考文字",
        script="新话术",
        tts={},
        video={"fps": 25, "final_crf": 12},
        lipsync={
            "engine": "latentsync_1_6",
            "inference_steps": 30,
            "guidance_scale": 1.3,
            "seed": 1247,
            "enable_deepcache": False,
        },
        composite={"mode": "native"},
        mouth_roi=MouthROI(0.5, 0.5, 0.2, 0.1, 8),
    )


def test_latentsync_command_uses_pinned_512_config(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    local = load_local_config(root / "config" / "local.cloud.yaml")
    command = [
        str(item)
        for item in LatentSyncAdapter(local).build_command(
            video=tmp_path / "base.mp4",
            audio=tmp_path / "audio.wav",
            output=tmp_path / "result.mp4",
            job=_job(tmp_path),
            work_dir=tmp_path,
        )
    ]
    joined = " ".join(command)
    assert "stage2_512.yaml" in joined
    assert "--inference_steps 30" in joined
    assert "--guidance_scale 1.3" in joined
    assert "--enable_deepcache" not in command
