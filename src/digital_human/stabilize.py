"""HeyGem 输出的人脸部色调时域稳定器。

HeyGem 逐帧重绘脸部会引入高频亮度/色度闪变（2026-08-26 实测：Y 帧间跳变为
基准 1.5 倍、Cb 达 2.2 倍，肉眼表现为"曝光一闪一闪"）。本模块对人脸区内的
Y/Cr/Cb 均值做时间 EMA 平滑，把每帧相对平滑轨迹的偏移回写回画面。

设计约束：

- 只作用于生成画面自身的时间轴统计，不引用基准视频帧——规避 HeyGem 输出
  恒少 2 帧带来的帧对齐问题。
- 只在人脸羽化框内生效，背景与证件区域保持 HeyGem 输出原样。
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

DEFAULT_FACE_BOX = (0.15, 0.03, 0.85, 0.55)
DEFAULT_FEATHER = 48
DEFAULT_MAX_DELTA = 3.0


class StabilizeError(RuntimeError):
    """色调稳定失败。"""


def tone_deltas(
    means: Sequence[Sequence[float]],
    ema: float,
    max_delta: float,
    deadband: float = 0.5,
) -> list[tuple[float, float, float]]:
    """对 (y, cr, cb) 均值序列求 EMA 平滑轨迹与实测值的偏差。

    偏差经死区与限幅：小于死区不校正（避免为纯噪声引入处理），其余按连续值
    校正；编码端配合 chroma-qp-offset 细量化，使亚整数级偏移不被 4:2:0
    色度量化重新打散成块噪声。
    """
    if not 0.0 < ema < 1.0:
        raise StabilizeError(f"ema 必须在 (0,1) 开区间: {ema}")
    smoothed: np.ndarray | None = None
    deltas: list[tuple[float, float, float]] = []
    for mean in means:
        current = np.asarray(mean, dtype=np.float64)
        smoothed = current.copy() if smoothed is None else ema * smoothed + (1 - ema) * current
        delta = np.clip(smoothed - current, -max_delta, max_delta)
        applied = np.where(np.abs(delta) <= deadband, 0.0, delta)
        deltas.append((float(applied[0]), float(applied[1]), float(applied[2])))
    return deltas


def feathered_mask(
    height: int,
    width: int,
    box: tuple[float, float, float, float],
    feather: int,
) -> np.ndarray:
    """按归一化 box 生成 [0,1] 羽化掩膜，形状 (h, w, 1)。"""
    mask = np.zeros((height, width), dtype=np.float32)
    x1, y1 = int(round(box[0] * width)), int(round(box[1] * height))
    x2, y2 = int(round(box[2] * width)), int(round(box[3] * height))
    mask[max(0, y1) : min(height, y2), max(0, x1) : min(width, x2)] = 1.0
    if feather > 0:
        size = feather * 2 + 1
        mask = cv2.GaussianBlur(mask, (size, size), 0)
    return mask[:, :, None]


def open_encoder(
    ffmpeg: str,
    width: int,
    height: int,
    fps: int,
    output: Path,
) -> tuple[subprocess.Popen, list[bytes]]:
    """打开 rawvideo -> libx264 管道编码器;返回 (进程, stderr 收集列表)。

    细色度量化(chroma-qp-offset)使亚整数级的色调偏移不被 4:2:0 量化打散。
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
        "-r", str(fps), "-i", "-",
        "-an", "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p",
        "-x264-params", "chroma-qp-offset=-9",
        str(output),
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    stderr_chunks: list[bytes] = []
    threading.Thread(
        target=lambda: stderr_chunks.extend(
            iter(lambda: process.stderr.read(4096) if process.stderr else b"", b"")  # type: ignore[union-attr]
        ),
        daemon=True,
    ).start()
    return process, stderr_chunks


def finish_encoder(process: subprocess.Popen, stderr_chunks: list[bytes]) -> None:
    """关闭管道并等待编码器退出,非零退出码抛错。"""
    assert process.stdin is not None
    process.stdin.close()
    returncode = process.wait(timeout=600)
    if returncode != 0:
        stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")[-2000:]
        raise StabilizeError(f"ffmpeg 编码失败（退出码 {returncode}）:\n{stderr}")


def stabilize_face_tone(
    video: Path,
    output: Path,
    *,
    fps: int,
    ffmpeg: str = "ffmpeg",
    ema: float = 0.9,
    face_box: tuple[float, float, float, float] = DEFAULT_FACE_BOX,
    feather: int = DEFAULT_FEATHER,
    max_delta: float = DEFAULT_MAX_DELTA,
) -> None:
    """两遍处理：先统计每帧人脸区 Y/Cr/Cb 均值，再按 EMA 偏差回写并编码。

    输出经 ffmpeg rawvideo 管道用 libx264 CRF12 编码，避免 OpenCV 编码器
    不可控的画质损失。
    """
    # 第一遍：统计。
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise StabilizeError(f"无法打开视频: {video}")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    x1, y1 = int(face_box[0] * width), int(face_box[1] * height)
    x2, y2 = int(face_box[2] * width), int(face_box[3] * height)
    stats: list[tuple[float, float, float]] = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        ycc = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
        region = ycc[y1:y2, x1:x2]
        stats.append(
            (float(region[..., 0].mean()), float(region[..., 1].mean()),
             float(region[..., 2].mean())
             )
        )
    cap.release()
    if not stats:
        raise StabilizeError(f"视频没有可读帧: {video}")

    deltas = tone_deltas(stats, ema, max_delta)
    mask = feathered_mask(height, width, face_box, feather)

    # 第二遍：回写并经 ffmpeg 管道编码。
    process, stderr_chunks = open_encoder(ffmpeg, width, height, fps, output)
    cap = cv2.VideoCapture(str(video))
    try:
        index = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            ycc = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb).astype(np.float32)
            delta = deltas[index]
            for channel, value in enumerate(delta):
                ycc[..., channel] += value * mask[..., 0]
            corrected = cv2.cvtColor(
                np.clip(ycc, 0, 255).astype(np.uint8), cv2.COLOR_YCrCb2BGR
            )
            assert process.stdin is not None
            process.stdin.write(corrected.tobytes())
            index += 1
    finally:
        cap.release()
    finish_encoder(process, stderr_chunks)
    if index != len(stats):
        raise StabilizeError(f"两遍读取帧数不一致: {index} != {len(stats)}")
