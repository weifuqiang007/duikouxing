"""HeyGem 本地 Docker 服务的适配层。

API 语义按 2026-08-26 实测实现（docs/HEYGEM_LOCAL_DEPLOYMENT.md §6.1）：

- ``audio_url`` / ``video_url`` 必须是容器可下载的 HTTP URL。适配器内置一个只服务
  ``temp/<task_code>/`` 前缀的临时 HTTP 文件服务，通过 ``host.docker.internal`` 提供给容器。
- ``/easy/query`` 的任务记录在首次返回成功/失败后即被服务删除，轮询循环拿到终态后
  不再重复查询。
- 结果文件 ``data.result`` 形如 ``/<code>-r.mp4``，实际位于共享根的 ``temp/`` 目录下。
- 服务单任务串行，submit 返回 10001（忙碌）时在超时窗口内退避重试。
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any

from ..config import JobConfig, LocalConfig

RESULT_BUSY = 10001
RESULT_SUCCESS = 10000
TASK_FAILURE_CODES = {9999, 10002, 10003}
STATUS_SUCCESS = 2
STATUS_ERROR = 3
CONTENT_TYPES = {".mp4": "video/mp4", ".wav": "audio/wav", ".json": "application/json"}


class HeyGemError(RuntimeError):
    """HeyGem 服务调用失败。"""


class _TaskFileServer:
    """只服务单个任务 stage 目录的极简 HTTP 文件服务。

    绑定 0.0.0.0 是因为容器经 ``host.docker.internal``（WSL NAT）访问宿主机；
    路径白名单保证除当前任务的 ``temp/<code>/`` 外不接受任何请求。
    """

    def __init__(self, root: Path, prefix: str, port: int) -> None:
        self._root = root
        self._prefix = prefix
        self._port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server 约定
                relative = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
                if not relative.startswith("/" + server._prefix):
                    self.send_error(404)
                    return
                pure = PurePosixPath(relative.lstrip("/"))
                if pure.is_absolute() or ".." in pure.parts:
                    self.send_error(404)
                    return
                target = server._root.joinpath(*pure.parts).resolve()
                if not target.is_relative_to(server._root.resolve()):
                    self.send_error(404)
                    return
                if not target.is_file():
                    self.send_error(404)
                    return
                data = target.read_bytes()
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream"),
                )
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

        return Handler

    def start(self) -> None:
        try:
            handler = self._make_handler()
            self._server = ThreadingHTTPServer(("0.0.0.0", self._port), handler)
        except OSError as exc:
            raise HeyGemError(
                f"无法启动 HeyGem 文件服务（端口 {self._port} 可能被占用）: {exc}"
            ) from exc
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="heygem-file-server"
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    def url_for(self, container_host: str, relative: str) -> str:
        assert self._server is not None
        port = self._server.server_address[1]
        return f"http://{container_host}:{port}/{relative}"


def _json_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    # 直连本机服务，绝不走系统代理（Clash 会把 127.0.0.1 请求发往远端）。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HeyGemError(f"HeyGem 请求失败: {url}: {exc}") from exc
    if not isinstance(result, dict):
        raise HeyGemError(f"HeyGem 返回值不是 JSON 对象: {url}")
    return result


def _result_path(shared_root: Path, result_ref: str) -> Path:
    """把 data.result（形如 ``/<code>-r.mp4``）解析到共享根的 temp/ 下。

    拒绝绝对路径、目录穿越和 temp 目录之外的任何引用。
    """
    pure = PurePosixPath(result_ref.replace("\\", "/").lstrip("/"))
    if not pure.parts or pure.is_absolute() or ".." in pure.parts:
        raise HeyGemError(f"HeyGem 返回了不安全的结果路径: {result_ref!r}")
    if pure.parts[0] != "temp":
        # 实测 result 为 "/<code>-r.mp4"，服务把它放在 temp/ 下；其余形态一律拒绝。
        pure = PurePosixPath("temp", *pure.parts)
    candidate = shared_root.joinpath(*pure.parts).resolve()
    root = shared_root.resolve()
    if not candidate.is_relative_to(root / "temp"):
        raise HeyGemError(f"HeyGem 结果越出 temp 目录: {result_ref!r}")
    return candidate


class HeyGemAdapter:
    def __init__(self, config: LocalConfig) -> None:
        self.config = config

    def generate(
        self,
        *,
        video: Path,
        audio: Path,
        output: Path,
        job: JobConfig,
        work_dir: Path,  # noqa: ARG002 - 与其他适配器签名保持一致
        log_file: Path,
    ) -> None:
        shared_root = self.config.heygem_shared_root.resolve()
        temp_root = shared_root / "temp"
        temp_root.mkdir(parents=True, exist_ok=True)
        task_code = uuid.uuid4().hex
        stage_dir = temp_root / task_code
        stage_dir.mkdir(parents=True, exist_ok=False)

        staged_video = stage_dir / "base.mp4"
        staged_audio = stage_dir / "target.wav"
        shutil.copy2(video, staged_video)
        shutil.copy2(audio, staged_audio)

        log_file.parent.mkdir(parents=True, exist_ok=True)
        # 日志只记任务码与状态，不记话术、证件号或完整媒体内容。
        log_file.write_text(f"submit code={task_code}\n", encoding="utf-8")

        server = _TaskFileServer(shared_root, f"temp/{task_code}",
                                 self.config.heygem_file_server_port)
        server.start()
        try:
            base = self.config.heygem_base_url.rstrip("/")
            payload = {
                "audio_url": server.url_for(
                    self.config.heygem_container_host,
                    staged_audio.relative_to(shared_root).as_posix(),
                ),
                "video_url": server.url_for(
                    self.config.heygem_container_host,
                    staged_video.relative_to(shared_root).as_posix(),
                ),
                "code": task_code,
                "chaofen": int(job.lipsync.get("chaofen", 0)),
                "watermark_switch": int(job.lipsync.get("watermark_switch", 0)),
                "pn": int(job.lipsync.get("pn", 1)),
            }
            deadline = time.monotonic() + self.config.heygem_timeout_seconds
            self._submit(base, payload, deadline, log_file)
            result_ref = self._poll(base, task_code, deadline, log_file)
        finally:
            server.stop()

        produced = _result_path(shared_root, result_ref)
        if not produced.is_file() or produced.stat().st_size == 0:
            raise HeyGemError(f"HeyGem 未生成有效视频: {result_ref!r}")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(produced, output)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(
                f"result size={produced.stat().st_size} -> {output.name}\n"
            )
        if self.config.heygem_cleanup_stage:
            self._cleanup(temp_root, task_code)

    def _submit(
        self, base: str, payload: dict[str, Any], deadline: float, log_file: Path
    ) -> None:
        while True:
            submit = _json_request(
                f"{base}/submit", method="POST", payload=payload, timeout=60
            )
            code = int(submit.get("code", -1))
            if code == RESULT_SUCCESS:
                return
            if code == RESULT_BUSY:
                if time.monotonic() >= deadline:
                    raise HeyGemError("HeyGem 服务持续忙碌，提交超时")
                time.sleep(self.config.heygem_poll_interval_seconds)
                continue
            raise HeyGemError(
                f"HeyGem 拒绝任务: code={code} msg={submit.get('msg')}"
            )

    def _poll(self, base: str, task_code: str, deadline: float, log_file: Path) -> str:
        query_url = f"{base}/query?" + urllib.parse.urlencode({"code": task_code})
        last_progress = ""
        while time.monotonic() < deadline:
            status = _json_request(query_url, timeout=30)
            code = int(status.get("code", -1))
            if code != RESULT_SUCCESS:
                if code in TASK_FAILURE_CODES:
                    raise HeyGemError(
                        f"HeyGem 查询失败: code={code} msg={status.get('msg')}"
                    )
                time.sleep(self.config.heygem_poll_interval_seconds)
                continue
            data = status.get("data") or {}
            state = int(data.get("status", 0))
            progress = str(data.get("progress", ""))
            if progress != last_progress:
                with log_file.open("a", encoding="utf-8") as handle:
                    handle.write(f"status={state} progress={progress}\n")
                last_progress = progress
            if state == STATUS_SUCCESS:
                return str(data.get("result", ""))
            if state == STATUS_ERROR:
                raise HeyGemError(f"HeyGem 生成失败: {data.get('msg', '')}")
            time.sleep(self.config.heygem_poll_interval_seconds)
        raise HeyGemError(f"HeyGem 任务超时: {task_code}")

    def _cleanup(self, temp_root: Path, task_code: str) -> None:
        """只删除确认为本任务前缀的 temp 产物（输入目录、下载件、中间件与结果）。"""
        root = temp_root.resolve()
        for candidate in (
            temp_root / task_code,
            temp_root / f"{task_code}.wav",
            temp_root / f"{task_code}.mp4",
            temp_root / f"{task_code}-r.mp4",
        ):
            resolved = candidate.resolve()
            if not resolved.is_relative_to(root):
                continue
            if resolved.is_dir():
                shutil.rmtree(resolved, ignore_errors=True)
            elif resolved.exists():
                resolved.unlink()
