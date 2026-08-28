from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .config import ConfigurationError, IdCardConfig, PolygonRegion


class IdCardReplacementError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _shoelace_area(corners: list[tuple[float, float]]) -> float:
    """Signed area via shoelace formula; negative means CW winding."""
    n = len(corners)
    area = 0.0
    for i in range(n):
        x1, y1 = corners[i]
        x2, y2 = corners[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return area / 2.0


def _validate_corners(corners: list[tuple[float, float]]) -> None:
    if len(corners) != 4:
        raise ConfigurationError(
            f"corners must have exactly 4 points, got {len(corners)}"
        )
    for i, (x, y) in enumerate(corners):
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ConfigurationError(
                f"corners[{i}] = ({x}, {y}) out of [0, 1] range"
            )
    if abs(_shoelace_area(corners)) < 1e-6:
        raise ConfigurationError("corners form a degenerate (near-zero area) quadrilateral")


def _validate_polygon(poly: PolygonRegion) -> None:
    if len(poly.points) < 3:
        raise ConfigurationError(
            f"protect_polygon '{poly.name}' must have at least 3 points, "
            f"got {len(poly.points)}"
        )
    for i, (x, y) in enumerate(poly.points):
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ConfigurationError(
                f"protect_polygon '{poly.name}' point {i} = ({x}, {y}) "
                f"out of [0, 1] range"
            )


# ---------------------------------------------------------------------------
# Image / coordinate helpers
# ---------------------------------------------------------------------------

def _read_image_safe(path: Path) -> np.ndarray:
    """Read image supporting Chinese paths on Windows."""
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise IdCardReplacementError(f"cannot read image: {path}")
    return image


def _normalized_to_pixels(
    points: list[tuple[float, float]], width: int, height: int
) -> np.ndarray:
    """Convert normalized [0,1] coordinates to pixel coordinates.

    Returns float32 array of shape (N, 2).
    """
    return np.array(
        [[x * width, y * height] for x, y in points], dtype=np.float32
    )


# ---------------------------------------------------------------------------
# Perspective warp
# ---------------------------------------------------------------------------

def _perspective_warp(
    card_image: np.ndarray, M: np.ndarray, frame_size: tuple[int, int]
) -> tuple[np.ndarray, np.ndarray]:
    """Warp card image and its full-white mask by perspective matrix.

    Parameters
    ----------
    frame_size : (width, height) of the output frame.

    Returns (warped_card, card_mask) both as float32, same size as frame.
    card_mask is 0-1 float in (H, W), warped_card is BGR float32.
    """
    w, h = frame_size
    full_mask = np.ones((card_image.shape[0], card_image.shape[1]), dtype=np.uint8) * 255
    warped = cv2.warpPerspective(card_image, M, (w, h))
    mask = cv2.warpPerspective(full_mask, M, (w, h))
    return warped.astype(np.float32), mask.astype(np.float32) / 255.0


# ---------------------------------------------------------------------------
# Auto finger detection via LAB color difference
# ---------------------------------------------------------------------------


def _auto_detect_non_card(
    frame_bgr: np.ndarray,
    card_mask: np.ndarray,
    card_corners_px: np.ndarray,
) -> np.ndarray:
    """Detect non-card pixels (fingers, background) inside the card region.

    Uses LAB color difference: card surface is white/light gray, fingers are
    skin-toned. Samples center 20% of card bbox as reference, thresholds by
    LAB Euclidean distance, then morphology + CC filtering.

    Returns float32 (H, W) mask: 1 = non-card (protect), 0 = card surface.
    """
    h, w = frame_bgr.shape[:2]
    lab = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)

    xs = card_corners_px[:, 0]
    ys = card_corners_px[:, 1]
    x_min = int(max(0, xs.min()))
    x_max = int(min(w, xs.max()))
    y_min = int(max(0, ys.min()))
    y_max = int(min(h, ys.max()))

    cx0 = x_min + int((x_max - x_min) * 0.4)
    cx1 = x_min + int((x_max - x_min) * 0.6)
    cy0 = y_min + int((y_max - y_min) * 0.4)
    cy1 = y_min + int((y_max - y_min) * 0.6)
    center = lab[cy0:cy1, cx0:cx1]
    if center.size == 0:
        return np.zeros((h, w), dtype=np.float32)
    ref = np.median(center.reshape(-1, 3), axis=0)

    diff = lab - ref[None, None, :]
    dist = np.sqrt(np.sum(diff ** 2, axis=2))

    card_pixels = dist[card_mask > 0.5]
    if card_pixels.size < 100:
        return np.zeros((h, w), dtype=np.float32)
    threshold = float(np.percentile(card_pixels, 75)) * 1.8
    threshold = max(threshold, 30.0)

    non_card = (dist > threshold).astype(np.uint8)
    non_card = (non_card & (card_mask > 0.5)).astype(np.uint8)

    kernel_c = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    kernel_o = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    non_card = cv2.morphologyEx(non_card, cv2.MORPH_CLOSE, kernel_c)
    non_card = cv2.morphologyEx(non_card, cv2.MORPH_OPEN, kernel_o)

    card_area = float(np.sum(card_mask > 0.5))
    min_area = card_area * 0.005
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
        non_card, connectivity=8
    )
    filtered = np.zeros_like(non_card)
    for i in range(1, n_labels):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            filtered[labels == i] = 255
    non_card = filtered

    return non_card.astype(np.float32) / 255.0


