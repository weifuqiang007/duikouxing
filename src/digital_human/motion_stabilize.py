"""HeyGem 输出的脸部光流几何稳定器。

HeyGem 逐帧重绘脸部,使鼻/上唇等本应稳定的区域产生 ±0.3~1px 的帧间随机
偏移(高频几何噪声)。本模块以稀疏光流估计头部整体位移,累加成位置轨迹,
用居中滑动平均分离"真实低频运动"与"高频抖动",再以亚像素平移把每帧
推回平滑轨迹。校正只在脸部羽化框内生效,背景与证件区域一个像素不动。

设计决策(2026-08-26 评审定稿):

- 特征点 ≤150、质量门槛优先、播种时挖掉嘴部区(嘴部点是头部运动的污染源)、
  数量跌破阈值即重新播种——点的放置纪律比数量重要。
- 每帧位移取全体跟踪点的中位数(对 ≤50% 离群点免疫),而非均值。
- 居中滑动平均(离线两遍处理可非因果,无相位滞后);窗口 15 帧@30fps 时
  通带 <0.9Hz(真实头部运动),抑制带 >2Hz(重绘抖动)。
- 校正量限幅:超出幅值上限的帧不硬纠,防止脸部羽化框边界出现接缝。
  校正量 = 原始轨迹 − 平滑轨迹(偏离方向),回写按其负值平移。
- 光流跟踪按帧对独立进行(不逐帧链式传递),跟踪失败帧校正量沿用上一帧,
  避免误差沿链累积。
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

# 播种特征点时挖掉的嘴部区(归一化):嘴部点随说话运动,会污染头部位移中位数。
DEFAULT_MOUTH_EXCLUDE = (0.38, 0.30, 0.62, 0.44)
DEFAULT_MAX_POINTS = 150
MIN_TRACK_POINTS = 20
# LK 光流参数:31px 窗口在 720p 人脸上有足够纹理支持,3 层金字塔容忍 2~3px 位移。
LK_WINDOW = (31, 31)
LK_LEVELS = 3
LK_CRITERIA = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01)


class MotionStabilizeError(RuntimeError):
    """几何稳定失败。"""


def centered_smooth(series: np.ndarray, window: int) -> np.ndarray:
    """居中滑动平均(零相位)。窗口为偶数时向下取奇;window<=1 原样返回。

    边缘用镜像填充,避免首尾轨迹被窗口截断拉偏。
    """
    if window <= 1:
        return series.copy()
    if window % 2 == 0:
        window -= 1
    if window <= 1 or len(series) < 2:
        return series.copy()
    pad = window // 2
    kernel = np.ones(window, dtype=np.float64) / window
    padded = np.pad(
        series,
        ((pad, pad), (0, 0)),
        mode="edge",
    )
    smoothed = np.empty_like(series, dtype=np.float64)
    for axis in range(series.shape[1]):
        smoothed[:, axis] = np.convolve(padded[:, axis], kernel, mode="valid")
    return smoothed


def motion_corrections(
    displacements: Sequence[tuple[float, float] | None],
    window: int,
    max_shift: float,
) -> list[tuple[float, float]]:
    """由逐帧位移(累加轨迹)计算每帧的 (dx, dy) 校正量。

    displacements 中 None 表示该帧光流失败(校正沿用前一帧);全为 None 时
    返回全零。校正 = 平滑轨迹 - 原始轨迹,逐轴限幅。
    """
    count = len(displacements)
    if count == 0:
        return []
    valid = np.array([d for d in displacements if d is not None], dtype=np.float64)
    if len(valid) == 0:
        return [(0.0, 0.0)] * count
    trajectory = np.cumsum(valid, axis=0)
    smooth = centered_smooth(trajectory, window)
    # 校正量 = 原始轨迹对平滑轨迹的偏离;回写时按 -校正 平移内容,使脸部
    # 落到平滑轨迹上(符号约定与 warp_frame 的 (-dx,-dy) 平移配套)。
    shift = np.clip(trajectory - smooth, -max_shift, max_shift)
    corrections = [tuple(map(float, row)) for row in shift]
    # 把校正量按帧映射回去:失败帧沿用最近一次成功帧的校正。
    result: list[tuple[float, float]] = []
    last = (0.0, 0.0)
    valid_index = 0
    for item in displacements:
        if item is not None:
            last = corrections[valid_index]
            valid_index += 1
        result.append(last)
    return result


def seed_points(
    gray: np.ndarray,
    face_box: tuple[float, float, float, float],
    mouth_exclude: tuple[float, float, float, float],
    max_points: int,
) -> np.ndarray | None:
    """在脸部区(挖掉嘴部)播种质量门槛过滤后的特征点。"""
    height, width = gray.shape
    x1, y1 = int(face_box[0] * width), int(face_box[1] * height)
    x2, y2 = int(face_box[2] * width), int(face_box[3] * height)
    crop = gray[max(0, y1):y2, max(0, x1):x2]
    if crop.size == 0:
        return None
    points = cv2.goodFeaturesToTrack(
        crop, maxCorners=max_points, qualityLevel=0.05, minDistance=8, blockSize=9
    )
    if points is None or len(points) == 0:
        return None
    # 挖掉嘴部区(坐标转换回全帧)。
    mx1, my1 = int(mouth_exclude[0] * width), int(mouth_exclude[1] * height)
    mx2, my2 = int(mouth_exclude[2] * width), int(mouth_exclude[3] * height)
    keep = []
    for point in points.reshape(-1, 2):
        px, py = point[0] + x1, point[1] + y1
        if mx1 <= px < mx2 and my1 <= py < my2:
            continue
        keep.append([px, py])
    if not keep:
        return None
    return np.array(keep, dtype=np.float32).reshape(-1, 1, 2)


def estimate_displacement(
    prev_gray: np.ndarray,
    gray: np.ndarray,
    prev_points: np.ndarray,
    points: np.ndarray,
) -> tuple[float, float] | None:
    """两帧间光流位移中位数;有效点不足时返回 None。"""
    next_pts, status, _err = cv2.calcOpticalFlowPyrLK(
        prev_gray, gray, prev_points, None,
        winSize=LK_WINDOW, maxLevel=LK_LEVELS, criteria=LK_CRITERIA,
    )
    if next_pts is None:
        return None
    good = status.ravel() == 1
    if good.sum() < MIN_TRACK_POINTS:
        return None
    delta = (next_pts[good] - prev_points[good]).reshape(-1, 2)
    med = np.median(delta, axis=0)
    return float(med[0]), float(med[1])


def motion_displacement_series(
    video: Path,
    face_box: tuple[float, float, float, float],
    mouth_exclude: tuple[float, float, float, float] = DEFAULT_MOUTH_EXCLUDE,
    max_points: int = DEFAULT_MAX_POINTS,
) -> list[tuple[float, float] | None]:
    """遍历视频,返回逐帧 (dx, dy) 头部位移(0 号帧为 None)。"""
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise MotionStabilizeError(f"无法打开视频: {video}")
    try:
        prev_gray: np.ndarray | None = None
        prev_points: np.ndarray | None = None
        prev_full: np.ndarray | None = None
        displacements: list[tuple[float, float] | None] = []
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            gray_full = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            points = seed_points(gray_full, face_box, mouth_exclude, max_points)
            if prev_gray is not None and prev_points is not None and points is not None:
                displacements.append(
                    estimate_displacement(prev_gray, gray_full, prev_points, points)
                )
            else:
                displacements.append(None)
            prev_gray = gray_full
            prev_points = points
            prev_full = gray_full  # noqa: F841 - 保留引用便于调试
        if not displacements:
            raise MotionStabilizeError(f"视频没有可读帧: {video}")
        return displacements
    finally:
        cap.release()


def warp_frame(
    frame: np.ndarray,
    shift: tuple[float, float],
    mask: np.ndarray,
) -> np.ndarray:
    """按 (dx, dy) 亚像素平移整帧,再与原帧做羽化融合:框内为平移结果。"""
    if shift == (0.0, 0.0):
        return frame
    dx, dy = shift
    matrix = np.float32([[1, 0, -dx], [0, 1, -dy]])
    warped = cv2.warpAffine(
        frame, matrix, (frame.shape[1], frame.shape[0]),
        flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT,
    )
    blended = (
        warped.astype(np.float32) * mask + frame.astype(np.float32) * (1.0 - mask)
    )
    return np.clip(blended, 0, 255).astype(np.uint8)


def stabilize_face(
    video: Path,
    output: Path,
    *,
    fps: int,
    ffmpeg: str = "ffmpeg",
    face_box: tuple[float, float, float, float],
    feather: int = 48,
    tone_ema: float = 0.0,
    tone_deadband: float = 0.5,
    tone_max_delta: float = 3.0,
    motion_smooth_frames: int = 15,
    motion_max_shift: float = 3.0,
    mouth_exclude: tuple[float, float, float, float] = DEFAULT_MOUTH_EXCLUDE,
    max_points: int = DEFAULT_MAX_POINTS,
) -> None:
    """色调 + 几何联合稳定(同一对视频遍历、同一次编码)。

    tone_ema=0 关闭色调校正;motion_smooth_frames=0 关闭几何校正;两者都为 0
    时调用无意义,直接报错以免静默产生一代多余编码。
    """
    from .stabilize import (
        feathered_mask,
        finish_encoder,
        open_encoder,
        tone_deltas,
    )

    tone_on = tone_ema > 0
    motion_on = motion_smooth_frames > 1
    if not tone_on and not motion_on:
        raise MotionStabilizeError("tone_ema 与 motion_smooth_frames 不能同时关闭")

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise MotionStabilizeError(f"无法打开视频: {video}")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    tone_stats: list[tuple[float, float, float]] = []
    displacements: list[tuple[float, float] | None] = []
    prev_gray: np.ndarray | None = None
    prev_points: np.ndarray | None = None
    x1, y1 = int(face_box[0] * width), int(face_box[1] * height)
    x2, y2 = int(face_box[2] * width), int(face_box[3] * height)
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if tone_on:
            ycc = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
            region = ycc[y1:y2, x1:x2]
            tone_stats.append(
                (float(region[..., 0].mean()), float(region[..., 1].mean()),
                 float(region[..., 2].mean()))
            )
        if motion_on:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            points = seed_points(gray, face_box, mouth_exclude, max_points)
            if prev_gray is not None and prev_points is not None and points is not None:
                displacements.append(
                    estimate_displacement(prev_gray, gray, prev_points, points)
                )
            else:
                displacements.append(None)
            prev_gray, prev_points = gray, points
    cap.release()
    frames_total = len(tone_stats) if tone_on else len(displacements)
    if frames_total == 0:
        raise MotionStabilizeError(f"视频没有可读帧: {video}")

    deltas = (
        tone_deltas(tone_stats, tone_ema, tone_max_delta, tone_deadband)
        if tone_on else [(0.0, 0.0, 0.0)] * frames_total
    )
    corrections = (
        motion_corrections(displacements, motion_smooth_frames, motion_max_shift)
        if motion_on else [(0.0, 0.0)] * frames_total
    )
    mask = feathered_mask(height, width, face_box, feather)

    process, stderr_chunks = open_encoder(ffmpeg, width, height, fps, output)
    cap = cv2.VideoCapture(str(video))
    try:
        index = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            corrected = warp_frame(frame, corrections[index], mask)
            if tone_on:
                ycc = cv2.cvtColor(corrected, cv2.COLOR_BGR2YCrCb).astype(np.float32)
                for channel, value in enumerate(deltas[index]):
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
    if index != frames_total:
        raise MotionStabilizeError(f"两遍读取帧数不一致: {index} != {frames_total}")
