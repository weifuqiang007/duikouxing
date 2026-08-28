import tempfile

import cv2
import numpy as np
import pytest

from digital_human.config import ConfigurationError
from digital_human.config import IdCardConfig, PolygonRegion
from digital_human.id_card import (
    IdCardReplacementError,
    _build_protect_mask,
    _normalized_to_pixels,
    _perspective_warp,
    _read_image_safe,
    _validate_corners,
    _validate_polygon,
    replace_id_card_in_frame,
    replace_id_card_in_video,
)


# ---- helpers ----


def _make_frame(w: int = 320, h: int = 240, color: tuple = (80, 90, 100)) -> np.ndarray:
    return np.full((h, w, 3), color, dtype=np.uint8)


def _make_card(w: int = 200, h: int = 130, color: tuple = (200, 210, 220)) -> np.ndarray:
    return np.full((h, w, 3), color, dtype=np.uint8)


def _axis_aligned_corners(margin_x: float, margin_y: float, right_x: float, bottom_y: float):
    return [
        (margin_x, margin_y),
        (right_x, margin_y),
        (right_x, bottom_y),
        (margin_x, bottom_y),
    ]


# ---- 1. perspective warp ----


def test_perspective_warp_places_card_in_quadrilateral() -> None:
    frame = _make_frame(320, 240, (80, 90, 100))
    card = _make_card(200, 130, (200, 210, 220))
    corners = [
        (0.1, 0.1),
        (0.85, 0.15),
        (0.80, 0.85),
        (0.15, 0.80),
    ]
    result = replace_id_card_in_frame(
        frame, card, corners=corners, feather_pixels=0
    )
    # Check center of card region is from the card image, not the frame.
    cx, cy = int(320 * 0.48), int(240 * 0.48)
    # The card is (200,210,220), frame is (80,90,100).
    assert result[cy, cx, 0] > 150  # R channel closer to card's 200


# ---- 2. protect polygon preserves original pixels ----


def test_protect_polygon_preserves_original_pixels() -> None:
    frame = _make_frame(320, 240, (80, 90, 100))
    card = _make_card(200, 130, (200, 210, 220))
    corners = _axis_aligned_corners(0.05, 0.05, 0.95, 0.90)
    protect = PolygonRegion(
        name="test_finger",
        points=[(0.10, 0.20), (0.15, 0.20), (0.15, 0.40), (0.10, 0.40)],
    )
    result = replace_id_card_in_frame(
        frame, card, corners=corners, protect_polygons=[protect], feather_pixels=0
    )
    # Center of protect polygon should retain original pixel values.
    px, py = int(320 * 0.125), int(240 * 0.30)
    np.testing.assert_array_equal(result[py, px], frame[py, px])


# ---- 3. replace mask outside unchanged ----


def test_replace_mask_outside_unchanged() -> None:
    frame = _make_frame(320, 240, (80, 90, 100))
    card = _make_card(200, 130, (200, 210, 220))
    corners = _axis_aligned_corners(0.2, 0.2, 0.8, 0.8)
    result = replace_id_card_in_frame(
        frame, card, corners=corners, feather_pixels=0
    )
    # Top-left corner (0, 0) is well outside card area.
    np.testing.assert_array_equal(result[0, 0], frame[0, 0])
    # Bottom-right corner too.
    np.testing.assert_array_equal(result[239, 319], frame[239, 319])


# ---- 4. feather zero hard edge ----


def test_feather_zero_hard_edge() -> None:
    frame = _make_frame(320, 240, (0, 0, 0))
    card = _make_card(200, 130, (255, 255, 255))
    corners = _axis_aligned_corners(0.1, 0.1, 0.9, 0.9)
    result = replace_id_card_in_frame(
        frame, card, corners=corners, feather_pixels=0
    )
    # At the boundary, pixels should be either 0 or 255, no in-between.
    edge_y = int(240 * 0.1)  # top edge of card
    edge_x = int(320 * 0.1)  # left edge
    # Just above the top edge — should be close to original (0), not card (255).
    assert result[edge_y - 1, edge_x + 10, 0] < 128
    # Just below — should be close to card (255), not original (0).
    assert result[edge_y + 1, edge_x + 10, 0] > 128


# ---- 5. feather two smooth alpha ----


def test_feather_two_smooth_alpha() -> None:
    frame = _make_frame(640, 480, (0, 0, 0))
    card = _make_card(400, 250, (255, 255, 255))
    corners = _axis_aligned_corners(0.2, 0.2, 0.8, 0.8)
    result = replace_id_card_in_frame(
        frame, card, corners=corners, feather_pixels=2
    )
    # At the card edge, there should be a smooth transition, not a hard 0→255 jump.
    edge_y = int(480 * 0.2)
    col = int(640 * 0.5)  # middle of card horizontally
    vals = [result[edge_y + dy, col, 0] for dy in range(-2, 5)]
    # Should not jump from 0 directly to 255 — at least one intermediate value.
    has_intermediate = any(0 < v < 255 for v in vals)
    assert has_intermediate, f"expected smooth transition, got {vals}"


# ---- 6. LAB color match L channel ----