# ---------------------------------------------------------------------------
# Protect mask (manual polygons; auto-detect added later)
# ---------------------------------------------------------------------------

def _build_protect_mask(
    frame: np.ndarray,
    card_mask: np.ndarray,
    protect_polygons: list[PolygonRegion],
    frame_w: int,
    frame_h: int,
    auto_detect: bool = False,  # noqa: FBT001
) -> np.ndarray:
    """Build mask for regions that must NOT be replaced (fingers, occlusions).

    Returns float32 (H, W) mask: 1 = protect (keep original), 0 = ok to replace.
    """
    protect = np.zeros((frame_h, frame_w), dtype=np.float32)
    for poly in protect_polygons:
        pts = _normalized_to_pixels(poly.points, frame_w, frame_h)
        cv2.fillPoly(protect, [pts.astype(np.int32)], 1.0)
    # Auto-detect non-card pixels via LAB color difference.
    if auto_detect:
        ys, xs = np.where(card_mask > 0.5)
        if xs.size > 0:
            x0, x1 = int(xs.min()), int(xs.max())
            y0, y1 = int(ys.min()), int(ys.max())
            corners_approx = np.array([
                [x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float32
            )
            auto_mask = _auto_detect_non_card(frame, card_mask, corners_approx)
            protect = np.maximum(protect, auto_mask)

    # Intersect with card_mask: only protect pixels inside the card region.
    protect = np.minimum(protect, card_mask)
    return protect


# ---------------------------------------------------------------------------
# LAB local color matching
# ---------------------------------------------------------------------------

def _lab_local_color_match(
    warped: np.ndarray,
    original: np.ndarray,
    replace_mask: np.ndarray,
    cm_config: dict[str, Any] | None = None,
) -> np.ndarray:
    """Adjust warped card brightness/color to match original frame's card region.

    Operates in LAB space. Clamped mean-shift per channel.
    Optional low-frequency shadow transfer.
    """
    if cm_config is None:
        cm_config = {}

    exposure_clip = int(cm_config.get("exposure_clip", 25))
    chroma_clip = int(cm_config.get("chroma_clip", 10))
    shadow_transfer = bool(cm_config.get("shadow_transfer", True))
    sample_erode = int(cm_config.get("sample_erode_pixels", 4))

    result = warped.copy()

    # Build erosion-safe sample mask: avoid card edges where warp artifacts live.
    sample_mask = replace_mask.copy()
    if sample_erode > 0:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (sample_erode * 2 + 1, sample_erode * 2 + 1)
        )
        sample_mask = cv2.erode(sample_mask, kernel)

    sample_mask_bool = sample_mask > 0.5
    sample_count = int(np.sum(sample_mask_bool))

    if sample_count < 500:
        # Too few pixels for reliable multi-channel match; do simple L shift only.
        if sample_count < 10:
            return result
        lab_orig = cv2.cvtColor(original, cv2.COLOR_BGR2LAB).astype(np.float32)
        lab_warped = cv2.cvtColor(result, cv2.COLOR_BGR2LAB).astype(np.float32)
        delta_l = (
            np.mean(lab_orig[:, :, 0][sample_mask_bool])
            - np.mean(lab_warped[:, :, 0][sample_mask_bool])
        )
        lab_warped[:, :, 0] = np.clip(
            lab_warped[:, :, 0] + np.clip(delta_l, -exposure_clip, exposure_clip),
            0, 255,
        )
        return cv2.cvtColor(lab_warped.astype(np.uint8), cv2.COLOR_LAB2BGR).astype(
            np.float32
        )

    lab_orig = cv2.cvtColor(original, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab_warped = cv2.cvtColor(result, cv2.COLOR_BGR2LAB).astype(np.float32)

    clips = [exposure_clip, chroma_clip, chroma_clip]
    for ch in range(3):
        orig_mean = np.mean(lab_orig[:, :, ch][sample_mask_bool])
        warp_mean = np.mean(lab_warped[:, :, ch][sample_mask_bool])
        delta = orig_mean - warp_mean
        delta = np.clip(delta, -clips[ch], clips[ch])
        lab_warped[:, :, ch] = np.clip(lab_warped[:, :, ch] + delta, 0, 255)

    result = cv2.cvtColor(lab_warped.astype(np.uint8), cv2.COLOR_LAB2BGR).astype(np.float32)

    # Optional: low-frequency shadow transfer.
    if shadow_transfer:
        low_orig = cv2.GaussianBlur(original, (0, 0), sigmaX=25)
        low_warped = cv2.GaussianBlur(result, (0, 0), sigmaX=25)
        shadow_delta = low_orig.astype(np.float32) - low_warped
        result = np.clip(result + shadow_delta, 0, 255)

    return result


# ---------------------------------------------------------------------------
# Single-frame replacement
# ---------------------------------------------------------------------------

def replace_id_card_in_frame(
    frame: np.ndarray,
    card_image: np.ndarray,
    *,
    corners: list[tuple[float, float]],
    protect_polygons: list[PolygonRegion] | None = None,
    feather_pixels: int = 2,
    color_match: dict[str, Any] | None = None,
    auto_detect_fingers: bool = False,
) -> np.ndarray:
    """Replace card region in a single frame.

    Parameters
    ----------
    frame : np.ndarray
        Source frame (BGR, uint8) — typically from facefusion_result.mp4.
    card_image : np.ndarray
        New card image (BGR, uint8) to paste.
    corners : list of 4 (x, y) tuples
        Normalized [0, 1] card corners in order: TL, TR, BR, BL.
    protect_polygons : list of PolygonRegion
        Regions (fingers) to preserve from the original frame.
    feather_pixels : int
        Edge feathering radius (0 = hard mask).
    color_match : dict
        LAB color matching parameters.
    auto_detect_fingers : bool
        If True, auto-detect non-card pixels via LAB color difference.

    Returns
    -------
    np.ndarray : composited frame (BGR, uint8).
    """
    if protect_polygons is None:
        protect_polygons = []

    _validate_corners(corners)
    for poly in protect_polygons:
        _validate_polygon(poly)

    frame_h, frame_w = frame.shape[:2]

    # 1. Perspective transform.
    src_quad = np.array(
        [
            [0, 0],
            [card_image.shape[1] - 1, 0],
            [card_image.shape[1] - 1, card_image.shape[0] - 1],
            [0, card_image.shape[0] - 1],
        ],
        dtype=np.float32,
    )
    dst_quad = _normalized_to_pixels(corners, frame_w, frame_h)
    M = cv2.getPerspectiveTransform(src_quad, dst_quad)
    warped_card, card_mask = _perspective_warp(card_image, M, (frame_w, frame_h))

    # 2. Build protect mask.
    protect_mask = _build_protect_mask(
        frame, card_mask, protect_polygons, frame_w, frame_h, auto_detect_fingers
    )

    # 3. Final replace mask = card area minus protected area.
    replace_mask = np.clip(card_mask - protect_mask, 0, 1)

    # 4. Edge feathering (small — identity card text must stay sharp).
    if feather_pixels > 0:
        kernel_size = feather_pixels * 2 + 1
        alpha = cv2.GaussianBlur(
            replace_mask, (kernel_size, kernel_size), 0
        )
    else:
        alpha = replace_mask

    # 5. Color match warped card to original frame's card region.
    if color_match is not None:
        warped_card = _lab_local_color_match(
            warped_card, frame, replace_mask, color_match
        )

    # 6. Alpha composite.
    alpha_3ch = alpha[:, :, None]
    result = (
        frame.astype(np.float32) * (1.0 - alpha_3ch)
        + warped_card * alpha_3ch
    )
    return np.clip(result, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Video-level replacement
# ---------------------------------------------------------------------------

def replace_id_card_in_video(config: IdCardConfig) -> Path:
    """Process all frames of input_video, write output_video.

    Returns the output path.
    """
    card_image = _read_image_safe(config.source_image)

    cap = cv2.VideoCapture(str(config.input_video))
    if not cap.isOpened():
        raise IdCardReplacementError(
            f"cannot open video: {config.input_video}"
        )
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    if width <= 0 or height <= 0:
        raise IdCardReplacementError(
            f"cannot read video dimensions: {config.input_video}"
        )

    # Reuse codec pattern from composite.py.
    suffix = config.output_video.suffix.lower()
    codec = "FFV1" if suffix in {".mkv", ".avi"} else "mp4v"
    config.output_video.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(config.output_video),
        cv2.VideoWriter_fourcc(*codec),
        float(fps),
        (width, height),
    )
    if not writer.isOpened():
        raise IdCardReplacementError(
            f"cannot create output video: {config.output_video}"
        )

    cm = config.color_match
    frame_count = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            result = replace_id_card_in_frame(
                frame,
                card_image,
                corners=config.corners,
                protect_polygons=config.protect_polygons,
                feather_pixels=config.feather_pixels,
                color_match=cm,
                auto_detect_fingers=config.auto_detect_fingers,
            )
            writer.write(result)
            frame_count += 1
    finally:
        cap.release()
        writer.release()

    if frame_count == 0:
        raise IdCardReplacementError("input video has 0 frames")

    return config.output_video


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def preview_id_card(video, config, output_path):
    from .composite import grab_frame

    frame = grab_frame(video)
    h, w = frame.shape[:2]

    pts = _normalized_to_pixels(config.corners, w, h).astype(np.int32)
    cv2.polylines(frame, [pts], isClosed=True, color=(0, 255, 255), thickness=2)
    for i, (x, y) in enumerate(pts):
        cv2.circle(frame, (int(x), int(y)), 5, (0, 0, 255), -1)
        cv2.putText(
            frame, str(i + 1), (int(x) + 8, int(y) - 8),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1,
        )

    for poly in config.protect_polygons:
        poly_pts = _normalized_to_pixels(poly.points, w, h).astype(np.int32)
        cv2.fillPoly(frame, [poly_pts], (0, 0, 180))
        cv2.polylines(frame, [poly_pts], isClosed=True, color=(0, 0, 255), thickness=1)
        cx = int(poly_pts[:, 0].mean())
        cy = int(poly_pts[:, 1].mean())
        cv2.putText(
            frame, poly.name, (cx, cy),
            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), frame):
        raise RuntimeError(f"cannot write preview: {output_path}")
