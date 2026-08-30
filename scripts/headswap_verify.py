"""headswap 成片量化验收（运行在 liveportrait 环境）。

用法：
    python scripts/headswap_verify.py --base <原片> --final <成片> [--card-zone X Y W H] [--json OUT]

指标（docs §17.10）：
- mouth_corr       内唇 62/66 张开度相关系数（目标 >= 0.95）
- center_corr_x/y  人脸中心运动相关性（目标 >= 0.98）
- amp_x/amp_y      中心运动幅度比（目标 ~1.0）
- roll_corr        眼线角度相关性（目标 >= 0.90）
- roll_amp         眼线角度幅度比
- lag_x/lag_roll   互相关峰值滞后帧数（目标 <= 1）
- scale_std_ratio  眼距 std 比（目标 0.8~1.2，防"呼吸"）
- card_psnr_db     证件/手部区域 PSNR（越高越好，>=40 视为原片保留）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np


def series(path: str, card_zone=None, masks_dir=None):
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(
        allowed_modules=["detection", "landmark_3d_68"], providers=["CPUExecutionProvider"]
    )
    app.prepare(ctx_id=0, det_size=(640, 640))
    cap = cv2.VideoCapture(path)
    rows = []
    diffs = []
    halo_rows = []
    jaw_rows = []
    prev_zone = None
    idx = 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        faces = app.get(fr)
        head_mask = None
        if masks_dir is not None:
            head_mask = cv2.imread(str(Path(masks_dir) / f"mask_{idx:06d}.png"), cv2.IMREAD_GRAYSCALE)
        if faces:
            f = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
            lm = f.landmark_3d_68
            lm = lm[0] if getattr(lm, "ndim", 2) == 3 else lm
            le, re_ = lm[36], lm[45]  # 外眼角，眼距/眼线比 5 点稳
            mouth = abs(float(lm[66][1] - lm[62][1]))
            roll = float(np.arctan2(re_[1] - le[1], re_[0] - le[0]))
            ed = float(np.hypot(re_[0] - le[0], re_[1] - le[1]))
            cx = float((f.bbox[0] + f.bbox[2]) / 2)
            cy = float((f.bbox[1] + f.bbox[3]) / 2)
            rows.append((mouth, cx, cy, roll, ed))
            lab = cv2.cvtColor(fr, cv2.COLOR_BGR2LAB).astype(np.float32)
            halo_rows.append(_halo_ring_delta_e(lab, f.bbox, head_mask))
            jaw_rows.append(_jaw_seam_delta_e(lab, f.bbox))
        else:
            rows.append((np.nan,) * 5)
            halo_rows.append(float("nan"))
            jaw_rows.append(float("nan"))
        idx += 1
        if card_zone is not None and prev_zone is not None:
            x, y, w, h = card_zone
            cur = fr[y : y + h, x : x + w].astype(np.float32)
            diffs.append(float(np.mean((cur - prev_zone) ** 2)))
            prev_zone = cur
        elif card_zone is not None:
            x, y, w, h = card_zone
            prev_zone = fr[y : y + h, x : x + w].astype(np.float32)
    cap.release()
    return (
        np.array(rows, dtype=np.float64),
        diffs,
        np.array(halo_rows, dtype=np.float64),
        np.array(jaw_rows, dtype=np.float64),
    )


def corr(a: np.ndarray, b: np.ndarray) -> float | None:
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 30:
        return None
    return float(np.corrcoef(a[m], b[m])[0, 1])


# ---------------- 第三轮视觉代理指标（docs §20.12 / §21.4，均为代理，最终以人工看片为准） ----------------

def _above_head_wall_lab(lab: np.ndarray, bbox) -> np.ndarray | None:
    """头顶上方墙面带的中位 LAB（与补洞种子同思路，未被动过的区域）。"""
    x0, y0, x1, y1 = [float(v) for v in bbox]
    bw, bh = x1 - x0, y1 - y0
    ax0, ax1 = max(0, int(x0 - 0.5 * bw)), min(lab.shape[1], int(x1 + 0.5 * bw))
    ay0, ay1 = max(0, int(y0 - 0.55 * bh)), max(1, int(y0 - 0.15 * bh))
    band = lab[ay0:ay1, ax0:ax1]
    if band.size < 900:
        return None
    return np.median(band.reshape(-1, 3), axis=0)


def _halo_ring_delta_e(lab: np.ndarray, bbox, head_mask: np.ndarray | None = None) -> float:
    """头部外环带与头顶墙面参考的 ΔE 中位数。

    优先用 A 侧 head mask 外 2~10px 的窄环带（只取下颌线以上——下颌带属于
    jaw_seam 指标）。该带正是 v2 差集补洞米黄填充的所在区（old_head_safe =
    mask + safe_margin 8px），v2 此处为肤色、v3 应为墙面；无 mask 时退化为
    几何侧带。"""
    wall = _above_head_wall_lab(lab, bbox)
    if wall is None:
        return float("nan")
    x0, y0, x1, y1 = [float(v) for v in bbox]
    bw, bh = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    h, w = lab.shape[:2]
    yy = np.arange(h, dtype=np.float32)[:, None]
    if head_mask is not None and (head_mask > 0).any():
        m8 = (head_mask > 0).astype(np.uint8) * 255
        k10 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))
        k2 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        band = (cv2.dilate(m8, k10) > 0) & ~(cv2.dilate(m8, k2) > 0) & (yy <= float(y1))
        px = lab[band]
    else:
        mask = np.zeros((h, w), dtype=bool)
        ty, by = max(0, int(y0 - 0.20 * bh)), min(h, int(cy + 0.10 * bh))
        lx0, lx1 = max(0, int(cx - 1.05 * bw)), max(0, int(cx - 0.75 * bw))
        rx0, rx1 = min(w, int(cx + 0.75 * bw)), min(w, int(cx + 1.05 * bw))
        mask[ty:by, lx0:lx1] = True    # 左侧带
        mask[ty:by, rx0:rx1] = True    # 右侧带
        tpy0, tpy1 = max(0, int(y0 - 0.60 * bh)), max(0, int(y0 - 0.38 * bh))
        if tpy1 > tpy0:
            mask[tpy0:tpy1, max(0, int(cx - 0.5 * bw)) : min(w, int(cx + 0.5 * bw))] = True  # 顶带
        px = lab[mask]
    if len(px) < 900:
        return float("nan")
    de = np.linalg.norm(px - wall[None, :], axis=1)
    return float(np.median(de))


def _jaw_seam_delta_e(lab: np.ndarray, bbox) -> float:
    """下颌线上/下带状区（B 皮肤 vs A 脖子）的均值 LAB 差，代理接缝色差。"""
    x0, y0, x1, y1 = [float(v) for v in bbox]
    bw, bh = x1 - x0, y1 - y0
    cx = (x0 + x1) / 2.0
    jx0, jx1 = max(0, int(cx - 0.30 * bw)), min(lab.shape[1], int(cx + 0.30 * bw))
    a0, a1 = int(y1 - 0.07 * bh), int(y1 - 0.02 * bh)
    b0, b1 = int(y1 + 0.03 * bh), int(y1 + 0.10 * bh)
    above = lab[a0:a1, jx0:jx1]
    below = lab[b0:b1, jx0:jx1]
    if above.size < 300 or below.size < 300:
        return float("nan")
    d = above.reshape(-1, 3).mean(axis=0) - below.reshape(-1, 3).mean(axis=0)
    return float(np.linalg.norm(d))


def best_lag(a: np.ndarray, b: np.ndarray, span: int = 5) -> int | None:
    """b 滞后 a 多少帧时互相关最大（0 = 同步）。"""
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 60:
        return None
    best, best_l = -2.0, 0
    for lag in range(-span, span + 1):
        if lag >= 0:
            x, y = a[: len(a) - lag or None], b[lag:]
            mm = np.isfinite(x) & np.isfinite(y)
            if mm.sum() < 60:
                continue
            c = float(np.corrcoef(x[mm], y[mm])[0, 1])
        else:
            x, y = b[: len(b) + lag], a[-lag:]
            mm = np.isfinite(x) & np.isfinite(y)
            if mm.sum() < 60:
                continue
            c = float(np.corrcoef(x[mm], y[mm])[0, 1])
        if c > best:
            best, best_l = c, lag
    return best_l


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="headswap 成片量化验收")
    ap.add_argument("--base", required=True, type=Path)
    ap.add_argument("--final", required=True, type=Path)
    ap.add_argument("--card-zone", type=int, nargs=4, default=[150, 1050, 370, 350], metavar=("X", "Y", "W", "H"))
    ap.add_argument("--masks-dir", type=Path, default=None,
                    help="A 侧 head mask 目录（可选）：halo 环带用 mask 6~25px 外带精确测量")
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args(argv)

    sb, _, halo_b, jaw_b = series(str(args.base), masks_dir=args.masks_dir)
    sf, diffs, halo_f, jaw_f = series(str(args.final), args.card_zone, masks_dir=args.masks_dir)
    n = min(len(sb), len(sf))
    sb, sf = sb[:n], sf[:n]
    halo_f, jaw_f = halo_f[:n], jaw_f[:n]

    def ratio(i):
        bs, fs = np.nanstd(sb[:, i]), np.nanstd(sf[:, i])
        return round(float(fs / bs), 3) if bs > 1e-9 else None

    out = {
        "base": str(args.base),
        "final": str(args.final),
        "frames": int(n),
        "mouth_corr": round(corr(sb[:, 0], sf[:, 0]) or 0, 3),
        "center_corr_x": round(corr(sb[:, 1], sf[:, 1]) or 0, 3),
        "center_corr_y": round(corr(sb[:, 2], sf[:, 2]) or 0, 3),
        "amp_x": ratio(1),
        "amp_y": ratio(2),
        "roll_corr": round(corr(sb[:, 3], sf[:, 3]) or 0, 3),
        "roll_amp": ratio(3),
        "lag_x": best_lag(sb[:, 1], sf[:, 1]),
        "lag_roll": best_lag(sb[:, 3], sf[:, 3]),
        "scale_std_ratio": ratio(4),
        "card_psnr_db": round(float(np.mean([10 * np.log10(255**2 / max(d, 1e-6)) for d in diffs])), 1) if diffs else None,
        # 第三轮视觉代理（§20.12/§21.4）：halo=头部外环 vs 头顶墙面参考的 ΔE；
        # jaw_seam=下颌线上/下带状区均值 LAB 差。均为代理，最终以人工看片为准。
        "halo_delta_e_median": round(float(np.nanmedian(halo_f)), 2) if np.isfinite(halo_f).any() else None,
        "halo_delta_e_p90": round(float(np.nanpercentile(halo_f, 90)), 2) if np.isfinite(halo_f).any() else None,
        "jaw_seam_delta_e_median": round(float(np.nanmedian(jaw_f)), 2) if np.isfinite(jaw_f).any() else None,
        "halo_delta_e_base_median": round(float(np.nanmedian(halo_b)), 2) if np.isfinite(halo_b).any() else None,
        "jaw_seam_delta_e_base_median": round(float(np.nanmedian(jaw_b)), 2) if np.isfinite(jaw_b).any() else None,
    }
    text = json.dumps(out, ensure_ascii=False, indent=2)
    print(text)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
