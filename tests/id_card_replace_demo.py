from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import cv2
import numpy as np


# ── 复用 demo 的工具函数 ──────────────────────────────────────────────
from id_card_source_mark_demo import (
    Quad,
    denormalize_points,
    normalize_points,
    order_quad_points,
    read_image_unicode,
    write_image_unicode,
)


DEFAULT_FFMPEG_PATH = Path(r"D:\ffmpeg\bin\ffmpeg.exe")


def warp_card_to_frame(
    card_bgr: np.ndarray,
    frame_shape: tuple[int, int, int],
    target_quad: Quad,
) -> tuple[np.ndarray, np.ndarray]:
    """将干净证件图透视变换到视频帧中的目标位置，返回 (warped_bgr, mask_255)。"""
    frame_h, frame_w = frame_shape[:2]
    card_h, card_w = card_bgr.shape[:2]
    src = np.array(
        [
            [0, 0],
            [card_w - 1, 0],
            [card_w - 1, card_h - 1],
            [0, card_h - 1],
        ],
        dtype=np.float32,
    )
    dst = order_quad_points(denormalize_points(target_quad, frame_w, frame_h))
    matrix = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(
        card_bgr, matrix, (frame_w, frame_h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    source_mask = np.full((card_h, card_w), 255, dtype=np.uint8)
    mask = cv2.warpPerspective(source_mask, matrix, (frame_w, frame_h), flags=cv2.INTER_NEAREST)
    return warped, mask


def quad_mask(frame_shape: tuple[int, int, int], target_quad: Quad) -> np.ndarray:
    """Build a 0/255 mask for the target card quadrilateral."""
    frame_h, frame_w = frame_shape[:2]
    mask = np.zeros((frame_h, frame_w), dtype=np.uint8)
    pts = order_quad_points(denormalize_points(target_quad, frame_w, frame_h)).astype(np.int32)
    cv2.fillPoly(mask, [pts], 255)
    return mask


def resolve_ffmpeg_path(explicit_path: Path | None = None) -> Path | None:
    """Find ffmpeg for copying the original video's audio stream back into the result."""
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(explicit_path)

    discovered = shutil.which("ffmpeg")
    if discovered:
        candidates.append(Path(discovered))
    candidates.append(DEFAULT_FFMPEG_PATH)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def mux_original_audio(
    *,
    ffmpeg_path: Path,
    video_only_path: Path,
    original_video_path: Path,
    output_path: Path,
    audio_codec: str = "aac",
) -> None:
    """Copy video from OpenCV output and audio from the original video into final mp4."""
    audio_args = ["-c:a", "copy"] if audio_codec == "copy" else ["-c:a", "aac", "-b:a", "192k"]
    command = [
        str(ffmpeg_path),
        "-y",
        "-i",
        str(video_only_path),
        "-i",
        str(original_video_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a?",
        "-c:v",
        "copy",
        *audio_args,
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode == 0:
        return

    if audio_codec != "copy":
        raise RuntimeError(f"ffmpeg 合并原视频音频失败。\nstderr:\n{result.stderr}")

    # copy 模式下部分音频编码不能直接进 mp4，失败时只重编码音频，视频仍然 copy。
    fallback_command = [
        str(ffmpeg_path),
        "-y",
        "-i",
        str(video_only_path),
        "-i",
        str(original_video_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a?",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    fallback = subprocess.run(fallback_command, capture_output=True, text=True)
    if fallback.returncode != 0:
        raise RuntimeError(
            "ffmpeg 合并原视频音频失败。\n"
            f"copy stderr:\n{result.stderr}\n"
            f"aac fallback stderr:\n{fallback.stderr}"
        )


def rectify_frame_region(
    image_bgr: np.ndarray,
    target_quad: Quad,
    output_size: tuple[int, int],
) -> np.ndarray:
    """将视频帧里的旧证件区域反透视拉正到新证件图同尺寸。"""
    frame_h, frame_w = image_bgr.shape[:2]
    out_w, out_h = output_size
    src = order_quad_points(denormalize_points(target_quad, frame_w, frame_h))
    dst = np.array(
        [
            [0, 0],
            [out_w - 1, 0],
            [out_w - 1, out_h - 1],
            [0, out_h - 1],
        ],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(
        image_bgr,
        matrix,
        (out_w, out_h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def flat_card_sample_mask(
    image_bgr: np.ndarray,
    *,
    erode_pixels: int = 10,
    edge_dilate_pixels: int = 5,
) -> np.ndarray:
    """找证件里的平坦纸面区域，避开文字、头像、边缘，用于估计真实纸面颜色。"""
    h, w = image_bgr.shape[:2]
    mask = np.full((h, w), 255, dtype=np.uint8)
    if erode_pixels > 0:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (erode_pixels * 2 + 1, erode_pixels * 2 + 1),
        )
        mask = cv2.erode(mask, kernel)

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 35, 120)
    if edge_dilate_pixels > 0:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (edge_dilate_pixels * 2 + 1, edge_dilate_pixels * 2 + 1),
        )
        edges = cv2.dilate(edges, kernel)
    mask[edges > 0] = 0
    return mask


def masked_lab_mean_std(lab: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    idx = mask > 0
    count = int(idx.sum())
    if count == 0:
        return np.zeros(3, dtype=np.float32), np.ones(3, dtype=np.float32), 0
    pixels = lab[idx]
    return pixels.mean(axis=0), pixels.std(axis=0), count


def match_clean_card_to_original_card(
    clean_card_bgr: np.ndarray,
    original_card_bgr: np.ndarray,
    *,
    exposure_clip: float = 90,
    chroma_clip: float = 35,
    style_strength: float = 0.95,
    sample_erode_pixels: int = 10,
    match_l_std: bool = True,
    background_light_strength: float = 0.9,
    background_light_sigma: float = 52,
    shadow_clip: float = 45,
    whole_card_balance_strength: float = 0.85,
    whole_card_balance_l_clip: float = 18,
    whole_card_balance_chroma_clip: float = 12,
    match_ab_std: bool = True,
    ab_std_strength: float = 0.75,
    paper_texture: bool = True,
    texture_sigma: float = 1.8,
    texture_strength: float = 0.3,
    texture_clip: float = 6.0,
    match_blur: bool = True,
    verbose: bool = False,
) -> np.ndarray:
    """
    在证件正视图坐标系里，把新证件调成旧视频证件的成像状态。

    这里不拷贝旧证件的文字/照片细节，只取全局 LAB、低频光照和很轻的纸面纹理。
    """
    if original_card_bgr.shape[:2] != clean_card_bgr.shape[:2]:
        original_card_bgr = cv2.resize(
            original_card_bgr,
            (clean_card_bgr.shape[1], clean_card_bgr.shape[0]),
            interpolation=cv2.INTER_CUBIC,
        )

    clean_lab = cv2.cvtColor(clean_card_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    old_lab = cv2.cvtColor(original_card_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)

    clean_flat = flat_card_sample_mask(clean_card_bgr, erode_pixels=sample_erode_pixels)
    old_flat = flat_card_sample_mask(original_card_bgr, erode_pixels=sample_erode_pixels)
    sample = cv2.bitwise_and(clean_flat, old_flat)
    if int((sample > 0).sum()) < clean_card_bgr.shape[0] * clean_card_bgr.shape[1] * 0.05:
        sample = clean_flat
    if int((sample > 0).sum()) < clean_card_bgr.shape[0] * clean_card_bgr.shape[1] * 0.02:
        sample = np.full(clean_card_bgr.shape[:2], 255, dtype=np.uint8)

    old_mean, old_std, sample_count = masked_lab_mean_std(old_lab, sample)
    clean_mean, clean_std, _ = masked_lab_mean_std(clean_lab, sample)
    delta = old_mean - clean_mean
    delta[0] = np.clip(delta[0], -exposure_clip, exposure_clip)
    delta[1] = np.clip(delta[1], -chroma_clip, chroma_clip)
    delta[2] = np.clip(delta[2], -chroma_clip, chroma_clip)

    styled_lab = clean_lab + delta[None, None, :] * style_strength

    if verbose:
        print(
            "  旧证件风格采样："
            f"pixels={sample_count}, delta L={delta[0]:.1f}, a={delta[1]:.1f}, b={delta[2]:.1f}"
        )

    if match_l_std:
        idx = sample > 0
        clean_l_std = float(styled_lab[idx, 0].std())
        old_l_std = float(old_lab[idx, 0].std())
        if clean_l_std > 1.0 and old_l_std > 1.0:
            ratio = np.clip(old_l_std / clean_l_std, 0.55, 1.2)
            l_mean = float(styled_lab[idx, 0].mean())
            styled_lab[:, :, 0] = (styled_lab[:, :, 0] - l_mean) * ratio + l_mean

    if background_light_strength > 0:
        clean_edges = 255 - clean_flat
        old_edges = 255 - old_flat
        edge_protect = np.maximum(clean_edges, old_edges).astype(np.float32) / 255.0
        flat_weight = 1.0 - edge_protect
        flat_weight = cv2.GaussianBlur(flat_weight, (0, 0), 2.5)

        old_low = cv2.GaussianBlur(old_lab, (0, 0), background_light_sigma)
        styled_low = cv2.GaussianBlur(styled_lab, (0, 0), background_light_sigma)
        low_delta = np.clip(old_low - styled_low, -shadow_clip, shadow_clip)
        styled_lab += low_delta * flat_weight[..., None] * background_light_strength

    if paper_texture:
        old_l = old_lab[:, :, 0]
        styled_l = styled_lab[:, :, 0]
        old_hpf = old_l - cv2.GaussianBlur(old_l, (0, 0), texture_sigma)
        styled_hpf = styled_l - cv2.GaussianBlur(styled_l, (0, 0), texture_sigma)
        flat_weight = (sample.astype(np.float32) / 255.0)
        flat_weight = cv2.GaussianBlur(flat_weight, (0, 0), 1.0)
        texture = np.clip(old_hpf - styled_hpf, -texture_clip, texture_clip)
        styled_lab[:, :, 0] += texture * flat_weight * texture_strength

    if whole_card_balance_strength > 0:
        h, w = clean_card_bgr.shape[:2]
        inner = np.full((h, w), 255, dtype=np.uint8)
        inner_erode = max(2, sample_erode_pixels // 2)
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (inner_erode * 2 + 1, inner_erode * 2 + 1),
        )
        inner = cv2.erode(inner, kernel)
        old_whole_mean, _, whole_count = masked_lab_mean_std(old_lab, inner)
        styled_whole_mean, _, _ = masked_lab_mean_std(styled_lab, inner)
        whole_delta = old_whole_mean - styled_whole_mean
        whole_delta[0] = np.clip(
            whole_delta[0],
            -whole_card_balance_l_clip,
            whole_card_balance_l_clip,
        )
        whole_delta[1] = np.clip(
            whole_delta[1],
            -whole_card_balance_chroma_clip,
            whole_card_balance_chroma_clip,
        )
        whole_delta[2] = np.clip(
            whole_delta[2],
            -whole_card_balance_chroma_clip,
            whole_card_balance_chroma_clip,
        )
        styled_lab += whole_delta[None, None, :] * whole_card_balance_strength
        if verbose:
            print(
                "  整卡均值锁定："
                f"pixels={whole_count}, delta L={whole_delta[0]:.1f}, "
                f"a={whole_delta[1]:.1f}, b={whole_delta[2]:.1f}"
            )

    if match_ab_std:
        h, w = clean_card_bgr.shape[:2]
        inner = np.full((h, w), 255, dtype=np.uint8)
        inner_erode = max(2, sample_erode_pixels // 2)
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (inner_erode * 2 + 1, inner_erode * 2 + 1),
        )
        inner = cv2.erode(inner, kernel)
        old_mean, old_std, _ = masked_lab_mean_std(old_lab, inner)
        styled_mean, styled_std, _ = masked_lab_mean_std(styled_lab, inner)
        for channel in (1, 2):
            if styled_std[channel] > 0.5 and old_std[channel] > 0.5:
                ratio = np.clip(old_std[channel] / styled_std[channel], 0.45, 1.0)
                ratio = 1.0 + (ratio - 1.0) * ab_std_strength
                styled_lab[:, :, channel] = (
                    (styled_lab[:, :, channel] - styled_mean[channel]) * ratio
                    + old_mean[channel]
                )

    result = cv2.cvtColor(np.clip(styled_lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
    if match_blur:
        full_mask = np.full(clean_card_bgr.shape[:2], 255, dtype=np.uint8)
        result = match_card_video_blur(original_card_bgr, result, full_mask, max_sigma=0.9)
    return result


def normalized_blur_field(values: np.ndarray, mask: np.ndarray, sigma: float) -> np.ndarray:
    """Blur values inside a mask without black pixels outside the mask polluting the result."""
    weight = (mask.astype(np.float32) / 255.0)[..., None]
    numerator = cv2.GaussianBlur(values.astype(np.float32) * weight, (0, 0), sigma)
    denominator = cv2.GaussianBlur(weight, (0, 0), sigma)
    if denominator.ndim == 2:
        denominator = denominator[..., None]
    return numerator / np.maximum(denominator, 1e-4)


def masked_laplacian_variance(image_bgr: np.ndarray, mask: np.ndarray) -> float:
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    lap = cv2.Laplacian(gray, cv2.CV_32F)
    idx = mask > 0
    if int(idx.sum()) < 100:
        return 0.0
    return float(lap[idx].var())


def match_card_video_blur(
    original_bgr: np.ndarray,
    card_bgr: np.ndarray,
    replace_mask: np.ndarray,
    *,
    max_sigma: float = 0.75,
) -> np.ndarray:
    """Reduce the pasted card's too-clean sharpness when it exceeds the video card sharpness."""
    src_var = masked_laplacian_variance(original_bgr, replace_mask)
    card_var = masked_laplacian_variance(card_bgr, replace_mask)
    if src_var <= 1.0 or card_var <= src_var * 1.25:
        return card_bgr
    ratio = min(card_var / max(src_var, 1.0), 6.0)
    sigma = min(max_sigma, 0.22 * (ratio - 1.0) ** 0.5)
    blurred = cv2.GaussianBlur(card_bgr, (0, 0), sigma)
    alpha = (replace_mask.astype(np.float32) / 255.0)[..., None]
    return np.clip(card_bgr.astype(np.float32) * (1 - alpha) + blurred.astype(np.float32) * alpha, 0, 255).astype(np.uint8)


def should_skip_replacement(
    frame_bgr: np.ndarray,
    target_quad: Quad,
    *,
    whole_frame_luma_min: float = 12.0,
    target_luma_min: float = 18.0,
) -> tuple[bool, str]:
    """Skip replacement when the video has cut to black or the marked target is no longer visible."""
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    whole_mean = float(gray.mean())
    if whole_mean < whole_frame_luma_min:
        return True, f"skip dark-frame mean={whole_mean:.1f}"

    mask = quad_mask(frame_bgr.shape, target_quad)
    idx = mask > 0
    if int(idx.sum()) < 100:
        return True, "skip tiny-target-mask"

    target_mean = float(gray[idx].mean())
    if target_mean < target_luma_min:
        return True, f"skip dark-target mean={target_mean:.1f}"

    return False, f"visible target_mean={target_mean:.1f}"


def match_card_appearance(
    frame_bgr: np.ndarray,
    warped_card_bgr: np.ndarray,
    replace_mask: np.ndarray,
    **kwargs: Any,
) -> np.ndarray:
    """Crop around the card before appearance matching; full-frame blur is too slow."""
    ys, xs = np.where(replace_mask > 0)
    if xs.size == 0:
        return warped_card_bgr

    max_sigma = max(
        float(kwargs.get("shadow_sigma", 45)),
        float(kwargs.get("background_light_sigma", 38)),
    )
    pad = int(max_sigma * 2 + 16)
    h, w = replace_mask.shape[:2]
    x0 = max(0, int(xs.min()) - pad)
    x1 = min(w, int(xs.max()) + pad + 1)
    y0 = max(0, int(ys.min()) - pad)
    y1 = min(h, int(ys.max()) + pad + 1)

    result = warped_card_bgr.copy()
    crop = _match_card_appearance_crop(
        frame_bgr[y0:y1, x0:x1],
        warped_card_bgr[y0:y1, x0:x1],
        replace_mask[y0:y1, x0:x1],
        **kwargs,
    )
    result[y0:y1, x0:x1] = crop
    return result


def _match_card_appearance_crop(
    frame_bgr: np.ndarray,
    warped_card_bgr: np.ndarray,
    replace_mask: np.ndarray,
    *,
    exposure_clip: float = 60,
    chroma_clip: float = 15,
    sample_erode_pixels: int = 6,
    match_l_std: bool = True,
    shadow_transfer: bool = True,
    shadow_sigma: int = 45,
    shadow_clip: float = 28,
    background_light_strength: float = 0.65,
    background_light_sigma: float = 38,
    paper_texture: bool = True,
    texture_sigma: float = 1.6,
    texture_strength: float = 0.22,
    texture_clip: float = 5.0,
    match_blur: bool = True,
    verbose: bool = False,
) -> np.ndarray:
    """
    Match the pasted card to the original video card's imaging conditions.

    This is intentionally stronger than a global LAB mean shift:
      1. LAB mean correction for exposure / white balance.
      2. Optional L standard-deviation match for local contrast.
      3. Mask-normalized low-frequency light field transfer.
      4. Pull flat card background toward the original card's low-frequency paper color.
      5. Gentle paper grain / compression texture transfer from flat areas only.
      6. Optional blur matching so the card is not much sharper than the video.
    """
    # ── 采样区域 = erode(replace_mask) 避开边缘混色像素 ──
    sample = replace_mask.copy()
    if sample_erode_pixels > 0:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (sample_erode_pixels * 2 + 1, sample_erode_pixels * 2 + 1)
        )
        sample = cv2.erode(sample, kernel)

    sample_count = int((sample > 0).sum())
    if sample_count < 100:
        return warped_card_bgr

    # ── 转 LAB ──
    frame_lab = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    card_lab = cv2.cvtColor(warped_card_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)

    idx = sample > 0
    src_mean = frame_lab[idx].mean(axis=0)  # [L, a, b] 目标区域均值
    card_mean = card_lab[idx].mean(axis=0)  # [L, a, b] 证件区域均值
    delta = src_mean - card_mean

    if verbose:
        print(f"  颜色匹配 delta L={delta[0]:.1f}, a={delta[1]:.1f}, b={delta[2]:.1f}")
        print(f"  目标区域 L 均值={src_mean[0]:.1f}, 证件区域 L 均值={card_mean[0]:.1f}")

    # ── Step 1: 均值校正（带限幅） ──
    delta[0] = np.clip(delta[0], -exposure_clip, exposure_clip)
    delta[1] = np.clip(delta[1], -chroma_clip, chroma_clip)
    delta[2] = np.clip(delta[2], -chroma_clip, chroma_clip)

    card_lab_corrected = card_lab + delta[None, None, :]

    # ── Step 2: L 通道标准差匹配（对比度匹配） ──
    if match_l_std:
        src_std = float(frame_lab[idx, 0].std())
        card_std = float(card_lab_corrected[idx, 0].std())
        if card_std > 1.0 and src_std > 1.0:
            ratio = src_std / card_std
            ratio = np.clip(ratio, 0.7, 1.4)
            card_l_mean = float(card_lab_corrected[idx, 0].mean())
            card_lab_corrected[:, :, 0] = (
                (card_lab_corrected[:, :, 0] - card_l_mean) * ratio + card_l_mean
            )
            if verbose:
                print(f"  L std 匹配：src_std={src_std:.1f}, card_std={card_std:.1f}, ratio={ratio:.2f}")

    # ── Step 3: 阴影/曝光空间场迁移 ──
    if shadow_transfer:
        light_delta = normalized_blur_field(frame_lab - card_lab_corrected, replace_mask, shadow_sigma)
        light_delta = np.clip(light_delta, -shadow_clip, shadow_clip)
        alpha_spatial = (replace_mask.astype(np.float32) / 255.0)[..., None]
        card_lab_corrected += light_delta * alpha_spatial

    # ── Step 4: 平坦纸面底色更贴原视频，避开新证件文字/头像强边缘 ──
    if background_light_strength > 0:
        corrected_u8 = cv2.cvtColor(
            np.clip(card_lab_corrected, 0, 255).astype(np.uint8),
            cv2.COLOR_LAB2BGR,
        )
        card_edges = cv2.Canny(cv2.cvtColor(corrected_u8, cv2.COLOR_BGR2GRAY), 40, 120)
        card_edges = cv2.dilate(
            card_edges,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
            iterations=1,
        )
        flat_card = 1.0 - card_edges.astype(np.float32) / 255.0
        flat_card = cv2.GaussianBlur(flat_card, (0, 0), 1.2)
        alpha_bg = (replace_mask.astype(np.float32) / 255.0) * flat_card
        original_low = normalized_blur_field(frame_lab, replace_mask, background_light_sigma)
        corrected_low = cv2.GaussianBlur(card_lab_corrected, (0, 0), background_light_sigma)
        bg_delta = np.clip(original_low - corrected_low, -shadow_clip, shadow_clip)
        card_lab_corrected += bg_delta * alpha_bg[..., None] * background_light_strength

    # ── Step 5: 迁移一点原视频纸面/压缩质感，避开原证件文字和头像强边缘 ──
    if paper_texture:
        original_l = frame_lab[:, :, 0]
        corrected_l = card_lab_corrected[:, :, 0]
        original_hpf = original_l - cv2.GaussianBlur(original_l, (0, 0), texture_sigma)
        card_hpf = corrected_l - cv2.GaussianBlur(corrected_l, (0, 0), texture_sigma)

        edges = cv2.Canny(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY), 45, 130)
        edges = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
        flat_weight = (replace_mask.astype(np.float32) / 255.0) * (1.0 - edges.astype(np.float32) / 255.0)
        texture = np.clip(original_hpf - card_hpf, -texture_clip, texture_clip)
        card_lab_corrected[:, :, 0] += texture * flat_weight * texture_strength

    result = cv2.cvtColor(np.clip(card_lab_corrected, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
    if match_blur:
        result = match_card_video_blur(frame_bgr, result, replace_mask)
    return result


def replace_id_card_in_frame(
    frame_bgr: np.ndarray,
    clean_card_bgr: np.ndarray,
    *,
    target_quad: Quad,
    feather_pixels: int = 2,
    color_match_options: dict[str, Any] | None = None,
) -> np.ndarray:
    """单帧替换：先学习旧证件成像状态，再透视贴图 + 羽化合成。"""
    opts = color_match_options or {}
    styled_card = clean_card_bgr
    if bool(opts.get("original_card_style_match", True)):
        card_h, card_w = clean_card_bgr.shape[:2]
        original_card = rectify_frame_region(frame_bgr, target_quad, (card_w, card_h))
        styled_card = match_clean_card_to_original_card(
            clean_card_bgr,
            original_card,
            exposure_clip=float(opts.get("exposure_clip", 90)),
            chroma_clip=float(opts.get("chroma_clip", 35)),
            style_strength=float(opts.get("style_strength", 0.95)),
            sample_erode_pixels=int(opts.get("sample_erode_pixels", 10)),
            match_l_std=bool(opts.get("match_l_std", True)),
            background_light_strength=float(opts.get("background_light_strength", 0.9)),
            background_light_sigma=float(opts.get("background_light_sigma", 52)),
            shadow_clip=float(opts.get("shadow_clip", 45)),
            whole_card_balance_strength=float(opts.get("whole_card_balance_strength", 0.85)),
            whole_card_balance_l_clip=float(opts.get("whole_card_balance_l_clip", 18)),
            whole_card_balance_chroma_clip=float(opts.get("whole_card_balance_chroma_clip", 12)),
            match_ab_std=bool(opts.get("match_ab_std", True)),
            ab_std_strength=float(opts.get("ab_std_strength", 0.75)),
            paper_texture=bool(opts.get("paper_texture", True)),
            texture_sigma=float(opts.get("texture_sigma", 1.8)),
            texture_strength=float(opts.get("texture_strength", 0.3)),
            texture_clip=float(opts.get("texture_clip", 6.0)),
            match_blur=bool(opts.get("match_blur", True)),
            verbose=bool(opts.get("verbose", False)),
        )

    warped, mask = warp_card_to_frame(styled_card, frame_bgr.shape, target_quad)
    corrected = warped
    if bool(opts.get("frame_post_match", False)):
        corrected = match_card_appearance(
            frame_bgr,
            warped,
            mask,
            exposure_clip=float(opts.get("post_exposure_clip", 25)),
            chroma_clip=float(opts.get("post_chroma_clip", 10)),
            sample_erode_pixels=int(opts.get("post_sample_erode_pixels", 6)),
            match_l_std=False,
            shadow_transfer=bool(opts.get("shadow_transfer", True)),
            shadow_sigma=int(opts.get("shadow_sigma", 45)),
            shadow_clip=float(opts.get("post_shadow_clip", 14)),
            background_light_strength=float(opts.get("post_background_light_strength", 0.25)),
            background_light_sigma=float(opts.get("post_background_light_sigma", 38)),
            paper_texture=False,
            match_blur=bool(opts.get("match_blur", True)),
            verbose=bool(opts.get("verbose", False)),
        )

    # ── Alpha 羽化合成 ──
    alpha = mask.astype(np.float32) / 255.0
    if feather_pixels > 0:
        kernel_size = feather_pixels * 2 + 1
        alpha = cv2.GaussianBlur(alpha, (kernel_size, kernel_size), 0)
    alpha3 = alpha[..., None]
    result = frame_bgr.astype(np.float32) * (1.0 - alpha3) + corrected.astype(np.float32) * alpha3
    return np.clip(result, 0, 255).astype(np.uint8)


class CardQuadTracker:
    """Track the hand-held card quad with LK optical flow on the original video frames."""

    def __init__(
        self,
        first_frame: np.ndarray,
        initial_quad: Quad,
        *,
        min_points: int = 24,
        max_points: int = 160,
        redetect_interval: int = 18,
        max_motion_px: float = 80.0,
    ) -> None:
        self.min_points = min_points
        self.max_points = max_points
        self.redetect_interval = redetect_interval
        self.max_motion_px = max_motion_px
        self.prev_gray = cv2.cvtColor(first_frame, cv2.COLOR_BGR2GRAY)
        self.frame_h, self.frame_w = first_frame.shape[:2]
        self.current_quad_px = order_quad_points(
            denormalize_points(initial_quad, self.frame_w, self.frame_h)
        )
        self.prev_points = self._detect_points(self.prev_gray, self.current_quad_px)
        self.last_status = "init"

    def _detect_points(self, gray: np.ndarray, quad_px: np.ndarray) -> np.ndarray:
        mask = np.zeros_like(gray)
        padded = self._padded_quad(quad_px, 8)
        cv2.fillPoly(mask, [padded.astype(np.int32)], 255)
        points = cv2.goodFeaturesToTrack(
            gray,
            maxCorners=self.max_points,
            qualityLevel=0.01,
            minDistance=5,
            mask=mask,
            blockSize=5,
        )
        if points is None:
            return np.empty((0, 1, 2), dtype=np.float32)
        return points.astype(np.float32)

    @staticmethod
    def _padded_quad(quad_px: np.ndarray, pixels: float) -> np.ndarray:
        center = quad_px.mean(axis=0, keepdims=True)
        vec = quad_px - center
        length = np.linalg.norm(vec, axis=1, keepdims=True)
        scale = (length + pixels) / np.maximum(length, 1.0)
        return center + vec * scale

    def _valid_quad(self, quad_px: np.ndarray, previous_quad_px: np.ndarray) -> bool:
        if quad_px.shape != (4, 2):
            return False
        if not cv2.isContourConvex(quad_px.astype(np.float32)):
            return False
        if abs(cv2.contourArea(quad_px.astype(np.float32))) < 100:
            return False
        if (
            quad_px[:, 0].min() < -20
            or quad_px[:, 1].min() < -20
            or quad_px[:, 0].max() > self.frame_w + 20
            or quad_px[:, 1].max() > self.frame_h + 20
        ):
            return False
        motion = np.linalg.norm(quad_px.mean(axis=0) - previous_quad_px.mean(axis=0))
        return bool(motion <= self.max_motion_px)

    def update(self, frame: np.ndarray, frame_index: int) -> Quad:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        previous_quad = self.current_quad_px.copy()

        if len(self.prev_points) >= self.min_points:
            next_points, status, _ = cv2.calcOpticalFlowPyrLK(
                self.prev_gray,
                gray,
                self.prev_points,
                None,
                winSize=(21, 21),
                maxLevel=3,
                criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
            )
            if next_points is not None and status is not None:
                good_old = self.prev_points[status.reshape(-1) == 1].reshape(-1, 2)
                good_new = next_points[status.reshape(-1) == 1].reshape(-1, 2)
                if len(good_old) >= self.min_points:
                    homography, inliers = cv2.findHomography(
                        good_old,
                        good_new,
                        cv2.RANSAC,
                        ransacReprojThreshold=3.0,
                    )
                    inlier_count = int(inliers.sum()) if inliers is not None else 0
                    transform_type = "H"
                    if homography is not None and inlier_count >= self.min_points // 2:
                        new_quad = cv2.perspectiveTransform(
                            previous_quad.reshape(1, -1, 2), homography
                        ).reshape(-1, 2)
                    else:
                        matrix, inliers = cv2.estimateAffinePartial2D(
                            good_old,
                            good_new,
                            method=cv2.RANSAC,
                            ransacReprojThreshold=3.0,
                        )
                        inlier_count = int(inliers.sum()) if inliers is not None else 0
                        transform_type = "A"
                        new_quad = (
                            cv2.transform(previous_quad.reshape(1, -1, 2), matrix).reshape(-1, 2)
                            if matrix is not None
                            else None
                        )

                    if new_quad is not None and inlier_count >= self.min_points // 2:
                        new_quad = order_quad_points(new_quad)
                        if self._valid_quad(new_quad, previous_quad):
                            self.current_quad_px = new_quad
                            self.prev_points = good_new.reshape(-1, 1, 2).astype(np.float32)
                            self.last_status = f"tracked-{transform_type} points={len(good_new)} inliers={inlier_count}"
                        else:
                            self.last_status = "fallback invalid-quad"
                    else:
                        self.last_status = f"fallback no-transform points={len(good_old)}"
                else:
                    self.last_status = f"fallback few-points={len(good_old)}"
            else:
                self.last_status = "fallback optical-flow-failed"
        else:
            self.last_status = f"fallback too-few-prev-points={len(self.prev_points)}"

        if (
            frame_index % self.redetect_interval == 0
            or len(self.prev_points) < self.min_points
            or self.last_status.startswith("fallback")
        ):
            redetected = self._detect_points(gray, self.current_quad_px)
            if len(redetected) >= self.min_points:
                self.prev_points = redetected
                self.last_status += f"; redetect={len(redetected)}"

        self.prev_gray = gray
        return normalize_points(self.current_quad_px, self.frame_w, self.frame_h)


def draw_tracked_quad(frame_bgr: np.ndarray, quad: Quad, label: str) -> np.ndarray:
    output = frame_bgr.copy()
    h, w = output.shape[:2]
    pts = denormalize_points(quad, w, h).astype(np.int32)
    cv2.polylines(output, [pts], True, (0, 255, 255), 3, cv2.LINE_AA)
    cv2.putText(
        output,
        label,
        (12, 34),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (0, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return output


def replace_id_card_in_video(
    video_path: Path,
    clean_card_path: Path,
    target_quad: Quad,
    output_path: Path,
    *,
    feather_pixels: int = 2,
    color_match_options: dict[str, Any] | None = None,
    tracking_mode: str = "lk",
    tracking_options: dict[str, Any] | None = None,
    preview_dir: Path | None = None,
    copy_audio: bool = True,
    ffmpeg_path: Path | None = None,
    audio_codec: str = "aac",
    keep_video_only: bool = False,
    skip_dark_frames: bool = True,
    whole_frame_luma_min: float = 12.0,
    target_luma_min: float = 18.0,
) -> Path:
    """逐帧替换视频中的身份证，输出最终视频。"""
    clean_card = read_image_unicode(clean_card_path)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频: {video_path}")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_ffmpeg = resolve_ffmpeg_path(ffmpeg_path) if copy_audio else None
    video_only_path = output_path
    if copy_audio and resolved_ffmpeg:
        video_only_path = output_path.with_name(f"{output_path.stem}_video_only{output_path.suffix}")
        print(f"视频画面临时输出：{video_only_path}")
        print(f"将使用 ffmpeg 复用原音频：{resolved_ffmpeg}")
    elif copy_audio:
        print("警告：未找到 ffmpeg，本次只能输出无音频视频。")

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(video_only_path), fourcc, fps, (width, height))
    if not writer.isOpened():
        cap.release()
        raise RuntimeError(f"无法创建输出视频: {video_only_path}")

    # 预览帧索引
    preview_indices = sorted(set([0, total // 4, total // 2, 3 * total // 4, total - 1]))
    preview_set = set(preview_indices) if preview_dir else set()
    preview_frames: list[tuple[int, np.ndarray, np.ndarray, Quad, str]] = []
    tracker: CardQuadTracker | None = None
    track_log: list[dict[str, Any]] = []
    tracking_options = tracking_options or {}

    written = 0
    frame_idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            if tracking_mode == "lk":
                if tracker is None:
                    tracker = CardQuadTracker(
                        frame,
                        target_quad,
                        min_points=int(tracking_options.get("min_points", 24)),
                        max_points=int(tracking_options.get("max_points", 160)),
                        redetect_interval=int(tracking_options.get("redetect_interval", 18)),
                        max_motion_px=float(tracking_options.get("max_motion_px", 80)),
                    )
                    current_quad = target_quad
                    track_status = "init"
                else:
                    current_quad = tracker.update(frame, frame_idx)
                    track_status = tracker.last_status
            elif tracking_mode == "fixed":
                current_quad = target_quad
                track_status = "fixed"
            else:
                raise RuntimeError(f"未知 tracking_mode: {tracking_mode}")

            original = frame.copy()
            skip_reason = ""
            should_skip = False
            if skip_dark_frames:
                should_skip, skip_reason = should_skip_replacement(
                    frame,
                    current_quad,
                    whole_frame_luma_min=whole_frame_luma_min,
                    target_luma_min=target_luma_min,
                )

            if should_skip:
                out = frame
                track_status = f"{track_status}; {skip_reason}"
            else:
                out = replace_id_card_in_frame(
                    frame,
                    clean_card,
                    target_quad=current_quad,
                    feather_pixels=feather_pixels,
                    color_match_options=color_match_options,
                )
            writer.write(out)
            written += 1
            track_log.append(
                {
                    "frame": frame_idx,
                    "quad": current_quad,
                    "status": track_status,
                }
            )

            if frame_idx in preview_set:
                preview_frames.append((frame_idx, original, out, current_quad, track_status))

            frame_idx += 1
            if frame_idx % 50 == 0:
                print(f"  已处理 {frame_idx}/{total} 帧...")
    finally:
        cap.release()
        writer.release()

    if written == 0:
        raise RuntimeError("证件替换没有产生任何帧")
    print(f"视频替换完成：{written} 帧，{fps} fps，{width}x{height}")

    if copy_audio and resolved_ffmpeg:
        mux_original_audio(
            ffmpeg_path=resolved_ffmpeg,
            video_only_path=video_only_path,
            original_video_path=video_path,
            output_path=output_path,
            audio_codec=audio_codec,
        )
        print(f"已复用原视频音频：{output_path}")
        if not keep_video_only:
            try:
                video_only_path.unlink()
            except OSError:
                print(f"提示：临时无音频视频未删除，可手动清理：{video_only_path}")
    else:
        print(f"输出：{output_path}")

    # ── 生成预览对比图 ──
    if preview_dir and preview_frames:
        preview_dir.mkdir(parents=True, exist_ok=True)
        track_log_path = preview_dir / "tracking_log.json"
        track_log_path.write_text(
            json.dumps(track_log, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  跟踪日志：{track_log_path}")

        for idx, orig, replaced, quad, status in preview_frames:
            h, w = orig.shape[:2]
            scale = min(1.0, 640 / w, 1280 / h)
            ow, oh = int(w * scale), int(h * scale)
            left = cv2.resize(draw_tracked_quad(orig, quad, f"tracked {idx}: {status}"), (ow, oh))
            right = cv2.resize(replaced, (ow, oh))
            comparison = np.hstack([left, right])
            cv2.line(comparison, (ow, 0), (ow, oh), (0, 255, 0), 2)
            out_path = preview_dir / f"frame_{idx:04d}_comparison.jpg"
            write_image_unicode(out_path, comparison)
            print(f"  预览：{out_path}")

        # 生成 contact sheet
        sheets: list[np.ndarray] = []
        for idx, orig, replaced, quad, status in preview_frames:
            scale = min(1.0, 400 / orig.shape[1], 700 / orig.shape[0])
            ow, oh = int(orig.shape[1] * scale), int(orig.shape[0] * scale)
            left = cv2.resize(draw_tracked_quad(orig, quad, f"frame {idx}: {status}"), (ow, oh))
            right = cv2.resize(replaced, (ow, oh))
            cv2.putText(left, f"frame {idx} (original)", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2, cv2.LINE_AA)
            cv2.putText(right, f"frame {idx} (replaced)", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2, cv2.LINE_AA)
            sheets.append(np.hstack([left, right]))
        if sheets:
            contact = np.vstack(sheets)
            contact_path = preview_dir / "contact_sheet.jpg"
            write_image_unicode(contact_path, contact)
            print(f"  Contact sheet：{contact_path}")

    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="视频身份证替换 demo（外观匹配 + LK 逐帧跟踪）")
    parser.add_argument(
        "--source-json",
        type=Path,
        default=Path(r"G:\duikouxing\tests\id_card_demo_outputs\selected_source_quad.json"),
    )
    parser.add_argument(
        "--target-json",
        type=Path,
        default=Path(r"G:\duikouxing\tests\id_card_demo_outputs\selected_target_quad.json"),
    )
    parser.add_argument("--output", type=Path, default=None, help="输出视频路径")
    parser.add_argument("--feather", type=int, default=2, help="边缘羽化像素")
    parser.add_argument(
        "--exposure-clip", type=float, default=90,
        help="L通道最大修正幅度；越大越允许新证件向旧证件亮度靠拢",
    )
    parser.add_argument("--chroma-clip", type=float, default=35, help="ab通道最大修正幅度")
    parser.add_argument("--style-strength", type=float, default=0.95, help="新证件学习旧证件颜色/亮度的强度")
    parser.add_argument("--whole-card-balance-strength", type=float, default=0.85, help="最终整张证件均值向旧证件靠拢的强度")
    parser.add_argument("--ab-std-strength", type=float, default=0.75, help="新证件彩色印刷区域向旧视频低饱和观感靠拢的强度")
    parser.add_argument("--no-ab-std-match", action="store_true", help="禁用ab通道色彩波动匹配")
    parser.add_argument("--no-original-card-style", action="store_true", help="禁用旧证件区域反透视风格匹配")
    parser.add_argument("--frame-post-match", action="store_true", help="贴回视频后再做一轮轻量颜色匹配")
    parser.add_argument("--no-shadow-transfer", action="store_true", help="禁用阴影低频迁移")
    parser.add_argument("--no-l-std-match", action="store_true", help="禁用L通道标准差匹配")
    parser.add_argument("--background-light-strength", type=float, default=0.9, help="平坦证件底色向原视频证件低频底色靠拢的强度")
    parser.add_argument("--no-paper-texture", action="store_true", help="禁用原证件纸面/压缩质感迁移")
    parser.add_argument("--no-match-blur", action="store_true", help="禁用视频模糊程度匹配")
    parser.add_argument(
        "--tracking",
        choices=["lk", "fixed"],
        default="lk",
        help="lk=用光流逐帧跟踪身份证四角；fixed=沿用首帧固定四角",
    )
    parser.add_argument("--track-min-points", type=int, default=24)
    parser.add_argument("--track-redetect-interval", type=int, default=18)
    parser.add_argument("--track-max-motion", type=float, default=80)
    parser.add_argument("--ffmpeg", type=Path, default=None, help=r"ffmpeg 路径，默认自动找 PATH 或 D:\ffmpeg\bin\ffmpeg.exe")
    parser.add_argument("--no-copy-audio", action="store_true", help="不复用原视频音频")
    parser.add_argument("--copy-audio-codec", action="store_true", help="音频直接copy；默认转AAC以提高MP4兼容性")
    parser.add_argument("--keep-video-only", action="store_true", help="保留中间无音频视频文件")
    parser.add_argument("--no-skip-dark-frames", action="store_true", help="禁用黑场/目标不可见跳过保护")
    parser.add_argument("--whole-frame-luma-min", type=float, default=12.0, help="整帧平均亮度低于该值时跳过替换")
    parser.add_argument("--target-luma-min", type=float, default=18.0, help="目标证件区域平均亮度低于该值时跳过替换")
    parser.add_argument("--verbose-color", action="store_true", help="逐帧打印颜色匹配日志")
    args = parser.parse_args()

    # ── 读取 JSON ──
    source_data = json.loads(args.source_json.read_text(encoding="utf-8"))
    target_data = json.loads(args.target_json.read_text(encoding="utf-8"))

    clean_card_path = Path(source_data["outputs"]["clean_card"])
    video_path = Path(target_data["video_metadata"]["video"])
    target_quad: Quad = target_data["selected"]["points"]

    print(f"干净证件图：{clean_card_path}")
    print(f"输入视频：  {video_path}")
    print(f"目标四角：  {target_quad}")
    print(f"视频尺寸：  {target_data['video_metadata']['width']}x{target_data['video_metadata']['height']}")
    print(f"帧数：      {target_data['video_metadata']['frame_count']}")
    print(f"FPS：       {target_data['video_metadata']['fps']}")
    print()

    output_path = args.output or video_path.with_name("final_with_id_card.mp4")
    preview_dir = args.source_json.parent / "replace_preview"

    color_opts = {
        "original_card_style_match": not args.no_original_card_style,
        "frame_post_match": args.frame_post_match,
        "exposure_clip": args.exposure_clip,
        "chroma_clip": args.chroma_clip,
        "style_strength": args.style_strength,
        "whole_card_balance_strength": args.whole_card_balance_strength,
        "whole_card_balance_l_clip": 18,
        "whole_card_balance_chroma_clip": 12,
        "match_ab_std": not args.no_ab_std_match,
        "ab_std_strength": args.ab_std_strength,
        "match_l_std": not args.no_l_std_match,
        "shadow_transfer": not args.no_shadow_transfer,
        "sample_erode_pixels": 10,
        "shadow_sigma": 45,
        "shadow_clip": 45,
        "background_light_strength": args.background_light_strength,
        "background_light_sigma": 52,
        "paper_texture": not args.no_paper_texture,
        "texture_sigma": 1.8,
        "texture_strength": 0.3,
        "texture_clip": 6.0,
        "match_blur": not args.no_match_blur,
        "verbose": args.verbose_color,
    }
    tracking_opts = {
        "min_points": args.track_min_points,
        "redetect_interval": args.track_redetect_interval,
        "max_motion_px": args.track_max_motion,
    }
    print(f"颜色匹配参数：{color_opts}")
    print(f"跟踪模式：{args.tracking} {tracking_opts}")
    print()

    replace_id_card_in_video(
        video_path=video_path,
        clean_card_path=clean_card_path,
        target_quad=target_quad,
        output_path=output_path,
        feather_pixels=args.feather,
        color_match_options=color_opts,
        tracking_mode=args.tracking,
        tracking_options=tracking_opts,
        preview_dir=preview_dir,
        copy_audio=not args.no_copy_audio,
        ffmpeg_path=args.ffmpeg,
        audio_codec="copy" if args.copy_audio_codec else "aac",
        keep_video_only=args.keep_video_only,
        skip_dark_frames=not args.no_skip_dark_frames,
        whole_frame_luma_min=args.whole_frame_luma_min,
        target_luma_min=args.target_luma_min,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
