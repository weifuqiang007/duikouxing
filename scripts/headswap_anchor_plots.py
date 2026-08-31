"""106 点锚点编号图（第六轮 L3，docs §28.8/§28.11）。

为 A 原视频与 B 再演视频的指定帧输出 106 点带编号叠加图，供人工确认
鼻梁/鼻翼/眼角/眉端索引后写入固定 anchor group。禁止凭记忆硬编码索引。

用法（liveportrait 环境）：
    python scripts/headswap_anchor_plots.py \
        --videos jobs-home/hs-p1-0004/work/base_upright.mp4 \
                 jobs-home/hs-p1-0004/work/animated_head.mp4 \
        --frames 0 30 60 90 \
        --insightface-root external/LivePortrait/pretrained_weights/insightface \
        --out-dir jobs-home/hs-p1-0004/previews/anchor106
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser(description="106 点锚点编号图（人工确认索引用）")
    ap.add_argument("--videos", nargs="+", required=True, type=Path)
    ap.add_argument("--frames", nargs="+", required=True, type=int)
    ap.add_argument("--insightface-root", required=True, type=Path)
    ap.add_argument("--det-size", type=int, default=640)
    ap.add_argument("--out-dir", required=True, type=Path)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    from insightface.app import FaceAnalysis

    app = FaceAnalysis(
        name="buffalo_l",
        root=str(args.insightface_root),
        allowed_modules=["detection", "landmark_2d_106"],
        providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    app.prepare(ctx_id=0, det_size=(args.det_size, args.det_size))

    for video in args.videos:
        cap = cv2.VideoCapture(str(video))
        if not cap.isOpened():
            print(f"ERROR: cannot open {video}", flush=True)
            return 2
        tag = "base" if "base" in video.stem else "anim"
        for fi in args.frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            ok, frame = cap.read()
            if not ok:
                continue
            faces = app.get(frame)
            if not faces:
                print(f"[WARN] {tag} frame {fi}: no face", flush=True)
                continue
            face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
            lm = getattr(face, "landmark_2d_106", None)
            if lm is None:
                print(f"[WARN] {tag} frame {fi}: landmark_2d_106 不可用（模型缺失）", flush=True)
                return 3
            lm = np.asarray(lm, dtype=np.float32)
            x0, y0 = int(face.bbox[0]), int(face.bbox[1])
            x1, y1 = int(face.bbox[2]), int(face.bbox[3])
            pad = int(0.25 * (x1 - x0))
            cx0, cy0 = max(0, x0 - pad), max(0, y0 - pad)
            cx1 = min(frame.shape[1], x1 + pad)
            cy1 = min(frame.shape[0], y1 + pad)
            crop = frame[cy0:cy1, cx0:cx1].copy()
            scale = 960.0 / max(crop.shape[1], 1)
            crop = cv2.resize(crop, (int(crop.shape[1] * scale), int(crop.shape[0] * scale)))
            for i, (px, py) in enumerate(lm):
                qx, qy = (px - cx0) * scale, (py - cy0) * scale
                cv2.circle(crop, (int(qx), int(qy)), 3, (0, 0, 255), -1)
                cv2.putText(
                    crop, str(i), (int(qx) + 4, int(qy) - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.32, (0, 255, 255), 1, cv2.LINE_AA,
                )
            out = args.out_dir / f"anchor106_{tag}_f{fi:04d}.png"
            cv2.imwrite(str(out), crop)
            print(f"OK: {out} ({len(lm)} landmarks)", flush=True)
        cap.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
