"""第六轮运动验收指标（docs §28.12）。

对前三秒（或指定帧数）的 base 与各输出视频做逐帧主脸检测，提取
眼中点轨迹 / 眼线 roll / 眼距，计算：
- tx/ty corr（output vs A 锚点）      目标 >= 0.95
- lag（互相关峰值，±5 帧）            目标 0
- tx/ty 低频幅度增益（std 比）        0.85~1.10 / 0.80~1.15
- roll 幅度增益                       0.75~1.10
- 高频 jitter RMS（原信号 - smooth5） <0.40px，roll <0.08°
- 头—身体相对漂移 p95                 <=2px（去均值后的逐帧眼心偏差）

用法（liveportrait 环境）：
    python scripts/headswap_motion_metrics.py --base <原片> \
        --outs a.mp4 b.mp4 ... --frames 90 --json OUT.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def eye_series(path: str, n_frames: int):
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(
        name="buffalo_l",
        allowed_modules=["detection"],
        providers=["CPUExecutionProvider"],
    )
    app.prepare(ctx_id=0, det_size=(640, 640))
    cap = cv2.VideoCapture(path)
    rows = []
    idx = 0
    while idx < n_frames:
        ok, fr = cap.read()
        if not ok:
            break
        faces = app.get(fr)
        if faces:
            # 主脸 = 与上一帧主脸 IoU 最大（无历史时取最大框）；简化：取最大框
            f = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
            kps = np.asarray(f.kps, dtype=np.float64)
            le, re_, nose = kps[0], kps[1], kps[2]
            c = (le + re_) * 0.5
            rows.append(
                (
                    float(c[0]), float(c[1]),
                    float(np.arctan2(re_[1] - le[1], re_[0] - le[0])),
                    float(np.hypot(re_[0] - le[0], re_[1] - le[1])),
                    float((c * 0.7 + nose * 0.3)[0]),
                    float((c * 0.7 + nose * 0.3)[1]),
                )
            )
        else:
            rows.append(rows[-1] if rows else (np.nan,) * 6)
        idx += 1
    cap.release()
    arr = np.asarray(rows, dtype=np.float64)
    # 前向填充检测空洞
    for j in range(arr.shape[1]):
        ok = np.isfinite(arr[:, j])
        arr[~ok, j] = np.interp(np.flatnonzero(~ok), np.flatnonzero(ok), arr[ok, j])
    return arr


def centered(x: np.ndarray, win: int = 5) -> np.ndarray:
    out = np.empty_like(x)
    half = win // 2
    for i in range(len(x)):
        lo, hi = max(0, i - half), min(len(x), i + half + 1)
        out[i] = x[lo:hi].mean()
    return out


def lag_frames(a: np.ndarray, b: np.ndarray, span: int = 5) -> int:
    la, lb = a - a.mean(), b - b.mean()
    best, best_v = 0, -np.inf
    for l in range(-span, span + 1):
        if l <= 0:
            u, v = la[: len(la) + l], lb[-l: len(lb)]
        else:
            u, v = la[l:], lb[: len(lb) - l]
        if len(u) < 8:
            continue
        c = float(np.corrcoef(u, v)[0, 1])
        if c > best_v:
            best_v, best = c, l
    return best


def compare(base: np.ndarray, out: np.ndarray) -> dict:
    res = {}
    for name, i, tgt in (("tx", 0, (0.85, 1.10)), ("ty", 1, (0.80, 1.15))):
        b, o = base[:, i] - base[0, i], out[:, i] - out[0, i]
        res[f"{name}_corr"] = round(float(np.corrcoef(b, o)[0, 1]), 4)
        res[f"{name}_gain"] = round(float(o.std() / (b.std() + 1e-9)), 4)
        res[f"{name}_lag"] = lag_frames(b, o)
        hp = o - centered(o, 5)
        res[f"{name}_jitter_rms"] = round(float(np.sqrt((hp ** 2).mean())), 4)
    b_roll = np.degrees(base[:, 2] - base[0, 2])
    o_roll = np.degrees(out[:, 2] - out[0, 2])
    res["roll_corr"] = round(float(np.corrcoef(b_roll, o_roll)[0, 1]), 4)
    res["roll_gain"] = round(float(o_roll.std() / (b_roll.std() + 1e-9)), 4)
    res["roll_jitter_rms_deg"] = round(float(np.sqrt(((o_roll - centered(o_roll, 5)) ** 2).mean())), 4)
    # 头—身体相对漂移：output 眼心相对 base 眼心的逐帧偏差（去均值）p95
    dx = (out[:, 0] - out[0, 0]) - (base[:, 0] - base[0, 0])
    dy = (out[:, 1] - out[0, 1]) - (base[:, 1] - base[0, 1])
    drift = np.hypot(dx - dx.mean(), dy - dy.mean())
    res["head_body_drift_p95_px"] = round(float(np.percentile(drift, 95)), 3)
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--outs", nargs="+", required=True)
    ap.add_argument("--frames", type=int, default=90)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()

    base = eye_series(args.base, args.frames)
    report = {"frames": int(len(base)), "base_range": {
        "tx": round(float(base[:, 0].ptp()), 2), "ty": round(float(base[:, 1].ptp()), 2),
        "roll_deg": round(float(np.degrees(base[:, 2]).ptp()), 2),
    }}
    for out_path in args.outs:
        arr = eye_series(out_path, args.frames)
        tag = Path(out_path).stem
        report[tag] = compare(base, arr)
        print(f"== {tag} ==")
        for k, v in report[tag].items():
            print(f"  {k} = {v}")
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"OK: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
