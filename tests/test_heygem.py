"""HeyGem 适配器单元测试：用本地 fake HTTP 服务模拟真实容器 API。

覆盖：提交字段、忙碌重试、轮询成功/失败、超时、非 JSON、路径穿越、
结果缺失、输入文件服务的白名单、以及 stage 清理。不依赖 GPU 服务。
"""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from digital_human.adapters.heygem import HeyGemAdapter, HeyGemError
from digital_human.config import JobConfig, LocalConfig, MouthROI


class FakeHeyGem:
    """按 docs §6.1 实测语义模拟 /easy/submit 与 /easy/query。"""

    def __init__(self, shared_root: Path, state: dict[str, Any] | None = None) -> None:
        self.shared_root = shared_root
        self.state = state or {}
        self.submits: list[dict[str, Any]] = []
        self.submit_seen = 0
        self.poll_count = 0
        self.downloaded: dict[str, int] = {}
        self.forbidden_status: int | None = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_raw(self, body: bytes) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _fetch(self, url: str) -> int:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({})
                )
                with opener.open(url, timeout=10) as response:
                    return len(response.read())

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                owner.submits.append(payload)
                if owner.state.get("raw"):
                    self._send_raw(b"not-json")
                    return
                owner.submit_seen += 1
                if owner.submit_seen <= int(owner.state.get("busy_first", 0)):
                    self._send({"code": 10001, "msg": "忙碌中", "success": True})
                    return
                reject = owner.state.get("reject_code")
                if reject:
                    self._send({"code": reject, "msg": "参数异常", "success": False})
                    return
                if owner.state.get("fetch_inputs", True):
                    owner.downloaded["audio"] = self._fetch(payload["audio_url"])
                    owner.downloaded["video"] = self._fetch(payload["video_url"])
                    try:
                        self._fetch(
                            payload["audio_url"].rsplit("/", 1)[0]
                            + "/../other/secret.wav"
                        )
                        owner.forbidden_status = 200
                    except urllib.error.HTTPError as exc:
                        owner.forbidden_status = exc.code
                self._send({"code": 10000, "msg": 10000, "success": True})

            def do_GET(self) -> None:  # noqa: N802
                if owner.state.get("raw"):
                    self._send_raw(b"<html>")
                    return
                code = self.path.split("code=")[-1]
                if code != "wrong" and not owner.submits:
                    self._send({"code": 10004, "msg": "任务不存在", "success": True})
                    return
                owner.poll_count += 1
                if owner.state.get("fail_status3"):
                    self._send(
                        {
                            "code": 10000,
                            "success": True,
                            "data": {"status": 3, "msg": "内部失败", "result": ""},
                        }
                    )
                    return
                if owner.poll_count <= int(owner.state.get("running_polls", 0)):
                    self._send(
                        {
                            "code": 10000,
                            "success": True,
                            "data": {"status": 1, "progress": "50%"},
                        }
                    )
                    return
                task_code = owner.submits[-1]["code"] if owner.submits else ""
                ref = owner.state.get("result_ref", f"/{task_code}-r.mp4")
                content = owner.state.get("result_content", b"generated-video-bytes")
                if content is not None:
                    result_file = owner.shared_root / "temp" / f"{task_code}-r.mp4"
                    result_file.parent.mkdir(parents=True, exist_ok=True)
                    result_file.write_bytes(content)
                self._send(
                    {
                        "code": 10000,
                        "success": True,
                        "data": {
                            "status": 2,
                            "progress": 100,
                            "result": ref,
                            "width": 720,
                            "height": 1280,
                        },
                    }
                )

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def _local_config(tmp_path: Path, base_url: str, **overrides: Any) -> LocalConfig:
    shared_root = tmp_path / "face2face"
    values: dict[str, Any] = dict(
        profile="home",
        conda="conda",
        ffmpeg="ffmpeg",
        ffprobe="ffprobe",
        orchestrator_env=tmp_path / "envs" / "orchestrator",
        dots_env=tmp_path / "envs" / "dots",
        musetalk_env=tmp_path / "envs" / "musetalk",
        latentsync_env=tmp_path / "envs" / "latentsync",
        dots_quality_model="dots-quality",
        dots_fast_model="dots-fast",
        musetalk_repo=tmp_path / "repos" / "musetalk",
        latentsync_repo=tmp_path / "repos" / "latentsync",
        latentsync_checkpoint=tmp_path / "repos" / "ckpt.pt",
        jobs_root=tmp_path / "jobs",
        expected_gpu="RTX 4070",
        gpu_id=0,
        musetalk_batch_size=4,
        use_float16=True,
        tts_profile="quality",
        primary_lipsync_engine="heygem_local",
        heygem_base_url=base_url,
        heygem_shared_root=shared_root,
        heygem_timeout_seconds=overrides.pop("timeout_seconds", 8),
        heygem_poll_interval_seconds=overrides.pop("poll_interval_seconds", 0.05),
        heygem_file_server_port=overrides.pop("file_server_port", 0),
        heygem_container_host="127.0.0.1",
        heygem_cleanup_stage=overrides.pop("cleanup_stage", False),
    )
    assert not overrides, f"未知覆盖项: {overrides}"
    return LocalConfig(**values)


