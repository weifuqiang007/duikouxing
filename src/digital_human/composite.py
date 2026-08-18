from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .config import MouthROI


def _video_writer(output: Path, fps: int, size: tuple[int, int]) -> cv2.VideoWriter:
    """流水线使用 MKV/FFV1 无损中间件；MP4 仅供独立预览兼容。"""
    codec = "FFV1" if output.suffix.lower() in {".mkv", ".avi"} else "mp4v"
    writer = cv2.VideoWriter(str(output), cv2.VideoWriter_fourcc(*codec), float(fps), size)
    if not writer.isOpened():
        raise RuntimeError(f"无法创建合成视频: {output} (codec={codec})")
    return writer


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
    writer = _video_writer(output, fps, (width, height))
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


def _fallback_face_box(
    frame_width: int, frame_height: int, roi: MouthROI
) -> tuple[int, int, int, int]:
    """从人工嘴部 ROI 推导人脸框，仅在检测失败时使用。"""
    mouth_x = frame_width * roi.center_x
    mouth_y = frame_height * roi.center_y
    width = max(frame_width * roi.width * 1.35, frame_width * 0.12)
    height = max(frame_height * roi.height * 3.2, width * 1.15)
    width = min(width, frame_width * 0.6)
    height = min(height, frame_height * 0.7)
    x = mouth_x - width / 2
    y = mouth_y - height * 0.70
    return _clip_box((x, y, width, height), frame_width, frame_height)


def _clip_box(
    box: tuple[float, float, float, float], frame_width: int, frame_height: int
) -> tuple[int, int, int, int]:
    x, y, width, height = box
    x = min(max(0, int(round(x))), frame_width - 2)
    y = min(max(0, int(round(y))), frame_height - 2)
    width = min(max(2, int(round(width))), frame_width - x)
    height = min(max(2, int(round(height))), frame_height - y)
    return x, y, width, height


def _smooth_box(
    previous: tuple[float, float, float, float] | None,
    current: tuple[int, int, int, int],
    retention: float,
) -> tuple[float, float, float, float]:
    if previous is None:
        return tuple(float(value) for value in current)
    return tuple(
        retention * old + (1.0 - retention) * new
        for old, new in zip(previous, current, strict=True)
    )


def _largest_face(
    detector: cv2.CascadeClassifier, frame: np.ndarray
) -> tuple[int, int, int, int] | None:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    minimum = max(40, min(frame.shape[:2]) // 12)
    faces = detector.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(minimum, minimum),
        flags=cv2.CASCADE_SCALE_IMAGE,
    )
    if len(faces) == 0:
        return None
    x, y, width, height = max(faces, key=lambda item: int(item[2]) * int(item[3]))
    return int(x), int(y), int(width), int(height)


def _dynamic_texture_mask(
    base_frame: np.ndarray,
    generated_frame: np.ndarray,
    face_box: tuple[int, int, int, int],
    feather_pixels: int,
    change_threshold: float,
) -> np.ndarray:
    """
    构建“下半脸皮肤 - 嘴唇/口腔”蒙版。

    这个蒙版只控制高频纹理回填，不在原视频与生成视频之间切换几何动作，
    因此即使边界不完美，也不会再出现固定嘴部贴片。
    """
    height, width = base_frame.shape[:2]
    x, y, face_width, face_height = face_box
    lower_face = np.zeros((height, width), dtype=np.uint8)
    center = (int(x + face_width * 0.5), int(y + face_height * 0.68))
    axes = (max(2, int(face_width * 0.48)), max(2, int(face_height * 0.39)))
    cv2.ellipse(lower_face, center, axes, 0, 0, 360, 255, -1)

    # 嘴唇、牙齿和口腔必须 100% 保留 MuseTalk 结果，不回填旧嘴部纹理。
    mouth = np.zeros_like(lower_face)
    mouth_center = (int(x + face_width * 0.5), int(y + face_height * 0.72))
    mouth_axes = (max(2, int(face_width * 0.27)), max(2, int(face_height * 0.15)))
    cv2.ellipse(mouth, mouth_center, mouth_axes, 0, 0, 360, 255, -1)

    difference = cv2.absdiff(base_frame, generated_frame)
    difference = cv2.cvtColor(difference, cv2.COLOR_BGR2GRAY)
    changed = np.where(difference >= change_threshold, 255, 0).astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    changed = cv2.morphologyEx(changed, cv2.MORPH_CLOSE, kernel)
    changed = cv2.dilate(changed, kernel, iterations=1)

    texture_region = cv2.bitwise_and(lower_face, changed)
    texture_region = cv2.bitwise_and(texture_region, cv2.bitwise_not(mouth))
    if feather_pixels > 0:
        kernel_size = feather_pixels * 2 + 1
        texture_region = cv2.GaussianBlur(texture_region, (kernel_size, kernel_size), 0)
    return texture_region.astype(np.float32)[:, :, None] / 255.0


