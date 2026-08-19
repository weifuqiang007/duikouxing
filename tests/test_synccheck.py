import json
import math
import random
import wave
from array import array
from pathlib import Path
from types import SimpleNamespace

from digital_human.synccheck import (
    ENVELOPE_WINDOW_SECONDS,
    SAMPLE_RATE,
    SyncReport,
    best_offset_correlation,
    check_driving_sync,
)


def _write_wav(path: Path, samples: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(array("h", samples).tobytes())


def _speech_pattern(seconds: float, start_offset: float) -> list[int]:
    """0.4s 一节的假语音：0.2s 类语音爆发 + 0.2s 静音，起读位置可偏移。"""
    samples: list[int] = []
    total = int(seconds * SAMPLE_RATE)
    start = int(start_offset * SAMPLE_RATE)
    samples.extend([0] * start)
    position = start
    while position < total:
        for i in range(int(0.2 * SAMPLE_RATE)):
            amplitude = 9000 + 4000 * math.sin(position * 0.05)
            samples.append(int(amplitude * math.sin(position * 0.045)))
            position += 1
            if position >= total:
                return samples
        samples.extend([0] * int(0.2 * SAMPLE_RATE))
        position += int(0.2 * SAMPLE_RATE)
    return samples


def test_best_offset_finds_shifted_envelope() -> None:
    guide = [1.0 if (i // 10) % 2 == 0 else 0.0 for i in range(200)]
    shifted = [0.0] * 30 + guide[:-30]
    correlation, lag = best_offset_correlation(shifted, guide, max_lag_windows=80)
    assert lag == 30
    assert correlation > 0.95


def test_best_offset_unrelated_envelopes_score_low() -> None:
    guide = [1.0 if (i // 10) % 2 == 0 else 0.0 for i in range(200)]
    rng = random.Random(7)
    other = [rng.random() for _ in range(200)]
    correlation, _ = best_offset_correlation(other, guide, max_lag_windows=50)
    assert correlation < 0.3


def _fake_run_command(driving_samples: list[int], guide_samples: list[int], has_audio: bool = True):
    def fake(command, *, cwd=None, log_file=None, env=None):
        argv = [str(item) for item in command]
        if argv[0] == "ffprobe":
            if "-select_streams" in argv:
                payload = {"streams": [{"codec_type": "audio"}]} if has_audio else {"streams": []}
            else:
                payload = {"format": {"duration": f"{len(driving_samples) / SAMPLE_RATE:.3f}"}}
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload))
        output = Path(argv[-1])
        if output.name == "sync_driving_16k.wav":
            _write_wav(output, driving_samples)
        else:
            _write_wav(output, guide_samples)
        return SimpleNamespace(returncode=0, stdout="")

    return fake


def _patch_run_command(monkeypatch, fake) -> None:
    """synccheck 与 media_duration（ffmpeg 模块）各自持有 run_command 引用，都要替换。"""
    monkeypatch.setattr("digital_human.synccheck.run_command", fake)
    monkeypatch.setattr("digital_human.ffmpeg.run_command", fake)


def test_check_driving_sync_measures_offset(monkeypatch, tmp_path: Path) -> None:
    guide = _speech_pattern(4.0, 0.5)
    driving = _speech_pattern(4.4, 1.0)  # 起读比导读晚 0.5s
    _patch_run_command(monkeypatch, _fake_run_command(driving, guide))
    report = check_driving_sync("ffmpeg", "ffprobe", tmp_path / "d.mp4", tmp_path / "g.wav", tmp_path)
    assert report is not None
    assert report.correlation > 0.5
    assert abs(report.offset_seconds - 0.5) < 2 * ENVELOPE_WINDOW_SECONDS


def test_check_driving_sync_rejects_unrelated_audio(monkeypatch, tmp_path: Path) -> None:
    guide = _speech_pattern(4.0, 0.5)
    noise = [
        int(6000 * math.sin(i * 0.013) * math.cos(i * 0.0031)) for i in range(int(4.4 * SAMPLE_RATE))
    ]
    _patch_run_command(monkeypatch, _fake_run_command(noise, guide))
    report = check_driving_sync("ffmpeg", "ffprobe", tmp_path / "d.mp4", tmp_path / "g.wav", tmp_path)
    assert report is not None
    assert report.correlation < 0.3


def test_check_driving_sync_skips_silent_and_missing_audio(monkeypatch, tmp_path: Path) -> None:
    guide = _speech_pattern(4.0, 0.5)
    silent = [0] * int(4.4 * SAMPLE_RATE)
    _patch_run_command(monkeypatch, _fake_run_command(silent, guide))
    assert check_driving_sync("ffmpeg", "ffprobe", tmp_path / "d.mp4", tmp_path / "g.wav", tmp_path) is None

    _patch_run_command(monkeypatch, _fake_run_command(silent, guide, has_audio=False))
    assert check_driving_sync("ffmpeg", "ffprobe", tmp_path / "d.mp4", tmp_path / "g.wav", tmp_path) is None


def test_pipeline_rejects_low_sync_before_render(monkeypatch, tmp_path: Path) -> None:
    from digital_human.pipeline import Pipeline

    local = SimpleNamespace(
        jobs_root=tmp_path, ffmpeg="ffmpeg", ffprobe="ffprobe"
    )
    job = SimpleNamespace(job_id="job", performance_drive={"min_sync_envelope": 0.3})
    pipeline = Pipeline(local, job)
    assert pipeline.driving_sync is None

    monkeypatch.setattr(
        "digital_human.pipeline.check_driving_sync",
        lambda *args, **kwargs: SyncReport(correlation=0.11, offset_seconds=-1.9),
    )
    try:
        pipeline._check_driving_sync(tmp_path / "driving.mp4", tmp_path / "guide.wav")
    except RuntimeError as exc:
        assert "0.11" in str(exc)
        assert "重录" in str(exc)
    else:
        raise AssertionError("低相关度录音应当被拒绝")

    monkeypatch.setattr(
        "digital_human.pipeline.check_driving_sync",
        lambda *args, **kwargs: SyncReport(correlation=0.72, offset_seconds=0.3),
    )
    pipeline._check_driving_sync(tmp_path / "driving.mp4", tmp_path / "guide.wav")
    assert pipeline.driving_sync == {
        "status": "measured",
        "correlation": 0.72,
        "offset_seconds": 0.3,
    }


def test_pipeline_sync_check_can_be_disabled(monkeypatch, tmp_path: Path) -> None:
    from digital_human.pipeline import Pipeline

    def unexpected(*args, **kwargs):  # pragma: no cover - 关闭时不应触达
        raise AssertionError("校验关闭时不应调用 check_driving_sync")

    monkeypatch.setattr("digital_human.pipeline.check_driving_sync", unexpected)
    pipeline = Pipeline(
        SimpleNamespace(jobs_root=tmp_path),
        SimpleNamespace(job_id="job", performance_drive={"min_sync_envelope": 0.0}),
    )
    pipeline._check_driving_sync(tmp_path / "d.mp4", tmp_path / "g.wav")
    assert pipeline.driving_sync == {"status": "disabled"}
