"""色调稳定器与单遍 pingpong 的单元测试。"""

from __future__ import annotations

import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest

from digital_human.ffmpeg import pingpong_filter_graph
from digital_human.stabilize import feathered_mask, stabilize_face_tone, tone_deltas


def test_tone_deltas_constant_series_gives_zero() -> None:
    means = [(100.0, 140.0, 120.0)] * 20
    assert all(d == (0.0, 0.0, 0.0) for d in tone_deltas(means, 0.9, 3.0))


def test_tone_deltas_bounds_single_frame_flash() -> None:
    base = (100.0, 140.0, 120.0)
    means = [base] * 10 + [(108.0, 140.0, 120.0)] + [base] * 10
    deltas = tone_deltas(means, 0.9, 6.0)
    # 过亮闪帧的校正为负、取整数级、且被 max_delta 限幅。
    assert deltas[10][0] == pytest.approx(-6.0)
    assert deltas[9] == (0.0, 0.0, 0.0)
    # 小偏差落在死区内不校正。
    tiny = [base] * 5 + [(100.4, 140.0, 120.0)] + [base] * 5
    assert all(d == (0.0, 0.0, 0.0) for d in tone_deltas(tiny, 0.9, 6.0))


def test_tone_deltas_rejects_invalid_ema() -> None:
    with pytest.raises(Exception, match="ema"):
        tone_deltas([(1.0, 1.0, 1.0)], 1.0, 3.0)


def test_feathered_mask_shape_and_range() -> None:
    mask = feathered_mask(128, 96, (0.2, 0.1, 0.8, 0.9), 8)
    assert mask.shape == (128, 96, 1)
    assert mask.min() >= -1e-6 and mask.max() <= 1.0 + 1e-6
    assert mask[64, 48, 0] == pytest.approx(1.0)
    assert mask[0, 0, 0] == pytest.approx(0.0)


def test_pingpong_filter_graph_structure() -> None:
    graph = pingpong_filter_graph(3, 12.5)
    assert graph.startswith("[0:v]split=3[s0][s1][s2]")
    assert "[s1]reverse[r1]" in graph
    assert "[s2]" in graph and "[s2]reverse" not in graph
    assert "concat=n=3:v=1:a=0[cyc]" in graph
    assert "trim=duration=12.500000[out]" in graph
    odd = pingpong_filter_graph(5, 1.0)
    assert "[s1]reverse[r1]" in odd and "[s3]reverse[r3]" in odd
    assert "[s2]reverse" not in odd and "[s4]reverse" not in odd


def test_stabilize_roundtrip_reduces_flash(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("本机无 ffmpeg")
    width, height, frames = 96, 128, 40
    source = tmp_path / "flash.avi"
    writer = cv2.VideoWriter(
        str(source), cv2.VideoWriter_fourcc(*"mp4v"), 30, (width, height)
    )
    rng = np.random.default_rng(7)
    for index in range(frames):
        frame = np.full((height, width, 3), (110, 130, 120), dtype=np.uint8)
        frame[16:96, 16:80] = (150, 135, 125)  # “脸”
        # 每 6 帧制造一次 +10 亮度闪变。
        if index % 6 == 0:
            flash = frame.copy()
            flash[16:96, 16:80] = np.clip(flash[16:96, 16:80].astype(int) + 10, 0, 255).astype(np.uint8)
            frame = flash
        frame = frame.astype(np.int16)
        frame += rng.integers(-2, 3, frame.shape)  # 轻微噪声
        writer.write(np.clip(frame, 0, 255).astype(np.uint8))
    writer.release()

    def face_luma_std(path: Path) -> float:
        cap = cv2.VideoCapture(str(path))
        values = []
        while True:
            ok, item = cap.read()
            if not ok:
                break
            values.append(cv2.cvtColor(item, cv2.COLOR_BGR2YCrCb)[24:88, 28:68, 0].mean())
        cap.release()
        return float(np.std(values))

    output = tmp_path / "stable.mp4"
    stabilize_face_tone(
        source,
        output,
        fps=30,
        ffmpeg=ffmpeg,
        ema=0.9,
        face_box=(0.2, 0.15, 0.8, 0.7),
        feather=6,
        max_delta=6.0,
    )
    assert output.is_file()
    assert face_luma_std(output) < 0.6 * face_luma_std(source)
