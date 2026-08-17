from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .config import MouthROI


def _mask(width: int, height: int, roi: MouthROI) -> np.ndarray:
    mask = np.zeros((height, width), dtype=np.uint8)
    center = (int(width * roi.center_x), int(height * roi.center_y))
    axes = (max(1, int(width * roi.width / 2)), max(1, int(height * roi.height / 2)))
    cv2.ellipse(mask, center, axes, 0, 0, 360, 255, -1)
    if roi.feather_pixels:
        kernel = roi.feather_pixels * 2 + 1
        mask = cv2.GaussianBlur(mask, (kernel, kernel), 0)
    return mask.astype(np.float32)[:, :, None] / 255.0


def grab_frame(video: Path, at_seconds: float = 0.0) -> np.ndarray:
    capture = cv2.VideoCapture(str(video))
    if at_seconds > 0:
        capture.set(cv2.CAP_PROP_POS_MSEC, at_seconds * 1000)
    ok, frame = capture.read()
    capture.release()
    if not ok:
        raise RuntimeError(f"无法读取视频帧: {video} @ {at_seconds}s")
    return frame


def box_to_roi(
    x: int, y: int, w: int, h: int, frame_w: int, frame_h: int, feather_pixels: int
) -> MouthROI:
    """把拖拽矩形换算成归一化 ROI；椭圆内接于矩形，中心和尺寸裁剪到不超出画面。"""
    size_w = min(w / frame_w, 1.0)
    size_h = min(h / frame_h, 1.0)
    center_x = round(min(max((x + w / 2) / frame_w, size_w / 2), 1 - size_w / 2), 4)
    center_y = round(min(max((y + h / 2) / frame_h, size_h / 2), 1 - size_h / 2), 4)
    # 留 0.0005 余量，保证中心/尺寸四舍五入后仍满足 validate_job 的不出画面约束。
    width = round(min(size_w, 2 * min(center_x, 1 - center_x) - 0.0005), 4)
    height = round(min(size_h, 2 * min(center_y, 1 - center_y) - 0.0005), 4)
    return MouthROI(
        center_x=round(center_x, 4),
        center_y=round(center_y, 4),
        width=round(width, 4),
        height=round(height, 4),
        feather_pixels=feather_pixels,
    )


def preview_roi(video: Path, roi: MouthROI, output: Path) -> None:
    capture = cv2.VideoCapture(str(video))
    ok, frame = capture.read()
    capture.release()
    if not ok:
        raise RuntimeError(f"无法读取视频首帧: {video}")
    height, width = frame.shape[:2]
    center = (int(width * roi.center_x), int(height * roi.center_y))
    axes = (int(width * roi.width / 2), int(height * roi.height / 2))
    cv2.ellipse(frame, center, axes, 0, 0, 360, (0, 255, 255), 3)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), frame):
        raise RuntimeError(f"无法写入 ROI 预览: {output}")


def composite_mouth_region(
    base_video: Path,
    generated_video: Path,
    output: Path,
    roi: MouthROI,
    fps: int,
) -> None:
    base = cv2.VideoCapture(str(base_video))
    generated = cv2.VideoCapture(str(generated_video))
    width = int(base.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(base.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if width <= 0 or height <= 0:
        raise RuntimeError("无法读取基底视频尺寸")
    gen_width = int(generated.get(cv2.CAP_PROP_FRAME_WIDTH))
    gen_height = int(generated.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if (width, height) != (gen_width, gen_height):
        raise RuntimeError(
            f"基底与生成视频尺寸不同: {(width, height)} != {(gen_width, gen_height)}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(output), cv2.VideoWriter_fourcc(*"mp4v"), float(fps), (width, height)
    )
    if not writer.isOpened():
        raise RuntimeError(f"无法创建合成视频: {output}")
    alpha = _mask(width, height, roi)
    frame_count = 0
    try:
        while True:
            base_ok, base_frame = base.read()
            generated_ok, generated_frame = generated.read()
            if not base_ok or not generated_ok:
                break
            mixed = (
                generated_frame.astype(np.float32) * alpha
                + base_frame.astype(np.float32) * (1.0 - alpha)
            )
            writer.write(np.clip(mixed, 0, 255).astype(np.uint8))
            frame_count += 1
    finally:
        base.release()
        generated.release()
        writer.release()
    if frame_count == 0:
        raise RuntimeError("局部合成未产生任何帧")

