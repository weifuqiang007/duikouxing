"""光流几何稳定器单元测试:纯函数 + 合成抖动视频回环验收。"""

from __future__ import annotations

import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest

from digital_human.motion_stabilize import (
    DEFAULT_MOUTH_EXCLUDE,
    centered_smooth,
    motion_corrections,
    motion_displacement_series,
    seed_points,
    stabilize_face,
    warp_frame,
)


def test_centered_smooth_constant_is_unchanged() -> None:
    series = np.full((40, 2), 3.5)
    out = centered_smooth(series, 15)
    assert np.allclose(out, 3.5)


def test_centered_smooth_spreads_impulse() -> None:
    series = np.zeros((31, 1))
    series[15, 0] = 1.0
    out = centered_smooth(series, 15)
    # 冲激被摊成 1/15 高的平顶,峰值显著下降且总和守恒。
    assert out.max() == pytest.approx(1 / 15)
    assert out.sum() == pytest.approx(1.0)


def test_centered_smooth_even_window_is_normalized() -> None:
    series = np.ones((20, 2))
    assert np.allclose(centered_smooth(series, 16), 1.0)


def test_motion_corrections_remove_zero_mean_jitter() -> None:
    rng = np.random.default_rng(3)
    count = 60
    drift = np.linspace(0, 8, count)  # 真实慢速运动
    jitter = rng.normal(0, 0.8, count)  # 高频抖动
    position = drift + jitter
    displacement = [(float(d), 0.0) for d in np.diff(position, prepend=0.0)]
    corrections = motion_corrections(displacement, window=15, max_shift=3.0)
    # 校正后位置 = 累计位移 - 校正量,其相对真实轨迹的残余应远小于原抖动。
    corrected_pos = np.cumsum([d[0] for d in displacement]) - np.array(
        [c[0] for c in corrections]
    )
    residual = (corrected_pos - drift)[10:-10].std()  # 去掉平滑窗口边缘
    assert residual < 0.35 * jitter.std(), (
        f"残余抖动 {residual:.3f} 未显著低于原抖动 {jitter.std():.3f}"
    )


def test_motion_corrections_clamps_to_max_shift() -> None:
    displacement = [(5.0, 0.0)] * 20  # 持续大位移(真实运动),轨迹-平滑差受限幅
    corrections = motion_corrections(displacement, window=15, max_shift=2.0)
    assert all(abs(dx) <= 2.0 + 1e-9 and dy == 0.0 for dx, dy in corrections)


def test_motion_corrections_handles_missing_frames() -> None:
    displacement: list[tuple[float, float] | None] = [(1.0, 0.0)] * 10
    displacement[5] = None
    corrections = motion_corrections(displacement, window=7, max_shift=3.0)
    assert len(corrections) == 10
    assert corrections[5] == corrections[4]  # 失败帧沿用上一帧
    assert all(motion_corrections([None, None], 7, 3.0)[i] == (0.0, 0.0) for i in (0, 1))


def test_seed_points_excludes_mouth_region() -> None:
    rng = np.random.default_rng(11)
    gray = (rng.integers(0, 255, (400, 300))).astype(np.uint8)
    face_box = (0.1, 0.1, 0.9, 0.9)
    points = seed_points(gray, face_box, DEFAULT_MOUTH_EXCLUDE, 150)
    assert points is not None and len(points) > 10
    h, w = gray.shape
    mx1, my1 = int(DEFAULT_MOUTH_EXCLUDE[0] * w), int(DEFAULT_MOUTH_EXCLUDE[1] * h)
    mx2, my2 = int(DEFAULT_MOUTH_EXCLUDE[2] * w), int(DEFAULT_MOUTH_EXCLUDE[3] * h)
    for point in points.reshape(-1, 2):
        assert not (mx1 <= point[0] < mx2 and my1 <= point[1] < my2)


def test_warp_frame_zero_shift_is_identity() -> None:
    frame = np.full((64, 64, 3), 120, dtype=np.uint8)
    mask = np.zeros((64, 64, 1), dtype=np.float32)
    mask[16:48, 16:48] = 1.0
    assert warp_frame(frame, (0.0, 0.0), mask) is frame


def test_warp_frame_shifts_inside_mask_only() -> None:
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    frame[40:60, 40:60] = 255  # 中心亮块
    mask = np.zeros((100, 100, 1), dtype=np.float32)
    mask[10:90, 10:90] = 1.0  # 框内全生效
    warped = warp_frame(frame, (5.0, 0.0), mask)
    # 平移后亮块中心右移 5px(内容左移),边界外不受影响。
    assert warped[50, 45, 0] == 255  # 新位置(原 40)有内容
    assert warped[50, 58, 0] == 0  # 原位置(58 在块内)已被移走
    assert warped[5, 5, 0] == 0 and warped[95, 95, 0] == 0


def _make_jitter_video(path: Path, frames: int = 60) -> tuple[float, float]:
    """合成:纹理脸块(慢漂移+高频抖动)+ 静止背景,返回(漂移斜率,抖动幅度)。"""
    rng = np.random.default_rng(5)
    texture = rng.integers(60, 200, (120, 160, 3), dtype=np.uint8)  # 可跟踪纹理
    canvas_h, canvas_w = 240, 320
    drift = np.linspace(0, 10, frames)
    jitter = rng.normal(0, 1.2, frames)
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (canvas_w, canvas_h)
    )
    for index in range(frames):
        frame = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
        x = int(80 + drift[index] + jitter[index])
        y = int(60 + 0.3 * drift[index] + 0.5 * jitter[index])
        frame[y:y + 120, x:x + 160] = texture
        writer.write(frame)
    writer.release()
    return 10.0 / frames, 1.2


def _feature_residual(path: Path, face_box: tuple[float, float, float, float]) -> float:
    series = motion_displacement_series(path, face_box)
    valid = [d for d in series if d is not None]
    arr = np.array(valid)
    # 高频残差 = 位移一阶差分的 std(剔除慢漂移后的帧间抖动)。
    return float(np.std(np.diff(arr[:, 0])) + np.std(np.diff(arr[:, 1])))


def test_stabilize_face_roundtrip_reduces_jitter(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("本机无 ffmpeg")
    source = tmp_path / "jitter.avi"
    _make_jitter_video(source)
    face_box = (0.25, 0.2, 0.75, 0.8)
    before = _feature_residual(source, face_box)
    output = tmp_path / "stable.mp4"
    stabilize_face(
        source,
        output,
        fps=30,
        ffmpeg=ffmpeg,
        face_box=face_box,
        feather=10,
        tone_ema=0.0,
        motion_smooth_frames=15,
        motion_max_shift=3.0,
    )
    assert output.is_file()
    after = _feature_residual(output, face_box)
    assert after < 0.75 * before, f"抖动未显著降低: {before:.3f} -> {after:.3f}"


def test_stabilize_face_rejects_all_disabled(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="不能同时关闭"):
        stabilize_face(
            tmp_path / "x.mp4",
            tmp_path / "y.mp4",
            fps=30,
            face_box=(0.1, 0.1, 0.9, 0.9),
            tone_ema=0.0,
            motion_smooth_frames=0,
        )
