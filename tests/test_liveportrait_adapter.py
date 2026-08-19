from pathlib import Path
from types import SimpleNamespace

from digital_human.adapters.liveportrait import LivePortraitAdapter


def test_liveportrait_command_uses_regional_pasteback(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "LivePortrait"
    repo.mkdir()
    (repo / "inference.py").write_text("", encoding="utf-8")
    runner_dir = repo.parents[1] / "scripts"
    runner_dir.mkdir(parents=True, exist_ok=True)
    (runner_dir / "liveportrait_runner.py").write_text("", encoding="utf-8")
    source = tmp_path / "source.mp4"
    driving = tmp_path / "driving.mp4"
    source.write_bytes(b"source")
    driving.write_bytes(b"driving")
    captured: list[str] = []

    def fake_run(command, *, cwd=None, log_file=None, env=None):
        captured.extend(str(item) for item in command)
        result_dir = Path(command[command.index("--output_dir") + 1])
        result_dir.mkdir(parents=True, exist_ok=True)
        (result_dir / "source--driving.mp4").write_bytes(b"result")
        return SimpleNamespace(returncode=0, stdout="ok")

    monkeypatch.setattr("digital_human.adapters.liveportrait.run_command", fake_run)
    local = SimpleNamespace(
        liveportrait_repo=repo,
        liveportrait_env=tmp_path / "env",
        conda="conda",
        gpu_id=0,
        use_float16=True,
    )
    job = SimpleNamespace(performance_drive={"animation_region": "exp", "driving_multiplier": 0.85})
    output = tmp_path / "output.mp4"
    LivePortraitAdapter(local).generate(
        source=source,
        driving=driving,
        output=output,
        job=job,
        work_dir=tmp_path / "work",
        log_file=tmp_path / "log.txt",
    )

    assert output.read_bytes() == b"result"
    assert "liveportrait_runner.py" in captured[5]
    assert "--animation_region" in captured
    assert captured[captured.index("--animation_region") + 1] == "exp"
    assert "--flag_relative_motion" in captured
    assert "--flag_pasteback" in captured
    assert "--flag_crop_driving_video" in captured
    assert "--flag_lip_retargeting" not in captured