def _job(tmp_path: Path) -> JobConfig:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    return JobConfig(
        job_id="heygem-job",
        consent_confirmed=True,
        local_only=True,
        source_video=source,
        reference_audio=None,
        reference_start_seconds=0.0,
        reference_duration_seconds=10.0,
        reference_text="参考文字",
        script="新话术内容",
        tts={},
        video={"fps": 25},
        lipsync={
            "engine": "heygem_local",
            "chaofen": 0,
            "watermark_switch": 0,
            "pn": 1,
        },
        composite={"mode": "native"},
        mouth_roi=MouthROI(0.5, 0.5, 0.2, 0.1, 10),
    )


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    video = tmp_path / "base.mp4"
    video.write_bytes(b"base-video-content")
    audio = tmp_path / "target.wav"
    audio.write_bytes(b"target-audio-content")
    return video, audio


def _run(config: LocalConfig, job: JobConfig, tmp_path: Path) -> Path:
    video, audio = _inputs(tmp_path)
    output = tmp_path / "out" / "heygem_result.mp4"
    HeyGemAdapter(config).generate(
        video=video,
        audio=audio,
        output=output,
        job=job,
        work_dir=tmp_path,
        log_file=tmp_path / "heygem.log",
    )
    return output


@pytest.fixture()
def fake(tmp_path: Path):
    server = FakeHeyGem(tmp_path / "face2face")
    yield server
    server.stop()


def test_submit_payload_and_success(tmp_path: Path, fake: FakeHeyGem) -> None:
    fake.state.update({"busy_first": 1, "running_polls": 2})
    config = _local_config(tmp_path, f"http://127.0.0.1:{fake.port}/easy")
    output = _run(config, _job(tmp_path), tmp_path)

    assert output.read_bytes() == b"generated-video-bytes"
    assert len(fake.submits) == 2  # 一次忙碌 + 一次成功
    payload = fake.submits[-1]
    code = payload["code"]
    assert len(code) == 32 and code.isalnum()
    assert payload["audio_url"].startswith("http://127.0.0.1:")
    assert payload["audio_url"].endswith(f"/temp/{code}/target.wav")
    assert payload["video_url"].endswith(f"/temp/{code}/base.mp4")
    assert payload["chaofen"] == 0 and payload["watermark_switch"] == 0
    assert payload["pn"] == 1
    # 输入文件确实被服务端下载，且白名单外的路径被拒绝。
    assert fake.downloaded == {"audio": len(b"target-audio-content"),
                               "video": len(b"base-video-content")}
    assert fake.forbidden_status == 404
    log = (tmp_path / "heygem.log").read_text(encoding="utf-8")
    assert f"submit code={code}" in log
    assert "新话术内容" not in log
    assert "参考文字" not in log