def _warp_detail_to_generated(
    detail: np.ndarray,
    base_frame: np.ndarray,
    generated_frame: np.ndarray,
    face_box: tuple[int, int, int, int],
) -> np.ndarray:
    """使用稠密光流将原帧皮肤细节对齐到生成帧的局部几何。"""
    x, y, width, height = face_box
    base_crop = cv2.cvtColor(base_frame[y : y + height, x : x + width], cv2.COLOR_BGR2GRAY)
    generated_crop = cv2.cvtColor(
        generated_frame[y : y + height, x : x + width], cv2.COLOR_BGR2GRAY
    )
    flow = cv2.calcOpticalFlowFarneback(
        generated_crop,
        base_crop,
        None,
        pyr_scale=0.5,
        levels=3,
        winsize=21,
        iterations=3,
        poly_n=5,
        poly_sigma=1.2,
        flags=0,
    )
    grid_x, grid_y = np.meshgrid(
        np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32)
    )
    warped_crop = cv2.remap(
        detail[y : y + height, x : x + width],
        grid_x + flow[..., 0],
        grid_y + flow[..., 1],
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT,
    )
    warped = np.zeros_like(detail)
    warped[y : y + height, x : x + width] = warped_crop
    return warped


def composite_dynamic_texture(
    base_video: Path,
    generated_video: Path,
    output: Path,
    roi: MouthROI,
    fps: int,
    options: dict[str, Any],
) -> None:
    """
    以 MuseTalk 作为完整动作层，只从原视频迁移毛孔/痘坑/胡茬等高频细节。

    这与旧的固定 ROI 像素混合有本质区别：原视频中与新音频不一致的
    嘴唇、脸颊和下颌低频形变不会被重新贴回。
    """
    base = cv2.VideoCapture(str(base_video))
    generated = cv2.VideoCapture(str(generated_video))
    width = int(base.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(base.get(cv2.CAP_PROP_FRAME_HEIGHT))
    gen_width = int(generated.get(cv2.CAP_PROP_FRAME_WIDTH))
    gen_height = int(generated.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if width <= 0 or height <= 0:
        raise RuntimeError("无法读取基底视频尺寸")
    if (width, height) != (gen_width, gen_height):
        raise RuntimeError(
            f"基底与生成视频尺寸不同: {(width, height)} != {(gen_width, gen_height)}"
        )

    cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    detector = cv2.CascadeClassifier(str(cascade_path))
    if detector.empty():
        raise RuntimeError(f"无法加载 OpenCV 人脸检测器: {cascade_path}")

    output.parent.mkdir(parents=True, exist_ok=True)
    writer = _video_writer(output, fps, (width, height))

    strength = float(options.get("texture_strength", 0.55))
    detail_sigma = float(options.get("detail_sigma", 1.2))
    feather = int(options.get("mask_feather_pixels", 6))
    retention = float(options.get("temporal_ema", 0.8))
    detect_interval = max(1, int(options.get("detect_interval", 5)))
    change_threshold = float(options.get("change_threshold", 2.5))
    use_flow = bool(options.get("optical_flow", True))

    fallback = _fallback_face_box(width, height, roi)
    tracked_box: tuple[float, float, float, float] | None = None
    last_detected = fallback
    frame_count = 0
    try:
        while True:
            base_ok, base_frame = base.read()
            generated_ok, generated_frame = generated.read()
            if not base_ok or not generated_ok:
                break
            if frame_count % detect_interval == 0:
                last_detected = _largest_face(detector, generated_frame) or last_detected
            tracked_box = _smooth_box(tracked_box, last_detected, retention)
            face_box = _clip_box(tracked_box, width, height)

            blurred = cv2.GaussianBlur(base_frame, (0, 0), sigmaX=detail_sigma)
            detail = base_frame.astype(np.float32) - blurred.astype(np.float32)
            if use_flow:
                detail = _warp_detail_to_generated(
                    detail, base_frame, generated_frame, face_box
                )
            alpha = _dynamic_texture_mask(
                base_frame,
                generated_frame,
                face_box,
                feather,
                change_threshold,
            )
            result = generated_frame.astype(np.float32) + detail * alpha * strength
            writer.write(np.clip(result, 0, 255).astype(np.uint8))
            frame_count += 1
    finally:
        base.release()
        generated.release()
        writer.release()
    if frame_count == 0:
        raise RuntimeError("动态纹理合成未产生任何帧")


def composite_video(
    base_video: Path,
    generated_video: Path,
    output: Path,
    roi: MouthROI,
    fps: int,
    options: dict[str, Any],
) -> None:
    """按任务配置选择新纹理合成器或旧固定 ROI 兼容模式。"""
    mode = str(options.get("mode", "dynamic_texture"))
    if mode == "fixed_roi":
        composite_mouth_region(base_video, generated_video, output, roi, fps)
        return
    if mode == "dynamic_texture":
        composite_dynamic_texture(base_video, generated_video, output, roi, fps, options)
        return
    raise ValueError(f"未知合成模式: {mode}")

