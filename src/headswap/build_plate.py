"""静态背景底板构建（运行在编排环境：cv2 + numpy 即可）。

固定机位 + 人物基本不动时，对全视频均匀抽帧，逐像素累计"非头区域"样本的均值，
得到背景底板；从未见够样本的洞区域（头部长期遮挡处）用 cv2.inpaint 补齐。

输出：background_plate.png（与输入视频同分辨率）。

内存策略：用累加代替 median，避免 50 帧 1080p 的 float 栈（约 1.2GB 峰值）。
静态场景下均值与中位数几乎等价。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np


def build_plate(
    video: Path,
    masks_dir: Path,
    output: Path,
    sample_frames: int = 50,
    min_samples: int = 8,
    mask_expand_px: int = 12,
    inpaint_radius: int = 5,
) -> dict:
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频: {video}")
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        raise RuntimeError(f"无法读取帧数: {video}")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    idxs = np.unique(np.linspace(0, total - 1, num=min(sample_frames, total)).astype(int))

    acc = np.zeros((height, width, 3), dtype=np.float64)
    cnt = np.zeros((height, width, 1), dtype=np.int32)
    used = 0
    for i in idxs:
        mask_path = masks_dir / f"mask_{i:06d}.png"
        if not mask_path.is_file():
            continue
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, frame = cap.read()
        if not ok:
            continue
        mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        if mask is None or mask.shape[:2] != (height, width):
            continue
        valid = mask == 0
        acc[valid] += frame[valid]
        cnt[valid] += 1
        used += 1
    cap.release()
    if used < 3:
        raise RuntimeError(f"可用采样帧过少: {used}")

    plate = np.zeros((height, width, 3), dtype=np.float32)
    seen_enough = cnt[..., 0] >= min_samples
    plate[seen_enough] = (acc[seen_enough] / cnt[seen_enough]).astype(np.float32)

    # 洞 = 样本不足的像素；扩张后统一修复，避免边缘残留发丝
    hole = (~seen_enough).astype(np.uint8) * 255
    if mask_expand_px > 0 and hole.any():
        k = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (mask_expand_px * 2 + 1, mask_expand_px * 2 + 1)
        )
        hole = cv2.dilate(hole, k)
    if hole.any():
        # inpaint 需要 8bit 图；有值的区域先填上任何可用估计（全帧均值），
        # 洞区域由 TELEA 从边界向内扩散
        plate_u8 = np.clip(plate, 0, 255).astype(np.uint8)
        # 无样本像素先填邻域可用的粗值，避免 inpaint 输入出现 0 值黑洞边界
        fallback = cv2.blur(plate_u8, (31, 31))
        plate_u8 = np.where(seen_enough[..., None], plate_u8, fallback)
        plate_u8 = cv2.inpaint(plate_u8, hole, inpaint_radius, cv2.INPAINT_TELEA)
        plate = plate_u8.astype(np.float32)
    else:
        plate = np.clip(plate, 0, 255).astype(np.uint8)

    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), np.clip(plate, 0, 255).astype(np.uint8)):
        raise RuntimeError(f"无法写出底板: {output}")
    stats = {
        "video": str(video),
        "total_frames": total,
        "sampled": used,
        "hole_pixels_before_expand": int((~seen_enough).sum()),
        "output": str(output),
    }
    output.with_suffix(".json").write_text(json.dumps(stats, ensure_ascii=False), encoding="utf-8")
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="静态背景底板构建")
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--masks-dir", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--sample-frames", type=int, default=50)
    ap.add_argument("--min-samples", type=int, default=8)
    ap.add_argument("--mask-expand-px", type=int, default=12)
    ap.add_argument("--inpaint-radius", type=int, default=5)
    args = ap.parse_args(argv)
    try:
        stats = build_plate(
            args.video,
            args.masks_dir,
            args.output,
            sample_frames=args.sample_frames,
            min_samples=args.min_samples,
            mask_expand_px=args.mask_expand_px,
            inpaint_radius=args.inpaint_radius,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