def test_service_reports_generation_failure(tmp_path: Path, fake: FakeHeyGem) -> None:
    fake.state.update({"fail_status3": True})
    config = _local_config(tmp_path, f"http://127.0.0.1:{fake.port}/easy")
    with pytest.raises(HeyGemError, match="生成失败"):
        _run(config, _job(tmp_path), tmp_path)


def test_submit_rejection_is_fatal(tmp_path: Path, fake: FakeHeyGem) -> None:
    fake.state.update({"reject_code": 10002})
    config = _local_config(tmp_path, f"http://127.0.0.1:{fake.port}/easy")
    with pytest.raises(HeyGemError, match="拒绝任务"):
        _run(config, _job(tmp_path), tmp_path)


def test_timeout_while_running(tmp_path: Path, fake: FakeHeyGem) -> None:
    fake.state.update({"running_polls": 10_000})
    config = _local_config(
        tmp_path, f"http://127.0.0.1:{fake.port}/easy", timeout_seconds=1
    )
    with pytest.raises(HeyGemError, match="超时"):
        _run(config, _job(tmp_path), tmp_path)


def test_non_json_response(tmp_path: Path, fake: FakeHeyGem) -> None:
    fake.state.update({"raw": True})
    config = _local_config(tmp_path, f"http://127.0.0.1:{fake.port}/easy")
    with pytest.raises(HeyGemError, match="请求失败"):
        _run(config, _job(tmp_path), tmp_path)


def test_unsafe_result_path_is_rejected(tmp_path: Path, fake: FakeHeyGem) -> None:
    fake.state.update({"result_ref": "/../../evil.mp4", "result_content": None})
    config = _local_config(tmp_path, f"http://127.0.0.1:{fake.port}/easy")
    with pytest.raises(HeyGemError, match="不安全的结果路径"):
        _run(config, _job(tmp_path), tmp_path)


def test_absolute_result_path_is_rejected(tmp_path: Path, fake: FakeHeyGem) -> None:
    fake.state.update({"result_ref": "/etc/passwd", "result_content": None})
    config = _local_config(tmp_path, f"http://127.0.0.1:{fake.port}/easy")
    with pytest.raises(HeyGemError):
        _run(config, _job(tmp_path), tmp_path)


def test_missing_result_file_is_rejected(tmp_path: Path, fake: FakeHeyGem) -> None:
    fake.state.update({"result_content": None})
    config = _local_config(tmp_path, f"http://127.0.0.1:{fake.port}/easy")
    with pytest.raises(HeyGemError, match="未生成有效视频"):
        _run(config, _job(tmp_path), tmp_path)


def test_cleanup_stage_removes_task_artifacts(tmp_path: Path, fake: FakeHeyGem) -> None:
    config = _local_config(
        tmp_path, f"http://127.0.0.1:{fake.port}/easy", cleanup_stage=True
    )
    output = _run(config, _job(tmp_path), tmp_path)
    assert output.read_bytes() == b"generated-video-bytes"
    temp_root = config.heygem_shared_root / "temp"
    assert list(temp_root.iterdir()) == []


def test_stage_kept_by_default(tmp_path: Path, fake: FakeHeyGem) -> None:
    config = _local_config(tmp_path, f"http://127.0.0.1:{fake.port}/easy")
    _run(config, _job(tmp_path), tmp_path)
    temp_root = config.heygem_shared_root / "temp"
    entries = sorted(
        str(p.relative_to(temp_root)).replace("\\", "/")
        for p in temp_root.rglob("*")
    )
    assert any(name.endswith("-r.mp4") for name in entries)
    assert any(name.endswith("/base.mp4") for name in entries)
