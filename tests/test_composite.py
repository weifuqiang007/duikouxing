import numpy as np

from digital_human.composite import _dynamic_texture_mask, _fallback_face_box
from digital_human.config import MouthROI


def test_dynamic_texture_mask_excludes_mouth_center() -> None:
    base = np.zeros((200, 200, 3), dtype=np.uint8)
    generated = np.full_like(base, 30)
    face_box = (50, 20, 100, 140)

    mask = _dynamic_texture_mask(
        base,
        generated,
        face_box,
        feather_pixels=0,
        change_threshold=2.5,
    )

    # 嘴心为 MuseTalk 专用区，嘴旁下半脸皮肤可回填纹理。
    assert mask[121, 100, 0] == 0.0
    assert mask[110, 65, 0] == 1.0


def test_fallback_face_box_stays_inside_frame() -> None:
    roi = MouthROI(center_x=0.98, center_y=0.98, width=0.02, height=0.02, feather_pixels=4)
    x, y, width, height = _fallback_face_box(320, 240, roi)
    assert x >= 0 and y >= 0
    assert x + width <= 320
    assert y + height <= 240