def test_lab_color_match_l_channel() -> None:
    # Frame has a dark card area; warped card is bright.
    frame = _make_frame(640, 480, (50, 50, 50))
    card = _make_card(400, 250, (200, 200, 200))
    corners = _axis_aligned_corners(0.1, 0.1, 0.9, 0.9)
    cm = {"mode": "lab_local", "exposure_clip": 60, "chroma_clip": 40}
    result = replace_id_card_in_frame(
        frame, card, corners=corners, feather_pixels=0, color_match=cm
    )
    # After color match, center should be darker than the original 200 — closer to 50.
    cx, cy = 320, 240
    original_val = 200
    matched_val = result[cy, cx, 0]
    assert matched_val < original_val - 20, (
        f"color match should darken card: {matched_val} vs original {original_val}"
    )


# ---- 7. exposure clip limits correction ----


def test_exposure_clip_limits_correction() -> None:
    frame = _make_frame(640, 480, (30, 30, 30))
    card = _make_card(400, 250, (255, 255, 255))
    corners = _axis_aligned_corners(0.1, 0.1, 0.9, 0.9)
    # Very tight clip should prevent full correction.
    cm = {"mode": "lab_local", "exposure_clip": 10, "chroma_clip": 5, "shadow_transfer": False}
    result = replace_id_card_in_frame(
        frame, card, corners=corners, feather_pixels=0, color_match=cm
    )
    cx, cy = 320, 240
    matched_val = result[cy, cx, 0]
    # With clip=10, max correction is -10. Card was 255, so min should be 245.
    assert matched_val >= 245, (
        f"exposure_clip should limit correction: {matched_val} >= 245"
    )


# ---- 8. invalid corner count ----


@pytest.mark.parametrize("n", [1, 2, 3, 5, 6])
def test_invalid_corner_count_raises(n: int) -> None:
    pts = [(0.5, 0.5)] * n
    with pytest.raises(ConfigurationError, match="exactly 4"):
        _validate_corners(pts)


# ---- 9. degenerate quad ----


def test_degenerate_quad_raises() -> None:
    # Collinear points.
    collinear = [(0.1, 0.5), (0.4, 0.5), (0.7, 0.5), (0.9, 0.5)]
    with pytest.raises(ConfigurationError, match="degenerate"):
        _validate_corners(collinear)


# ---- 10. protect polygon few points ----


@pytest.mark.parametrize("n", [0, 1, 2])
def test_protect_polygon_few_points_raises(n: int) -> None:
    poly = PolygonRegion(name="bad", points=[(0.5, 0.5)] * max(n, 0))
    with pytest.raises(ConfigurationError, match="at least 3"):
        _validate_polygon(poly)


# ---- 11. video output metadata ----


def test_video_output_matches_input_metadata() -> None:
    frame = _make_frame(320, 240, (100, 110, 120))
    card = _make_card(200, 130, (200, 200, 200))
    corners = _axis_aligned_corners(0.1, 0.1, 0.9, 0.9)

    with tempfile.TemporaryDirectory() as tmpdir:
        import os

        input_path = os.path.join(tmpdir, "input.mp4")
        output_path = os.path.join(tmpdir, "output.mp4")
        card_path = os.path.join(tmpdir, "card.png")

        # Write 10-frame input video.
        writer = cv2.VideoWriter(
            input_path, cv2.VideoWriter_fourcc(*"mp4v"), 25.0, (320, 240)
        )
        for _ in range(10):
            writer.write(frame)
        writer.release()

        # Write card image.
        cv2.imwrite(card_path, card)

        from pathlib import Path

        config = IdCardConfig(
            source_image=Path(card_path),
            input_video=Path(input_path),
            output_video=Path(output_path),
            corners=corners,
            protect_polygons=[],
            feather_pixels=0,
        )
        result_path = replace_id_card_in_video(config)

        cap = cv2.VideoCapture(str(result_path))
        assert int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) == 320
        assert int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) == 240
        assert cap.get(cv2.CAP_PROP_FPS) == 25.0
        count = 0
        while True:
            ok, _ = cap.read()
            if not ok:
                break
            count += 1
        cap.release()
        assert count == 10


# ---- 12. empty video raises ----


def test_empty_video_raises() -> None:
    frame = _make_frame(320, 240, (100, 110, 120))
    card = _make_card(200, 130, (200, 200, 200))
    corners = _axis_aligned_corners(0.1, 0.1, 0.9, 0.9)

    with tempfile.TemporaryDirectory() as tmpdir:
        import os

        input_path = os.path.join(tmpdir, "empty.mp4")
        output_path = os.path.join(tmpdir, "output.mp4")
        card_path = os.path.join(tmpdir, "card.png")

        # Write a video with 0 frames — just open and close.
        writer = cv2.VideoWriter(
            input_path, cv2.VideoWriter_fourcc(*"mp4v"), 25.0, (320, 240)
        )
        writer.release()
        cv2.imwrite(card_path, card)

        from pathlib import Path

        config = IdCardConfig(
            source_image=Path(card_path),
            input_video=Path(input_path),
            output_video=Path(output_path),
            corners=corners,
            protect_polygons=[],
        )
        with pytest.raises(IdCardReplacementError, match="cannot open video"):
            replace_id_card_in_video(config)
