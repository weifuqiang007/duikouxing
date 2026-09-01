"""新头对齐回贴 worker（运行在 liveportrait 环境）。第二轮重构版（docs §17）。

与第一版的关键差异（对应人工看片发现的三个问题）：

问题一（灰白光晕）：
  - 旧版把 A 头洞整块替换为"非头区域均值底板"——身体/手/证件运动重影直接入片；
  - 旧版对二值 mask 做对称 GaussianBlur，羽化向外扩散，B 肖像的白背景被卷进成片；
  - 新版：预乘 alpha 后 warp（B 背景永不参与插值），alpha 用内部距离变换生成、
    原 mask 外严格为 0；背景只在 residual = 旧头安全清理区 - 新头核心 的差集上，
    用采样环鲁棒平面拟合局部补洞（白墙场景），不再整块换底板。

问题二（头身姿态滞后/脱节）：
  - 旧版 5 点（含嘴角）相似变换 + 9 帧因果中位数 + EMA0.8，总延迟约 6~7 帧（0.2s），
    且嘴角开合污染 scale/ty、眼距抖动造成"呼吸"；
  - 新版：只用双眼估计 scale/angle/translation（嘴角完全退出），离线两遍式滤波
    （Hampel 去尖峰 + 居中零相位平滑），无因果延迟。

问题三（A 旧头边缘漏出）：
  - 旧版 A mask 全画布二值 EMA 造成前缘漏出/后缘拖尾；
  - 新版：segment 阶段关闭 EMA（配置 temporal_ema: 0），清理区用
    当前帧 mask ∪ 运动补偿后的上一帧 mask（按双眼刚体变换 warp），再小膨胀。

调试：--debug-dir 每 N 帧输出 7 联调试图（A 帧/清理区/B warp/alpha/差集/补洞底/成片）；
每帧 raw+filtered 变换参数写入 <output>.transforms.json 供运动曲线分析。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from segment_head import BiSeNetParser, HeadSegmenter, PrimaryFaceTracker  # noqa: E402
from color_transfer import ColorMatcher, lab_stats  # noqa: E402


# ---------------------------------------------------------------- 变换估计

def rigid_from_eyes(kps_src: np.ndarray, kps_dst: np.ndarray):
    """纯眼部刚体参数：scale/angle 来自双眼向量，平移对齐双眼中心。

    嘴角完全退出估计（§17.5.2：嘴角随说话运动，会污染 scale 和 ty）。
    返回 (s, angle, tx, ty)；构造 M = rebuild(s, angle, tx, ty) 映射 src -> dst。
    """
    ls, rs = np.asarray(kps_src[0], dtype=np.float64), np.asarray(kps_src[1], dtype=np.float64)
    ld, rd = np.asarray(kps_dst[0], dtype=np.float64), np.asarray(kps_dst[1], dtype=np.float64)
    vb, va = rs - ls, rd - ld
    nb = float(np.hypot(*vb))
    na = float(np.hypot(*va))
    if nb < 1e-6 or na < 1e-6:
        return None
    s = na / nb
    angle = math.atan2(va[1], va[0]) - math.atan2(vb[1], vb[0])
    cb, ca = (ls + rs) * 0.5, (ld + rd) * 0.5
    c, sn = s * math.cos(angle), s * math.sin(angle)
    tx = ca[0] - (c * cb[0] - sn * cb[1])
    ty = ca[1] - (sn * cb[0] + c * cb[1])
    return s, angle, tx, ty


def rigid_from_eyes_nose(kps_src: np.ndarray, kps_dst: np.ndarray, nose_weight: float = 0.3):
    """眼睛+鼻尖刚体参数（§28.7 快速方案）：只用 kps[:3]，嘴角完全不进。

    双眼仍决定 scale/roll（两点方向最稳）；平移由"眼中点 0.7 + 鼻尖 0.3"的
    加权中心对齐解出——鼻尖随 yaw 有少量横移，不能让单鼻点决定全部平移。
    """
    src = np.asarray(kps_src[:3], dtype=np.float64)
    dst = np.asarray(kps_dst[:3], dtype=np.float64)
    if not (np.isfinite(src).all() and np.isfinite(dst).all()):
        return None
    base = rigid_from_eyes(src, dst)
    if base is None:
        return None
    s, angle = base[0], base[1]
    w = float(np.clip(nose_weight, 0.0, 1.0))
    cb = (1.0 - w) * ((src[0] + src[1]) * 0.5) + w * src[2]
    ca = (1.0 - w) * ((dst[0] + dst[1]) * 0.5) + w * dst[2]
    c, sn = s * math.cos(angle), s * math.sin(angle)
    tx = ca[0] - (c * cb[0] - sn * cb[1])
    ty = ca[1] - (sn * cb[0] + c * cb[1])
    return s, angle, tx, ty


def weighted_similarity(
    src_pts: np.ndarray,
    dst_pts: np.ndarray,
    weights: np.ndarray,
    irls_iters: int = 3,
    mad_reject: float = 2.5,
) -> tuple[float, float, float, float] | None:
    """加权 4 自由度相似变换（§28.8 正式方案）：加权 Procrustes + MAD 降权。

    只解 scale/roll/tx/ty，禁止 full affine（shear 会让脸型随帧扭曲）。
    单点残差 > mad_reject×MAD 的锚点在本帧降权（×0.1）；返回 None 表示
    无法求解（有效点不足由调用方负责回退 eyes_nose）。
    """
    src = np.asarray(src_pts, dtype=np.float64)
    dst = np.asarray(dst_pts, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64).copy()
    if src.shape != dst.shape or src.shape[0] < 3 or w.shape[0] != src.shape[0]:
        return None
    if not (np.isfinite(src).all() and np.isfinite(dst).all() and (w > 0).any()):
        return None
    w = np.clip(w, 0.0, None)
    w = w / w.sum()

    def solve(wv: np.ndarray):
        cb = (wv[:, None] * src).sum(axis=0)
        ca = (wv[:, None] * dst).sum(axis=0)
        xs, ys = src - cb, dst - ca
        # 加权相似变换闭式解（Umeyama 无反射版）
        cov = float((wv * (xs[:, 0] * ys[:, 0] + xs[:, 1] * ys[:, 1])).sum())
        var = float((wv * (xs[:, 0] ** 2 + xs[:, 1] ** 2)).sum())
        kxy = float((wv * (xs[:, 0] * ys[:, 1] - xs[:, 1] * ys[:, 0])).sum())
        if var < 1e-9:
            return None
        s = math.hypot(cov, kxy) / var
        angle = math.atan2(kxy, cov)
        c, sn = s * math.cos(angle), s * math.sin(angle)
        tx = ca[0] - (c * cb[0] - sn * cb[1])
        ty = ca[1] - (sn * cb[0] + c * cb[1])
        return s, angle, tx, ty

    wv = w.copy()
    sol = solve(wv)
    if sol is None:
        return None
    for _ in range(max(0, int(irls_iters))):
        s, angle, tx, ty = sol
        c, sn = s * math.cos(angle), s * math.sin(angle)
        R = np.array([[c, -sn], [sn, c]])
        resid = np.linalg.norm(dst - (src @ R.T + np.array([tx, ty])), axis=1)
        med = np.median(resid[wv > 0])
        mad = 1.4826 * np.median(np.abs(resid[wv > 0] - med)) + 1e-9
        new_w = np.where(resid > mad_reject * mad, wv * 0.1, w)
        if np.allclose(new_w, wv, atol=1e-12) or new_w.sum() <= 0:
            break
        wv = new_w / new_w.sum()
        nxt = solve(wv)
        if nxt is None:
            break
        sol = nxt
    return sol


def similarity(src_kps: np.ndarray, dst_kps: np.ndarray) -> np.ndarray | None:
    """旧版全 5 点 LMEDS 相似变换（仅作 A/B 对照保留）。"""
    src = np.asarray(src_kps, dtype=np.float32)
    dst = np.asarray(dst_kps, dtype=np.float32)
    if not np.isfinite(src).all() or not np.isfinite(dst).all():
        return None
    m, _ = cv2.estimateAffinePartial2D(src, dst, method=cv2.LMEDS)
    if m is None:
        return None
    s = math.hypot(float(m[0, 0]), float(m[1, 0]))
    if not (0.05 < s < 20.0):
        return None
    return m


def decompose(m: np.ndarray):
    s = math.hypot(float(m[0, 0]), float(m[1, 0]))
    angle = math.atan2(float(m[1, 0]), float(m[0, 0]))
    return s, angle, float(m[0, 2]), float(m[1, 2])


def rebuild(s: float, angle: float, tx: float, ty: float) -> np.ndarray:
    c, sn = s * math.cos(angle), s * math.sin(angle)
    return np.array([[c, -sn, tx], [sn, c, ty]], dtype=np.float64)


def transform_point(point: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """用 2×3 仿射矩阵变换单点，显式用于头颈支点审计。"""
    p = np.asarray(point, dtype=np.float64).reshape(2)
    m = np.asarray(matrix, dtype=np.float64).reshape(2, 3)
    return m[:, :2] @ p + m[:, 2]


def estimate_head_attachment(kps: np.ndarray, bbox: np.ndarray) -> np.ndarray | None:
    """从眼睛/鼻梁估计 B 头颈连接点 Q，不使用持续发音的嘴角。

    InsightFace 的五点顺序为双眼、鼻尖、双嘴角。本函数只读取前三点：X 主要跟随
    双眼中心，少量吸收鼻尖的 yaw 位移；Y 沿眼中点→鼻尖方向延伸到下颌底部。
    bbox 只作宽松限幅，避免检测尖峰把 Q 推出头部。
    """
    pts = np.asarray(kps, dtype=np.float64)
    box = np.asarray(bbox, dtype=np.float64).reshape(4)
    if pts.shape[0] < 3 or not np.isfinite(pts[:3]).all() or not np.isfinite(box).all():
        return None
    eye = 0.5 * (pts[0] + pts[1])
    nose = pts[2]
    h = max(float(box[3] - box[1]), 1.0)
    qx = eye[0] + 0.25 * (nose[0] - eye[0])
    qy = eye[1] + 2.55 * (nose[1] - eye[1])
    qx = float(np.clip(qx, box[0] + 0.25 * (box[2] - box[0]), box[2] - 0.25 * (box[2] - box[0])))
    qy = float(np.clip(qy, box[1] + 0.78 * h, box[1] + 1.08 * h))
    return np.array([qx, qy], dtype=np.float64)


def estimate_neck_pivot(
    neck_mask: np.ndarray,
    face_box: np.ndarray,
    face_kps: np.ndarray | None = None,
) -> np.ndarray | None:
    """在 A raw neck 主体顶部估计颈根支点 P（full-frame 像素坐标）。

    只搜索脸框中心附近、下颌下方的 class14 区域，取顶部 5% 分位附近窄带的
    中位中心。这样不会让远处衣领、手或证件决定支点，也不依赖嘴部关键点。
    """
    mask = np.asarray(neck_mask) > 0
    box = np.asarray(face_box, dtype=np.float64).reshape(4)
    if mask.ndim != 2 or not np.isfinite(box).all():
        return None
    h_img, w_img = mask.shape
    bw, bh = max(box[2] - box[0], 1.0), max(box[3] - box[1], 1.0)
    cx = 0.5 * (box[0] + box[2])
    if face_kps is not None:
        pts = np.asarray(face_kps, dtype=np.float64)
        if pts.shape[0] >= 3 and np.isfinite(pts[:3]).all():
            eye = 0.5 * (pts[0] + pts[1])
            cx = 0.75 * eye[0] + 0.25 * pts[2, 0]
    x0 = max(0, int(math.floor(cx - 0.42 * bw)))
    x1 = min(w_img, int(math.ceil(cx + 0.42 * bw)) + 1)
    y0 = max(0, int(math.floor(box[3] - 0.18 * bh)))
    y1 = min(h_img, int(math.ceil(box[3] + 0.55 * bh)) + 1)
    roi = mask[y0:y1, x0:x1]
    ys, xs = np.nonzero(roi)
    if len(xs) < 12:
        return None
    ys = ys + y0
    xs = xs + x0
    top = float(np.percentile(ys, 5))
    band = (ys >= top - 1.0) & (ys <= top + max(5.0, 0.045 * bh))
    if int(band.sum()) < 4:
        return None
    # neck 顶边常被下颌遮挡成左右两个不等大的细片。直接取顶带全部像素中位数会
    # 在某一侧细片面积变化时横跳 50~100px（实片 frame0→299 的失败根因）。
    # X 改由下方已连成完整脖子的 carrier band 决定：逐行取左右外边界中点，再
    # 对行中点取中位数。Y 仍保留真实顶部，故不会把连接点下移到衣领。
    carrier_y0 = max(y0, int(round(box[3] + 0.04 * bh)))
    carrier_y1 = min(y1, int(round(box[3] + 0.18 * bh)) + 1)
    mids = []
    min_span = max(6.0, 0.25 * bw)
    for gy in range(carrier_y0, carrier_y1):
        row_x = np.flatnonzero(mask[gy, x0:x1]) + x0
        if len(row_x) >= 4 and float(row_x[-1] - row_x[0]) >= min_span:
            mids.append(0.5 * float(row_x[0] + row_x[-1]))
    pivot_x = float(np.median(mids)) if mids else float(
        0.75 * cx + 0.25 * np.median(xs[band])
    )
    return np.array([pivot_x, float(np.median(ys[band]))], dtype=np.float64)


def _fallback_neck_pivot(face_box: np.ndarray, face_kps: np.ndarray) -> np.ndarray:
    """neck mask 不可靠时的显式肩颈近似；不读取嘴点。"""
    box = np.asarray(face_box, dtype=np.float64)
    pts = np.asarray(face_kps, dtype=np.float64)
    eye = 0.5 * (pts[0] + pts[1])
    nose = pts[2]
    x = 0.75 * eye[0] + 0.25 * nose[0]
    y = box[3] + 0.03 * max(box[3] - box[1], 1.0)
    return np.array([x, y], dtype=np.float64)


def smooth_point_track(points: np.ndarray, window: int = 7) -> np.ndarray:
    """Hampel + 对称平滑二维点；窗口必须为正奇数，零相位。"""
    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 2 or not np.isfinite(pts).all():
        raise ValueError("point track 必须为有限 N×2")
    if window < 1 or window % 2 == 0:
        raise ValueError("point smooth window 必须为正奇数")
    out = np.empty_like(pts)
    for axis in range(2):
        clean = hampel(pts[:, axis], window=window)
        out[:, axis] = centered_smooth(clean, window=window)
    return out


def resolve_point_track(
    points: list[np.ndarray | None],
    fallbacks: list[np.ndarray],
    max_gap: int = 5,
) -> tuple[np.ndarray, list[str]]:
    """短缺失在相邻可靠点之间插值，长缺失显式使用 fallback。"""
    if len(points) != len(fallbacks) or not points:
        raise ValueError("point/fallback 轨迹长度不一致或为空")
    n = len(points)
    out: list[np.ndarray | None] = [
        None if p is None else np.asarray(p, dtype=np.float64) for p in points
    ]
    sources = ["raw_neck" if p is not None else "missing" for p in points]
    i = 0
    while i < n:
        if out[i] is not None:
            i += 1
            continue
        start = i
        while i < n and out[i] is None:
            i += 1
        end = i
        gap = end - start
        left = start - 1
        right = end
        if gap <= max(0, int(max_gap)) and left >= 0 and right < n:
            p0, p1 = out[left], out[right]
            assert p0 is not None and p1 is not None
            for j in range(start, end):
                t = (j - left) / (right - left)
                out[j] = (1.0 - t) * p0 + t * p1
                sources[j] = "interpolated"
        else:
            for j in range(start, end):
                out[j] = np.asarray(fallbacks[j], dtype=np.float64)
                sources[j] = "face_fallback"
    return np.asarray(out, dtype=np.float64), sources


def build_neck_pivot_transforms(
    a_kps: list[np.ndarray],
    b_kps: list[np.ndarray],
    b_boxes: list[np.ndarray],
    neck_pivots: np.ndarray,
    *,
    smooth_window: int = 7,
    offset: tuple[float, float] = (0.0, 0.0),
) -> tuple[list[tuple[float, float, float, float]], dict]:
    """构造“常量 scale/angle + 动态支点平移”的逐帧外部变换。

    scale 和基础角只做 A/B 画布标定，全片恒定；每帧唯一动态二维自由度是把
    B 的 Q 对齐到 A 的 P。返回的误差以同一矩阵重算，供发现坐标系/矩阵不一致。
    """
    n = len(a_kps)
    if not (n and len(b_kps) == len(b_boxes) == len(neck_pivots) == n):
        raise ValueError("pivot transform 输入轨迹长度不一致或为空")
    scales, angles, q_raw = [], [], []
    for ka, kb, bb in zip(a_kps, b_kps, b_boxes):
        ka = np.asarray(ka, dtype=np.float64)
        kb = np.asarray(kb, dtype=np.float64)
        va, vb = ka[1] - ka[0], kb[1] - kb[0]
        na, nb = float(np.linalg.norm(va)), float(np.linalg.norm(vb))
        if na < 1e-6 or nb < 1e-6:
            raise ValueError("双眼距离为 0，无法标定 pivot transform")
        scales.append(na / nb)
        angles.append(math.atan2(va[1], va[0]) - math.atan2(vb[1], vb[0]))
        q = estimate_head_attachment(kb, bb)
        if q is None:
            raise ValueError("B 头颈连接点估计失败")
        q_raw.append(q)
    scale = float(np.median(scales))
    angle = float(np.median(np.unwrap(np.asarray(angles, dtype=np.float64))))
    p_used = smooth_point_track(np.asarray(neck_pivots), window=smooth_window)
    q_used = smooth_point_track(np.asarray(q_raw), window=smooth_window)
    target = p_used + np.asarray(offset, dtype=np.float64)[None, :]
    c, sn = scale * math.cos(angle), scale * math.sin(angle)
    linear = np.array([[c, -sn], [sn, c]], dtype=np.float64)
    params, errors = [], []
    transformed = []
    for p, q in zip(target, q_used):
        translation = p - linear @ q
        item = (scale, angle, float(translation[0]), float(translation[1]))
        params.append(item)
        mapped = transform_point(q, rebuild(*item))
        transformed.append(mapped)
        errors.append(float(np.linalg.norm(mapped - p)))
    return params, {
        "scale_const": scale,
        "angle_const_rad": angle,
        "p_neck_used": p_used.tolist(),
        "q_attach_raw": np.asarray(q_raw).tolist(),
        "q_attach_used": q_used.tolist(),
        "q_attach_full": np.asarray(transformed).tolist(),
        "attachment_error_px": errors,
        "offset": [float(offset[0]), float(offset[1])],
    }


# ---------------------------------------------------------------- 离线滤波（§17.5.3）

def hampel(series: np.ndarray, window: int = 7, sigma: float = 3.0) -> np.ndarray:
    """滑窗中位数 + MAD 的尖峰剔除，异常点替换为窗口中位数。"""
    n = len(series)
    half = max(1, window // 2)
    out = series.astype(np.float64).copy()
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        win = out[lo:hi]
        med = np.median(win)
        mad = np.median(np.abs(win - med)) * 1.4826 + 1e-9
        if abs(out[i] - med) > sigma * mad:
            out[i] = med
    return out


def soft_cut(height: int, cut_y: float, soft: float) -> np.ndarray:
    """纵向软切割：cut_y-soft 以上为 1，cut_y+soft 以下为 0。"""
    ys = np.arange(height, dtype=np.float32)[:, None]
    ramp = (cut_y + soft - ys) / (2.0 * soft)
    return np.clip(ramp, 0.0, 1.0)


def centered_smooth(series: np.ndarray, window: int = 11) -> np.ndarray:
    """居中滑动平均：零相位延迟（离线两遍式用，不引入因果滞后）。"""
    n = len(series)
    half = max(1, window // 2)
    out = np.zeros(n, dtype=np.float64)
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        # 边缘缩窗，避免把序列长度削短或引入偏移
        out[i] = float(np.mean(series[lo:hi]))
    return out


def offline_filter(
    raw: list,
    hampel_window: int = 7,
    smooth_window: int = 11,
    scale_mode: str = "smooth",
    angle_window: int = 0,
) -> list:
    """对逐帧 (s, angle, tx, ty) 做 Hampel 去尖峰 + 居中零相位平滑。

    - angle 先解卷绕再平滑，最后映射回 (-pi, pi]（%2pi 会把微小负角变成 ~2pi）；
    - scale_mode="const" 时取全程中位数：固定机位+人物距离不变时，逐帧 scale 只是
      把歪头透视（B 内部动画已复现）重复补偿，反而造成"呼吸"；常量才是物理正确；
    - angle_window 可单独加大：roll 信号极小时（<1°），强平滑只留慢趋势。
    """
    arr = np.array(raw, dtype=np.float64)
    if len(arr) == 0:
        return []
    angles = np.unwrap(arr[:, 1])
    out = np.zeros_like(arr)
    for j, col in enumerate((arr[:, 0], angles, arr[:, 2], arr[:, 3])):
        col = hampel(col, window=hampel_window)
        win = smooth_window
        if j == 0 and scale_mode == "const":
            col = np.full_like(col, float(np.median(col)))
            win = 1
        if j == 0 and scale_mode == "smooth_clamped":
            # §28.10 K2：smooth 21 + clamp 到中位数±1%（前后摆存在但防"呼吸"）
            col = centered_smooth(col, window=21)
            med = float(np.median(col))
            col = np.clip(col, med * 0.99, med * 1.01)
            win = 1
        if j == 1 and angle_window > win:
            win = angle_window
        out[:, j] = centered_smooth(col, window=win) if win > 1 else col
    out[:, 1] = np.angle(np.exp(1j * out[:, 1]))  # 映射回 (-pi, pi]
    return [tuple(row) for row in out]


class SmoothedTransform:
    """在线因果滤波（旧版行为，仅 A/B 对照保留）：滑窗中位数 + EMA。"""

    def __init__(self, rot: float, trans: float, window: int = 9) -> None:
        self.rot = rot
        self.trans = trans
        self.window = max(3, int(window))
        self._history: list[tuple[float, float, float, float]] = []
        self._state = None

    def update(self, m: np.ndarray, scale_bias: float, dx: float, dy: float) -> np.ndarray:
        self._history.append(decompose(m))
        if len(self._history) > self.window:
            self._history.pop(0)
        med = tuple(float(np.median([h[i] for h in self._history])) for i in range(4))
        if self._state is None:
            self._state = med
        else:
            ps, pa, ptx, pty = self._state
            ms, ma, mtx, mty = med
            da = (ma - pa + math.pi) % (2 * math.pi) - math.pi
            self._state = (
                self.rot * ps + (1 - self.rot) * ms,
                pa + (1 - self.rot) * da,
                self.trans * ptx + (1 - self.trans) * mtx,
                self.trans * pty + (1 - self.trans) * mty,
            )
        s2, a2, tx2, ty2 = self._state
        return rebuild(s2 * scale_bias, a2, tx2 + dx, ty2 + dy)


# ---------------------------------------------------------------- alpha 与补洞（§17.4 / §17.3）

def warp_premultiplied(
    frame_b: np.ndarray, alpha_src: np.ndarray, m: np.ndarray, size: tuple[int, int]
) -> tuple[np.ndarray, np.ndarray]:
    """预乘 RGB 与 alpha 分别 warp，返回（去预乘的 B 头 RGB, warp 后 alpha）。

    B 肖像背景（mask 外像素）预乘后为 0，warp 插值不会把它带进成片；
    之前的做法直接 warp RGB 再按 alpha 混合，羽化/插值会把白背景卷进来。
    """
    a = np.clip(alpha_src.astype(np.float32), 0.0, 1.0)
    premult = frame_b.astype(np.float32) * a[..., None]
    w, h = size
    kw = dict(flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    w_pre = cv2.warpAffine(premult, m, (w, h), **kw)
    w_a = cv2.warpAffine(a, m, (w, h), **kw)
    safe_a = np.maximum(w_a, 1e-6)
    rgb = np.where(w_a[..., None] > 0.02, w_pre / safe_a[..., None], 0.0)
    return rgb, w_a


def inner_feather_alpha(
    warped_alpha: np.ndarray, feather_px: float, support_eps: float = 0.5
):
    """内部距离变换羽化：support 外严格为 0，过渡只发生在轮廓内侧（§20.5.3/§22.4）。

    alpha = min(内距 alpha, warp 后软 alpha)：保留 warp/matting/neck collar 自带的
    软信息。support_eps 默认 0.5 —— 硬 mask 输入下与 v2 行为逐位一致（Round E/F
    必须保持 alpha 与 v2 不变，§22.5.6）；Round G 的 helper 显式传 0.01 以保留
    <0.5 的渐变尾部不被截断（§22 Q2-2 前提）。
    返回 (alpha, transition_width_px_mean)：0<alpha<1 像素构成的平均过渡带宽。
    """
    wa = np.clip(warped_alpha.astype(np.float32), 0.0, 1.0)
    support = (wa > float(support_eps)).astype(np.uint8)
    dist = cv2.distanceTransform(support, cv2.DIST_L2, 3)
    inner = np.clip(dist / max(float(feather_px), 1e-6), 0.0, 1.0)
    alpha = np.minimum(inner, wa)
    alpha[wa <= float(support_eps)] = 0.0
    band = (alpha > 1e-3) & (alpha < 1.0 - 1e-3)
    if band.any():
        contours, _ = cv2.findContours(support, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        perimeter = sum(cv2.arcLength(c, True) for c in contours)
        width = float(band.sum() / max(perimeter, 1.0))
    else:
        width = 0.0
    return alpha, width


def fit_plane_fill(
    frame: np.ndarray, residual: np.ndarray, protect: np.ndarray, ring_width: int = 30
) -> tuple[np.ndarray, dict]:
    """只在 residual 区域做局部背景补洞（白墙场景：采样环鲁棒平面拟合）。

    采样环 = residual 外扩 ring_width 的环带，排除 protect（脖子/皮肤/证件）；
    每通道拟合 z = ax + by + c，2.5σ 离群迭代剔除（挡掉衣服/手等前景残留）；
    只回填 residual，边缘 3px 软融合。拟合质量差时回退 cv2.inpaint。
    protect 内像素绝不修改。
    """
    h, w = frame.shape[:2]
    out = frame.copy()
    stats = {"mode": "plane", "filled_px": 0, "fit_rms": 0.0, "fallback": False}
    if not residual.any():
        return out, stats

    k_outer = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ring_width * 2 + 1, ring_width * 2 + 1))
    k_inner = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    res_u8 = (residual > 0).astype(np.uint8) * 255
    outer = cv2.dilate(res_u8, k_outer) > 0
    inner = cv2.dilate(res_u8, k_inner) > 0
    ring = outer & ~inner & ~(protect > 0)
    ys, xs = np.nonzero(ring)
    if len(ys) < 300:
        stats["mode"] = "inpaint"
        stats["fallback"] = True
        filled = cv2.inpaint(frame, res_u8, 5, cv2.INPAINT_TELEA)
    else:
        filled = frame.copy()
        colors = frame[ys, xs].astype(np.float64)
        X = np.stack([xs, ys, np.ones_like(xs, dtype=np.float64)], axis=1)
        keep = np.ones(len(xs), dtype=bool)
        coefs = None
        min_keep = max(300, int(0.08 * len(xs)))
        coef = None
        for _ in range(4):  # 鲁棒迭代剔除衣服/肩膀等离群前景（实测环带双峰，需多轮）
            coef, *_ = np.linalg.lstsq(X[keep], colors[keep], rcond=None)
            resid = np.abs(colors - X @ coef).sum(axis=1)
            sigma = resid[keep].std() + 1e-6
            new_keep = resid < 2.2 * sigma
            if new_keep.sum() < min_keep:
                break
            keep = new_keep
            coefs = coef
        if coefs is None:
            coefs = coef
        hy, hx = np.nonzero(residual)
        H = np.stack([hx, hy, np.ones_like(hx, dtype=np.float64)], axis=1)
        patch = np.clip(H @ coefs, 0, 255).astype(np.uint8)
        filled[hy, hx] = patch
        ref = frame[ys[keep], xs[keep]].astype(np.float64)
        stats["fit_rms"] = float(np.sqrt(np.mean((ref - X[keep] @ coefs) ** 2)))
        if stats["fit_rms"] > 36.0:  # 背景非平面（纹理墙），降级 inpaint
            stats["mode"] = "inpaint"
            stats["fallback"] = True
            filled = cv2.inpaint(frame, res_u8, 5, cv2.INPAINT_TELEA)

    # 边缘 3px 软融合，且绝不碰 protect
    mask_blur = cv2.GaussianBlur(res_u8, (7, 7), 0).astype(np.float32) / 255.0
    mask_blur[protect > 0] = 0.0
    blend = mask_blur[..., None]
    out = np.clip(filled.astype(np.float32) * blend + out.astype(np.float32) * (1 - blend), 0, 255).astype(np.uint8)
    stats["filled_px"] = int(((residual) & (protect == 0)).sum())
    return out, stats


# ---------------------------------------------------------------- 第三轮（docs §20）

def wall_seed_mask(shape, face_box, exclusion: np.ndarray) -> np.ndarray:
    """头顶上方的横向墙面种子带（§20.3.3）：以 face box 为相对坐标，避开头发，
    不取下方脖子/衣服；再排除旧头安全区与补洞保护区。"""
    h, w = shape[:2]
    bx0, by0, bx1, by1 = [float(v) for v in face_box]
    bw, bh = bx1 - bx0, by1 - by0

    x0 = max(0, int(bx0 - 0.65 * bw))
    x1 = min(w, int(bx1 + 0.65 * bw))
    y0 = max(0, int(by0 - 0.70 * bh))
    y1 = max(y0 + 1, int(by0 - 0.20 * bh))

    seed = np.zeros((h, w), dtype=bool)
    seed[y0:y1, x0:x1] = True
    seed &= ~exclusion.astype(bool)
    return seed


def select_wall_samples(
    frame: np.ndarray,
    ring: np.ndarray,
    old_head_safe: np.ndarray,
    fill_protect: np.ndarray,
    face_box: np.ndarray,
    delta_e_threshold: float = 10.0,
):
    """墙面样本 = 采样环 ∩ 旧头安全区外 ∩ 保护区外 ∩ 与墙面种子 ΔE 达标（§20.3.4）。

    关键：ring 中必须排除整个 old_head_safe，而不是只排 residual 自身——否则
    旧头内部的皮肤像素会被当成墙面候选（v2 米黄色光晕的第一根因）。
    返回 (samples, wall_lab, seed)；种子不足 200px 时 samples 为 None。
    """
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB).astype(np.float32)
    exclusion = old_head_safe.astype(bool) | fill_protect.astype(bool)
    seed = wall_seed_mask(frame.shape, face_box, exclusion)
    if int(seed.sum()) < 200:
        return None, None, seed
    wall_lab = np.median(lab[seed], axis=0)
    delta_e = np.linalg.norm(lab - wall_lab[None, None, :], axis=2)
    samples = (
        ring.astype(bool)
        & (~old_head_safe.astype(bool))
        & (~fill_protect.astype(bool))
        & (delta_e <= float(delta_e_threshold))
    )
    return samples, wall_lab, seed


def _fit_wall_plane(
    xs: np.ndarray, ys: np.ndarray, colors: np.ndarray, min_keep: int
) -> tuple[np.ndarray | None, np.ndarray]:
    """MAD 鲁棒逐通道平面拟合（§20.3.5）：围绕中位数的 MAD 剔除，且必须用最终
    keep 重新拟合一次（不能沿用上一轮 coef）。返回 (coef, keep)。"""
    X = np.stack([xs, ys, np.ones_like(xs, dtype=np.float64)], axis=1)
    keep = np.ones(len(xs), dtype=bool)
    coef = None
    for _ in range(4):
        coef, *_ = np.linalg.lstsq(X[keep], colors[keep], rcond=None)
        pred = X @ coef
        error = np.linalg.norm(colors - pred, axis=1)
        med = np.median(error[keep])
        mad = 1.4826 * np.median(np.abs(error[keep] - med)) + 1e-6
        new_keep = np.abs(error - med) <= 3.0 * mad
        if int(new_keep.sum()) < min_keep:
            break
        if np.array_equal(new_keep, keep):
            keep = new_keep
            break
        keep = new_keep
    # 必须使用最终 keep 再拟合一次
    coef, *_ = np.linalg.lstsq(X[keep], colors[keep], rcond=None)
    return coef, keep


@dataclass
class WallModelState:
    """墙面平面模型状态（§22.3.1）。coef 形如 [[a],[b],[c]]×3 通道：z = ax+by+c。"""

    coef: np.ndarray | None = None
    wall_lab: np.ndarray | None = None
    source: str = "none"


def choose_fallback_wall_model(
    previous_state: WallModelState | None,
    global_state: WallModelState | None,
    wall_lab: np.ndarray | None,
) -> WallModelState | None:
    """墙面模型降级链（§22.3.1）：上一帧 → 任务级全局 → 种子中位色常量平面。

    固定机位下复用历史墙面模型，远比邻域 inpaint 可靠——residual 紧贴旧脸/
    耳朵/脖子，TELEA 会从皮肤侧传播颜色，重新生成米黄色光晕（§22 Q2-1 裁决）。
    """
    if previous_state is not None and previous_state.coef is not None:
        return WallModelState(
            coef=previous_state.coef.copy(),
            wall_lab=None if previous_state.wall_lab is None else previous_state.wall_lab.copy(),
            source=previous_state.source if previous_state.source != "current_frame" else "previous_frame",
        )
    if global_state is not None and global_state.coef is not None:
        return WallModelState(
            coef=global_state.coef.copy(),
            wall_lab=None if global_state.wall_lab is None else global_state.wall_lab.copy(),
            source=global_state.source if global_state.source != "current_frame" else "global",
        )
    if wall_lab is not None:
        # 常量颜色也是平面：z = 0*x + 0*y + median
        coef = np.zeros((3, 3), dtype=np.float64)
        wall_bgr = cv2.cvtColor(
            wall_lab.reshape(1, 1, 3).astype(np.uint8), cv2.COLOR_LAB2BGR
        )[0, 0].astype(np.float64)
        coef[2, :] = wall_bgr
        return WallModelState(coef=coef, wall_lab=wall_lab.copy(), source="seed_median")
    return None


def _boundary_wall_delta_e(
    filled_lab: np.ndarray,
    boundary_ys: np.ndarray,
    boundary_xs: np.ndarray,
    sample_ys: np.ndarray,
    sample_xs: np.ndarray,
    sample_lab: np.ndarray,
    shape,
    cell: int = 64,
):
    """边界 ΔE（§22.3.3）：residual 外沿补洞颜色 vs 其附近（3x3 粗网格邻域）
    可信墙面样本的均值颜色。比"全部补洞像素 vs 全局中位色"更贴近肉眼对
    边界的感受（墙面有光照渐变时）。无邻近样本的像素不计入。"""
    if len(boundary_ys) == 0 or len(sample_ys) == 0:
        return None, None
    gh, gw = shape[0] // cell + 1, shape[1] // cell + 1
    acc = np.zeros((gh, gw, 3), dtype=np.float64)
    cnt = np.zeros((gh, gw), dtype=np.float64)
    np.add.at(acc, (sample_ys // cell, sample_xs // cell), sample_lab)
    np.add.at(cnt, (sample_ys // cell, sample_xs // cell), 1.0)
    # 3x3 邻域聚合
    acc_p = np.pad(acc, ((1, 1), (1, 1), (0, 0)))
    cnt_p = np.pad(cnt, ((1, 1), (1, 1)))
    nb_acc = np.zeros_like(acc)
    nb_cnt = np.zeros_like(cnt)
    for dy in range(3):
        for dx in range(3):
            nb_acc += acc_p[dy : dy + gh, dx : dx + gw]
            nb_cnt += cnt_p[dy : dy + gh, dx : dx + gw]
    gys = np.minimum(boundary_ys // cell, gh - 1)
    gxs = np.minimum(boundary_xs // cell, gw - 1)
    n = nb_cnt[gys, gxs]
    valid = n > 0
    if not valid.any():
        return None, None
    local = nb_acc[gys, gxs][valid] / n[valid][:, None]
    de = np.linalg.norm(filled_lab[valid] - local, axis=1)
    return round(float(de.mean()), 3), round(float(np.percentile(de, 95)), 3)


def fit_wall_fill(
    frame: np.ndarray,
    residual: np.ndarray,
    old_head_safe: np.ndarray,
    fill_protect: np.ndarray,
    face_box: np.ndarray,
    previous_state: WallModelState | None = None,
    global_state: WallModelState | None = None,
    ring_width: int = 30,
    wall_delta_e: float = 10.0,
    outer_feather_px: int = 0,
    min_samples: int = 300,
    max_texture: float = 0.0,
) -> tuple[np.ndarray, dict, WallModelState]:
    """墙面种子 + ΔE 门限 + MAD 平面拟合的差集补洞（§20.3 + §22.3 修正版）。

    与 v2 fit_plane_fill 的关键差异：
    1. 采样环排除整个 old_head_safe（不只 residual 自身）；
    2. 样本必须与头顶上方墙面种子的 LAB ΔE <= wall_delta_e——低 RMS 不再能
       证明拟合正确（肤色簇自身也可以很低 RMS，§20.2.2）；
    3. residual 内 100% 写入拟合结果，不与原帧混回（§20.3.6）；
    4. 当前帧拟合失败时走 previous→global→seed_median 墙面模型降级链，
       禁止原帧 TELEA inpaint（会从皮肤侧传播颜色，§22 Q2-1 裁决）；
    5. 指标：fill_wall_delta_e_*（vs 墙面中位色）+ fill_boundary_delta_e_*
       （residual 外沿 vs 附近可信墙面样本，§22.3.3）；
    6. outer_feather_px>0 时按 §22.3.2 的距离衰减权重做外侧真融合；
       Round E 固定为 0（只做 100% 替换）；
    7. max_texture>0 时计算样本区纹理能量（§30.8）：超阈**显式报错拒绝**——
       平面模型只适用于纯色/缓变背景，窗帘/书架等复杂纹理必须改用
       clean_plate/temporal_plate/视频修复，不允许静默生成平色补丁。

    返回 (clean_base, stats, state)；state 供下一帧 previous_state 链式复用。
    """
    out = frame.copy()
    stats = {
        "mode": "wall_plane", "filled_px": 0, "fit_rms": 0.0,
        "fallback": False, "fallback_source": "",
        "wall_seed_px": 0, "wall_sample_px": 0, "wall_sample_purity": 0.0,
        "fill_wall_delta_e_mean": None, "fill_wall_delta_e_max": None,
        "fill_boundary_delta_e_mean": None, "fill_boundary_delta_e_p95": None,
    }
    if not residual.any():
        return out, stats, previous_state if previous_state is not None else WallModelState()

    res_bool = residual.astype(bool)
    res_u8 = res_bool.astype(np.uint8) * 255
    excl = old_head_safe.astype(bool) | fill_protect.astype(bool)
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB).astype(np.float32)

    # 采样环：residual 外扩 ring_width 的环带，排除旧头安全区与保护区
    k_outer = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ring_width * 2 + 1, ring_width * 2 + 1))
    k_inner = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    outer = cv2.dilate(res_u8, k_outer) > 0
    inner = cv2.dilate(res_u8, k_inner) > 0
    ring = outer & ~inner & ~excl

    samples, wall_lab, seed = select_wall_samples(
        frame, ring, old_head_safe, fill_protect, face_box, wall_delta_e
    )
    stats["wall_seed_px"] = int(seed.sum())
    ring_valid_px = int(ring.sum())

    if max_texture > 0 and samples is not None and int(samples.sum()) >= min_samples:
        energy = wall_texture_energy(frame, samples)
        stats["wall_texture_energy"] = round(energy, 3)
        if energy > max_texture:
            raise ValueError(
                f"背景纹理能量 {energy:.1f} 超过 smooth_plane 上限 {max_texture}："
                "平面墙模型拒绝复杂背景（窗帘/纹理墙），请改用 clean_plate/"
                "temporal_plate/视频修复（§30.8），不得静默生成平色补丁"
            )
    elif max_texture > 0 and seed is not None and int(seed.sum()) >= 200:
        energy = wall_texture_energy(frame, seed)
        stats["wall_texture_energy"] = round(energy, 3)
        if energy > max_texture:
            raise ValueError(
                f"背景纹理能量 {energy:.1f} 超过 smooth_plane 上限 {max_texture}："
                "平面墙模型拒绝复杂背景（§30.8）"
            )

    state: WallModelState | None = None
    sample_ys = sample_xs = None
    if samples is not None and int(samples.sum()) >= min_samples:
        stats["wall_sample_px"] = int(samples.sum())
        stats["wall_sample_purity"] = round(float(samples.sum()) / max(ring_valid_px, 1), 4)
        sample_ys, sample_xs = np.nonzero(samples)
        colors = frame[sample_ys, sample_xs].astype(np.float64)
        coef, keep = _fit_wall_plane(
            sample_xs, sample_ys, colors, max(min_samples, int(0.08 * len(sample_ys)))
        )
        Xk = np.stack(
            [sample_xs[keep], sample_ys[keep], np.ones(int(keep.sum()), dtype=np.float64)], axis=1
        )
        stats["fit_rms"] = float(np.sqrt(np.mean((colors[keep] - Xk @ coef) ** 2)))
        if stats["fit_rms"] <= 36.0:
            state = WallModelState(coef=coef, wall_lab=wall_lab.copy(), source="current_frame")

    if state is None:
        fallback = choose_fallback_wall_model(previous_state, global_state, wall_lab)
        if fallback is None:
            raise RuntimeError("当前帧及历史帧均无可信墙面模型（种子、样本、全局模型全部缺失）")
        state = fallback
        stats["fallback"] = True
        stats["fallback_source"] = fallback.source
        stats["mode"] = fallback.source

    coef = state.coef
    # residual 内 100% 替换为墙面平面预测，不与原帧混回
    hy, hx = np.nonzero(res_bool)
    H = np.stack([hx, hy, np.ones_like(hx, dtype=np.float64)], axis=1)
    patch = np.clip((H @ coef).reshape(-1, 1, 3), 0, 255).astype(np.uint8)
    out[hy, hx] = patch.reshape(-1, 3)

    # 外侧墙面边界真融合（§22.3.2 距离衰减权重；Round E 配置为 0 不启用）
    if outer_feather_px > 0:
        k_fe = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (outer_feather_px * 2 + 1, outer_feather_px * 2 + 1)
        )
        band = (cv2.dilate(res_u8, k_fe) > 0) & ~res_bool & ~excl
        if band.any():
            dist = cv2.distanceTransform((~res_bool).astype(np.uint8), cv2.DIST_L2, 3)
            weight = np.clip(1.0 - dist / float(outer_feather_px + 1), 0.0, 1.0)
            byy, bxx = np.nonzero(band)
            B = np.stack([bxx, byy, np.ones_like(bxx, dtype=np.float64)], axis=1)
            patch_b = np.clip(B @ coef, 0, 255)
            w = weight[byy, bxx][:, None]
            out[byy, bxx] = np.clip(
                out[byy, bxx].astype(np.float32) * (1.0 - w) + patch_b.astype(np.float32) * w,
                0, 255,
            ).astype(np.uint8)

    # 质量指标：补洞像素 vs 墙面参考色（种子或状态携带的 LAB）
    wall_lab_ref = wall_lab if wall_lab is not None else state.wall_lab
    if wall_lab_ref is not None:
        patch_lab = cv2.cvtColor(patch, cv2.COLOR_BGR2LAB).astype(np.float32).reshape(-1, 3)
        de = np.linalg.norm(patch_lab - wall_lab_ref[None, :], axis=1)
        stats["fill_wall_delta_e_mean"] = round(float(de.mean()), 3)
        stats["fill_wall_delta_e_max"] = round(float(de.max()), 3)
        if sample_ys is not None:
            # residual 外沿 ~1px 壳 vs 附近可信墙面样本（§22.3.3）
            shell = res_bool & ~(cv2.erode(res_u8, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))) > 0)
            if shell.any():
                syy, sxx = np.nonzero(shell)
                shell_lab = cv2.cvtColor(
                    out[syy, sxx].reshape(-1, 1, 3), cv2.COLOR_BGR2LAB
                ).astype(np.float32).reshape(-1, 3)
                stats["fill_boundary_delta_e_mean"], stats["fill_boundary_delta_e_p95"] = _boundary_wall_delta_e(
                    shell_lab, syy, sxx, sample_ys, sample_xs, lab[sample_ys, sample_xs], frame.shape
                )

    stats["filled_px"] = int(res_bool.sum())
    stats["_samples"] = samples if samples is not None else np.zeros_like(res_u8, dtype=bool)
    stats["_seed"] = seed
    return out, stats, state


def build_neck_keep_mask(skins_mask: np.ndarray, neck_keep_y: float) -> np.ndarray:
    """只保留缝合线以下的 A 脖子（§20.4.2 短期几何方案）。"""
    h, w = skins_mask.shape[:2]
    yy = np.arange(h, dtype=np.float32)[:, None]
    return (skins_mask > 0) & (yy >= float(neck_keep_y))


def build_fill_protect_mask(
    frame_a: np.ndarray,
    face_box: np.ndarray,
    skins_mask: np.ndarray | None,
    neck_keep_y: float,
    extra_masks: tuple = (),
) -> np.ndarray:
    """补洞保护区（§20.4）：只保护真正不能改的前景——缝合线以下的 A 脖子，
    以及外部传入的手/证件/衣服 mask。A 的旧脸、旧耳朵、旧下颌即使属于 skin
    也允许清理；它们仍留在 color_reference_mask 里做色彩参考。"""
    protect = np.zeros(frame_a.shape[:2], np.uint8)
    if skins_mask is not None:
        protect[build_neck_keep_mask(skins_mask, neck_keep_y)] = 255
    for extra in extra_masks:
        if extra is not None:
            protect[extra > 0] = 255
    return protect


def trim_hard_matte(mask_b: np.ndarray, erode_px: int) -> np.ndarray:
    """B 硬 mask 轻微内缩（§20.5.2）：去掉抗锯齿/pasteback/编码混色造成的
    1~2px 白色 matte。erode_px<=0 时仅二值化。"""
    out = (mask_b > 0).astype(np.uint8) * 255
    if erode_px <= 0:
        return out
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (erode_px * 2 + 1, erode_px * 2 + 1))
    return cv2.erode(out, k, iterations=1)


def build_neck_collar_mask(neck_mask: np.ndarray, face_box: np.ndarray, ratio: float = 0.12) -> np.ndarray:
    """B 源空间的窄 neck collar 窗口（§20.6.3）：脸下缘到下颌 + ratio*脸高，
    横向只留脸宽附近，避免带入肩膀/衣服。返回硬 mask（0/255），
    纵向软过渡在 warp 到 A 画布后由 neck_vertical_ramp 单独施加——
    若在源空间就做 ramp，inner_feather_alpha 的 0.5 二值化会把 <0.5 的
    渐变尾部硬切断。"""
    h, w = neck_mask.shape[:2]
    bx0, by0, bx1, by1 = [float(v) for v in face_box]
    bw, bh = bx1 - bx0, by1 - by0

    top = int(by1 - 0.02 * bh)
    bottom = int(by1 + ratio * bh)
    bottom = max(top + 1, min(h, bottom))
    x0 = max(0, int(bx0 - 0.08 * bw))
    x1 = min(w, int(bx1 + 0.08 * bw))

    allowed = np.zeros((h, w), dtype=bool)
    allowed[top:bottom, x0:x1] = True
    return ((neck_mask > 0) & allowed).astype(np.uint8) * 255


def neck_vertical_ramp(shape, bottom_y: float, soft_px: float) -> np.ndarray:
    """A 画布上下颌→脖子的纵向软过渡（§20.6.4，10~18px）：bottom_y-soft 以上 1，
    bottom_y 以下 0，单调下降。"""
    h, w = shape[:2]
    yy = np.arange(h, dtype=np.float32)[:, None]
    return np.clip((float(bottom_y) - yy) / max(float(soft_px), 1e-6), 0.0, 1.0)


def bump_mode_count(fill_mode_frames: dict, mode: str) -> None:
    """diag 计数防 KeyError（§22.3.4）：新 mode 键（wall_plane/previous_frame/
    global/seed_median）直接补 0 再自增。"""
    key = str(mode if mode else "unknown")
    fill_mode_frames[key] = fill_mode_frames.get(key, 0) + 1


def strip_debug_arrays(stats: dict) -> dict:
    """弹出 stats 中的 ndarray 调试字段（_samples/_seed），使剩余部分可 JSON
    序列化（§22.3.4：不得把 1080x1920 bool ndarray 写进逐帧 JSON）。"""
    stats.pop("_samples", None)
    stats.pop("_seed", None)
    return stats


def compose_debug_grid(panels: list, size: tuple[int, int]) -> np.ndarray:
    """§20.8 的 3×3 九格调试图。panels 必须恰好 9 张，第 9 张是最终 out——
    不允许再出现"声称 7 联实际 6 格"的情况（§22.9 第 14 条）。"""
    if len(panels) != 9:
        raise ValueError(f"debug grid 需要 9 个面板，收到 {len(panels)}")
    w, h = size
    sw, sh = w // 3, h // 3
    rows = []
    for r in range(3):
        rows.append(np.hstack([cv2.resize(p, (sw, sh)) for p in panels[r * 3 : r * 3 + 3]]))
    return np.vstack(rows)


def collar_bottom_of(a_face_box: np.ndarray, neck_collar_ratio: float) -> float:
    """collar 结束线 = A 下颌线 + ratio×A 脸高（§22.7.4）。

    build_warped_head_layers 与 fill_protect 必须经由本函数取值，
    保证 collar 底端与 A 脖子保护线是同一条线（§22.9 第 12 条）。"""
    _, _, _, aby1 = [float(v) for v in a_face_box]
    abh = float(a_face_box[3] - a_face_box[1])
    return aby1 + float(neck_collar_ratio) * abh


def b_fallback_trio(prev_box, prev_head, prev_neck):
    """B 检测失败帧：box/head/neck 三者整体沿用上一帧（§22.7.3）。"""
    if prev_box is None or prev_head is None:
        raise RuntimeError("首个可用 B 头分割不存在")
    return prev_box, prev_head, prev_neck


def build_warped_head_layers(
    frame_b: np.ndarray,
    box_b: np.ndarray,
    head_mask_b: np.ndarray,
    neck_mask_b: np.ndarray | None,
    transform: np.ndarray,
    output_size: tuple[int, int],
    head_erode_px: int,
    head_feather_px: float,
    neck_collar_ratio: float,
    neck_collar_soft_px: float,
    a_face_box: np.ndarray,
) -> dict:
    """Round G 专用：head 与 neck collar 分层 warp（§22.7.2）。

    ⚠️ v4 起 deprecated 于默认路径：B neck collar 被 §24 人工复审否决
    （矩形贴片/瘦脖接粗脖/随头独立移动）。仅供历史复现，v4 主路径
    neck_collar_enabled 必须为 false，且与 a_neck_preserve_enabled 互斥。
    """
    head_trim = trim_hard_matte(head_mask_b, head_erode_px)
    head_src = head_trim.astype(np.float32) / 255.0

    collar_src = np.zeros_like(head_src)
    if neck_mask_b is not None and (neck_mask_b > 0).any():
        collar_hard = build_neck_collar_mask(neck_mask_b, box_b, neck_collar_ratio)
        collar_src = collar_hard.astype(np.float32) / 255.0

    combined_src = np.maximum(head_src, collar_src)
    head_rgb, _ = warp_premultiplied(frame_b, combined_src, transform, output_size)

    w, h = output_size
    kw = dict(
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    head_w = cv2.warpAffine(head_src, transform, (w, h), **kw)
    collar_w = cv2.warpAffine(collar_src, transform, (w, h), **kw)

    alpha_head, transition_width = inner_feather_alpha(
        head_w, head_feather_px, support_eps=0.01
    )

    collar_bottom_y = collar_bottom_of(a_face_box, neck_collar_ratio)
    ramp = neck_vertical_ramp((h, w), collar_bottom_y, neck_collar_soft_px)
    alpha_neck = collar_w * ramp
    alpha_final = np.maximum(alpha_head, alpha_neck)

    return {
        "head_rgb": head_rgb,
        "alpha_head": alpha_head,
        "alpha_neck": alpha_neck,
        "alpha_final": alpha_final,
        "collar_bottom_y": collar_bottom_y,
        "transition_width": transition_width,
    }


# ---------------------------------------------------------------- 第四轮（docs §24）

def check_neck_mode(a_neck_preserve_enabled: bool, neck_collar_enabled: bool) -> None:
    """§24.11：A 脖子保护与 B neck collar 互斥——两者同开会重新制造
    独立运动的 neck 前景层。"""
    if a_neck_preserve_enabled and neck_collar_enabled:
        raise ValueError("a_neck_preserve_enabled 与 B neck_collar_enabled 不能同时开启")


def extend_mask_upward(mask: np.ndarray, pixels: int) -> np.ndarray:
    """neck mask 只向上延展（§24.9 原样）：逐 dy 上移取并集，不改左右宽度。"""
    src = (mask > 0).astype(np.uint8) * 255
    out = src.copy()
    for dy in range(1, max(0, int(pixels)) + 1):
        shifted = np.zeros_like(src)
        shifted[:-dy] = src[dy:]  # 将原 neck 支撑向上移动 dy
        out = cv2.max(out, shifted)
    return out


def motion_safe_neck_union(
    current_neck: np.ndarray,
    previous_neck: np.ndarray | None,
    prev_kps: np.ndarray | None,
    cur_kps: np.ndarray | None,
    upward_px: int = 3,
) -> np.ndarray:
    """A neck 时序安全保护（§24.9 原样）：当前帧 ∪ 运动补偿上一帧（双眼刚体
    变换），close 后只向上延展 upward_px。不做二值 EMA（前缘漏保护根因同
    §17.6.1）。宁可轻微多保护，不可被墙面补洞吃掉。"""
    safe = (current_neck > 0).astype(np.uint8) * 255

    if previous_neck is not None and prev_kps is not None and cur_kps is not None:
        params = rigid_from_eyes(prev_kps, cur_kps)
        if params is not None:
            m = rebuild(*params)
            warped_prev = cv2.warpAffine(
                (previous_neck > 0).astype(np.uint8) * 255,
                m,
                (safe.shape[1], safe.shape[0]),
                flags=cv2.INTER_NEAREST,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=0,
            )
            safe = cv2.max(safe, warped_prev)

    safe = cv2.morphologyEx(
        safe,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
    )
    return extend_mask_upward(safe, upward_px)


def region_aware_head_alpha(
    warped_alpha: np.ndarray,
    a_face_box: np.ndarray,
    side_feather_px: float = 4.0,
    jaw_feather_px: float = 8.0,
    jaw_start_ratio: float = 0.68,
    jaw_full_ratio: float = 0.82,
    support_eps: float = 0.01,
) -> tuple[np.ndarray, dict]:
    """头发/脸侧与下颌使用不同羽化宽度的区域化 alpha（§24.12 原样）。

    feather_map = side*(1-t) + jaw*t，t 为脸框内行位置的 smoothstep——
    避免 jaw 区起点出现水平参数断层。边界仍由 B 真实 head mask 决定，
    不生成矩形、不向 mask 外增加 B RGB。
    """
    wa = np.clip(warped_alpha.astype(np.float32), 0.0, 1.0)
    support = (wa > support_eps).astype(np.uint8)
    dist = cv2.distanceTransform(support, cv2.DIST_L2, 3)

    _, by0, _, by1 = [float(v) for v in a_face_box]
    bh = by1 - by0
    y_start = by0 + jaw_start_ratio * bh
    y_full = by0 + jaw_full_ratio * bh

    yy = np.arange(wa.shape[0], dtype=np.float32)[:, None]
    t = np.clip((yy - y_start) / max(y_full - y_start, 1.0), 0.0, 1.0)
    t = t * t * (3.0 - 2.0 * t)

    feather_map = side_feather_px * (1.0 - t) + jaw_feather_px * t
    alpha = np.clip(dist / np.maximum(feather_map, 1e-6), 0.0, 1.0)
    alpha = np.minimum(alpha, wa)
    alpha[wa <= support_eps] = 0.0

    return alpha, {
        "jaw_start_y": float(y_start),
        "jaw_full_y": float(y_full),
        "side_feather_px": float(side_feather_px),
        "jaw_feather_px": float(jaw_feather_px),
    }


def jaw_neck_gap_px(alpha_head: np.ndarray, a_neck_safe: np.ndarray, min_head_bottom_y: float | None = None):
    """B 下颌与 A neck 正面缝合区的纵向间隙（§24.19：mean<1px，max<=2px）。

    只统计：neck 中部 60% 列（正面咽喉区）且头底边降到下巴区
    （last_true >= min_head_bottom_y，建议 by1-0.05bh）。脖子两侧列的
    "间隙"是原片天然背景（下巴侧下方本来就是墙），不是接缝空隙。"""
    neck_cols = a_neck_safe.any(axis=0)
    if not neck_cols.any():
        return None, None
    xs = np.nonzero(neck_cols)[0]
    neck_cx = 0.5 * (xs[0] + xs[-1])
    half_w = 0.30 * (xs[-1] - xs[0])  # 中部 60%
    neck_top = np.argmax(a_neck_safe, axis=0).astype(np.float32)  # 每列 neck 顶边
    head_rows = alpha_head > 0.05
    h = alpha_head.shape[0]
    last_true = (h - 1 - np.argmax(head_rows[::-1], axis=0)).astype(np.float32)
    cols = neck_cols & head_rows.any(axis=0)
    cols &= np.abs(np.arange(len(cols)) - neck_cx) <= half_w
    if min_head_bottom_y is not None:
        cols &= last_true >= float(min_head_bottom_y)
    if not cols.any():
        return None, None
    g = neck_top[cols] - last_true[cols]
    g = g[g > 0]
    if len(g) == 0:
        return 0.0, 0.0
    return float(g.mean()), float(g.max())


def extend_neck_rgb_upward(frame_a: np.ndarray, neck_mask: np.ndarray, pixels: int = 3):
    """§24.14 兜底 5：A neck 顶部纹理逐列向上延展（原 A 像素，非 B neck）。
    仅当 jaw/neck 间隙 1~3px 且 §24.14 顺序前四步无效时使用，最多 3~4px。"""
    out = frame_a.copy()
    h, w = neck_mask.shape
    ys, xs = np.nonzero(neck_mask > 0)
    if len(xs) == 0:
        return out
    for x in np.unique(xs):
        col_y = ys[xs == x]
        top = int(col_y.min())
        src_y = min(h - 1, top + 1)
        y0 = max(0, top - pixels)
        out[y0:top, x] = frame_a[src_y, x]
    return out


# ---------------------------------------------------------------- 第五轮（docs §26）

def directional_dilate_down(mask: np.ndarray, down_px: int, side_px: int = 2) -> np.ndarray:
    """只把 mask 向下扩展（§26.7 原样）；横向最多 side_px。

    禁止向上扩、禁止矩形整带：越向下允许非常缓慢地向左右展开（rx 随 dy 线性
    增长到 side_px），形成斜边而不是直柱。用于 B 下颌向下的解剖包络——
    只约束"哪里允许保留 A neck 顶部"，不得拿它扩张 B RGB。
    """
    src = (mask > 0).astype(np.uint8)
    out = src.copy()
    down_px = max(0, int(down_px))
    for dy in range(1, down_px + 1):
        shifted = np.zeros_like(src)
        shifted[dy:] = src[:-dy]
        rx = int(round(side_px * dy / max(down_px, 1)))
        if rx > 0:
            shifted = cv2.dilate(
                shifted,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * rx + 1, 3)),
            )
        out = cv2.max(out, shifted)
    return out > 0


def _jaw_zone_mask(shape, face_box: np.ndarray, top_ratio: float = 0.70, bottom_ratio: float = 0.30) -> np.ndarray:
    """下颌区行带（§26.7/26.8）：by0+0.70bh ~ by1+0.30bh。"""
    h = shape[0]
    bx0, by0, bx1, by1 = [float(v) for v in face_box]
    bh = by1 - by0
    yy = np.arange(h, dtype=np.float32)[:, None]
    return (yy >= by0 + top_ratio * bh) & (yy <= by1 + bottom_ratio * bh)


def build_jaw_neck_junction(
    alpha_head: np.ndarray,
    a_neck_safe: np.ndarray,
    old_head_safe: np.ndarray,
    face_box: np.ndarray,
    jaw_underlay_px: int = 10,
    neck_taper_height_px: int = 16,
    side_px: int = 2,
) -> dict:
    """下颌—脖子接合区构造（§26.8）：neck 顶部按 B 下颌包络塑形 + jaw underlay。

    层级（§26.6）：B 头前景 / A 原视频 jaw-neck junction underlay（接缝 8~12px）/
    A 原脖子·衣服·身体·背景 / 只清理前三层都不需要的旧头残差。

    四个不变量（§26.8）：
    1. jaw_underlay 只来自 A 原帧空间（本函数只出 mask，不改 RGB）；
    2. jaw_underlay 只出现在下颌区、neck 上方有限距离、old_head_safe 内；
    3. neck 中下段不裁，只有顶部带受 B jaw envelope 约束；
    4. fill_protect 必须包含 neck_visible | jaw_underlay。
    """
    h, w = alpha_head.shape
    bx0, by0, bx1, by1 = [float(v) for v in face_box]
    bh = by1 - by0
    yy = np.arange(h, dtype=np.float32)[:, None]

    head_support = alpha_head > 0.02
    head_core = alpha_head >= 0.995
    jaw_zone = _jaw_zone_mask(alpha_head.shape, face_box)
    jaw_soft = jaw_zone & head_support & (~head_core)

    # B 下颌向下的解剖包络，只用于约束 A neck 顶部（不得扩张 B RGB）
    envelope = directional_dilate_down(
        head_support & jaw_zone,
        down_px=neck_taper_height_px,
        side_px=side_px,
    ) & jaw_zone

    neck = a_neck_safe.astype(bool)
    ys = np.nonzero(neck)[0]
    if len(ys) == 0:
        return {
            "neck_visible": neck,
            "jaw_underlay": np.zeros_like(neck),
            "fill_protect": neck,
            "jaw_soft": jaw_soft,
            "envelope": envelope,
            "neck_top": None,
            "top_band": np.zeros_like(neck),
        }

    # 用 5% 分位而不是单个最高噪点定义 neck 顶部
    neck_top = int(np.percentile(ys, 5))
    top_band = (yy >= neck_top) & (yy < neck_top + neck_taper_height_px)

    # 顶部只保留位于 B 下颌向下包络内的 A neck；中下段完全保留（不变量 3）
    neck_visible = (neck & (~top_band)) | (neck & top_band & envelope)

    # 接缝底层：从已塑形 neck 向上寻找 jaw_underlay_px，但必须同时靠近 B 下颌
    # 软边（head_near），且必须位于 A old_head_safe 内（不变量 2，不保护远处墙面）
    neck_reach = extend_mask_upward(
        neck_visible.astype(np.uint8) * 255, jaw_underlay_px
    ) > 0
    head_near = cv2.dilate(
        head_support.astype(np.uint8),
        cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (2 * side_px + 1, 2 * jaw_underlay_px + 1)
        ),
    ) > 0
    jaw_underlay = jaw_zone & neck_reach & head_near & old_head_safe

    # underlay 包含下颌 soft alpha 正下方的 A 原接合皮肤，禁止墙填充进入（不变量 4）
    fill_protect = neck_visible | jaw_underlay
    return {
        "neck_visible": neck_visible,
        "jaw_underlay": jaw_underlay,
        "fill_protect": fill_protect,
        "jaw_soft": jaw_soft,
        "envelope": envelope,
        "neck_top": neck_top,
        "top_band": top_band,
    }


def head_above_covered(head_support: np.ndarray, check_px: int = 12) -> np.ndarray:
    """每列上方 1~check_px 行内是否存在 B head 支撑（§26.11 指标 3 用）。

    返回 bool 图：covered[y,x] = 存在 dy∈[1,check_px] 使 head_support[y-dy,x]。
    """
    covered = np.zeros_like(head_support, dtype=bool)
    for dy in range(1, max(1, int(check_px)) + 1):
        shifted = np.zeros_like(head_support, dtype=bool)
        shifted[dy:] = head_support[:-dy]
        covered |= shifted
    return covered


def orphan_neck_top_px(
    neck_visible: np.ndarray,
    head_support: np.ndarray,
    neck_top: int | None,
    neck_taper_height_px: int,
    envelope: np.ndarray | None = None,
    check_px: int = 12,
) -> int:
    """§26.11 指标 3：neck 顶部 taper 高度带内，不在 jaw 下包络内、且上方
    1~12px 没有 B head 覆盖的 neck 像素数。目标 0。

    必须覆盖左右列（禁止只测中部 60%）——正是 §26.5 指出旧 gap 指标漏掉
    左右悬空脖子尖端的盲区。
    """
    if neck_top is None:
        return 0
    h = neck_visible.shape[0]
    yy = np.arange(h, dtype=np.float32)[:, None]
    top_band = (yy >= neck_top) & (yy < neck_top + neck_taper_height_px)
    cand = neck_visible & top_band
    if envelope is not None:
        cand = cand & (~envelope.astype(bool))
    if not cand.any():
        return 0
    covered = head_above_covered(head_support, check_px=check_px)
    return int((cand & (~covered)).sum())


def junction_wall_component_max_px(
    residual: np.ndarray,
    neck_visible: np.ndarray,
    head_core: np.ndarray,
    jaw_zone: np.ndarray,
    dilate_px: int = 12,
) -> int:
    """§26.11 指标 2：jaw_zone ∩ dilate(neck_visible) ∩ (~head_core) 内 residual
    的最大连通域面积。目标 0（至少下颌中部与左右接合锚点附近为 0）。"""
    region = jaw_zone & (~head_core)
    if dilate_px > 0:
        k = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (dilate_px * 2 + 1, dilate_px * 2 + 1)
        )
        region = region & (cv2.dilate(neck_visible.astype(np.uint8), k) > 0)
    else:
        region = region & neck_visible.astype(bool)
    target = (residual.astype(bool) & region).astype(np.uint8)
    if not target.any():
        return 0
    count, _, stats, _ = cv2.connectedComponentsWithStats(target, connectivity=8)
    if count <= 1:
        return 0
    return int(max(stats[i, cv2.CC_STAT_AREA] for i in range(1, count)))


# ------------------------------------------------------- 第六轮（docs §28）

def build_vertical_junction_bridge(
    alpha_head: np.ndarray,
    neck_visible: np.ndarray,
    old_head_safe: np.ndarray,
    jaw_zone: np.ndarray,
    max_gap_px: int = 6,
    alpha_eps: float = 0.02,
) -> np.ndarray:
    """逐列封闭 B 下颌底边与 A neck 顶边之间的 1~max_gap_px 窄缝（§28.3 原样）。

    白横纹根因（§28.2）：mask 接近 ≠ mask 连通——neck_visible 与 jaw_underlay
    任意一个的 1px 边界差异都会留下水平 residual 缝，被 fit_wall_fill 100% 写成
    墙色。本函数只输出 A 原帧保护 mask；不扩 B RGB、不生成 B 脖子、
    不跨越大面积真实背景（gap > max_gap_px 不填）。
    """
    h, w = alpha_head.shape
    head = (alpha_head > alpha_eps) & jaw_zone
    neck = neck_visible.astype(bool) & jaw_zone
    bridge = np.zeros((h, w), np.uint8)

    for x in range(w):
        hy = np.flatnonzero(head[:, x])
        ny = np.flatnonzero(neck[:, x])
        if len(hy) == 0 or len(ny) == 0:
            continue
        head_bottom = int(hy[-1])
        # 必须找 head_bottom 下面的第一个 neck，不能用整列最小值误接侧脸
        below = ny[ny > head_bottom]
        if len(below) == 0:
            continue
        neck_top = int(below[0])
        gap = neck_top - head_bottom - 1
        if 0 < gap <= max_gap_px:
            bridge[head_bottom + 1 : neck_top, x] = 255

    # 只保留原本属于 A 旧头安全区且位于 jaw_zone 的像素
    return (bridge > 0) & old_head_safe & jaw_zone


def build_junction_corridor(
    alpha_head: np.ndarray,
    neck_visible: np.ndarray,
    old_head_safe: np.ndarray,
    jaw_zone: np.ndarray,
    kernel: tuple[int, int] = (9, 17),
) -> np.ndarray:
    """接合走廊（§28.4）：jaw_zone ∩ neck_near ∩ head_near ∩ old_head_safe。

    neck_near/head_near 均为 (9,17) 椭圆膨胀——只有上下同时邻近 head 与 neck
    的像素才算走廊。走廊内禁止任何 residual（墙色），这是比 §26.11 更严格的
    硬闸门；不能扩大到整张脸侧，否则会把真实墙面错误保护成 A 旧脸。
    """
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, kernel)
    neck_near = cv2.dilate(neck_visible.astype(np.uint8), k) > 0
    head_near = cv2.dilate((alpha_head > 0.02).astype(np.uint8), k) > 0
    return jaw_zone & neck_near & head_near & old_head_safe


def corridor_close_fill_protect(
    fill_protect: np.ndarray, corridor: np.ndarray, kernel: tuple[int, int] = (3, 5)
) -> np.ndarray:
    """走廊内 1px 分割孔洞保险（§28.4）：MORPH_CLOSE 只作用于 corridor。

    核固定为 (3,5)/(3,7) 纵向椭圆核；禁止在全头 mask 上 close（会把发丝
    边缘和真实背景一起粘进来）。
    """
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, kernel)
    fp_u8 = (fill_protect > 0).astype(np.uint8) * 255
    closed = cv2.morphologyEx(fp_u8, cv2.MORPH_CLOSE, k) > 0
    return (fill_protect > 0) | (closed & corridor)


def corridor_wall_like_px(
    clean_base: np.ndarray,
    frame_a: np.ndarray,
    corridor: np.ndarray,
    wall_lab: np.ndarray | None,
    wall_de: float = 6.0,
    skin_de: float = 10.0,
) -> int:
    """墙色保险检查（§28.4）：走廊内最终 clean_base 接近墙色、而 A 原帧同位置
    本来接近 neck 肤色的像素数。目标 0——非零说明接缝又被写成墙，必须回到
    mask 修复，禁止靠调色掩盖。"""
    if wall_lab is None or not corridor.any():
        return 0
    cb_lab = cv2.cvtColor(clean_base, cv2.COLOR_BGR2LAB).astype(np.float32)
    a_lab = cv2.cvtColor(frame_a, cv2.COLOR_BGR2LAB).astype(np.float32)
    de_cb = np.linalg.norm(cb_lab - wall_lab[None, None, :], axis=2)
    de_a = np.linalg.norm(a_lab - wall_lab[None, None, :], axis=2)
    bad = corridor & (de_cb <= wall_de) & (de_a > skin_de)
    return int(bad.sum())


# ------------------------------------------------ 第七轮（docs §30）

def build_required_skin_bridge(
    alpha_head: np.ndarray,
    neck_visible: np.ndarray,
    raw_a_skin: np.ndarray,
    face_box: np.ndarray,
    max_vertical_gap: int = 14,
    side_margin: int = 4,
    no_cap: bool = False,
) -> np.ndarray:
    """B 下颌与 A neck 之间必须由 A 人体皮肤连续连接（§30.5 原样）。

    生产不变量：凡位于 B 下颌底边和 A neck 顶边之间、且 A 原帧语义属于
    face skin/neck 的像素，必须保留 A 原帧人体像素作为 underlay；不得进入
    residual，不得调用 wall fill。只输出 raw skin 语义内的像素——不会把墙
    错当皮肤，也不会恢复整张 A 旧脸。

    no_cap=True（第七轮扩展）：gap 超过 max_vertical_gap 的列不整段连接，
    但仍保留 span 内的 raw skin 像素——满足不变量的字面要求（两侧锚点外
    的细长 skin 列片），不填任何非皮肤像素。
    """
    head = (alpha_head > 0.02).astype(bool)
    neck = neck_visible.astype(bool)
    skin = raw_a_skin.astype(bool)
    required = np.zeros_like(neck)

    neck_cols = np.flatnonzero(neck.any(axis=0))
    if len(neck_cols) == 0:
        return required
    x0 = max(0, int(neck_cols[0]) - side_margin)
    x1 = min(neck.shape[1], int(neck_cols[-1]) + side_margin + 1)

    for x in range(x0, x1):
        hy = np.flatnonzero(head[:, x])
        ny = np.flatnonzero(neck[:, x])
        if len(hy) == 0 or len(ny) == 0:
            continue
        hb = int(hy[-1])
        below = ny[ny > hb]
        if len(below) == 0:
            continue
        # 连接到颈部主体（最后一个连续段的起点）：§26 taper/envelope 裁剪可能在
        # 列内部留下空洞（如 939..947 保留、953..968 被剪、969.. 主体），
        # 洞内的 raw_skin 同样是 jaw→neck 之间的人体像素，不得因空洞漏保护。
        if len(below) > 1:
            breaks = np.flatnonzero(np.diff(below) > 1)
            nt = int(below[breaks[-1] + 1]) if len(breaks) else int(below[0])
        else:
            nt = int(below[0])
        gap = nt - hb - 1
        if 0 <= gap <= max_vertical_gap or no_cap:
            # +2px 上下重叠，避免量化/warp 后重新裂开；只保留 A 原帧人体语义
            y0 = max(0, hb - 2)
            y1 = min(neck.shape[0], nt + 3)
            required[y0:y1, x] = skin[y0:y1, x]
    return required


def build_jaw_underlay_skin(
    alpha_head: np.ndarray,
    raw_a_skin: np.ndarray,
    band_px: int = 20,
) -> np.ndarray:
    """head 支撑底部 band_px 行内的 raw skin 铺垫（§30.6 audit 第 1 部分语义）。

    audit ROI 第 1 部分覆盖 head alpha 底部 20px 的**所有列**（含 neck 列之外
    的下颌角两侧列）；这些列没有 neck，§30.5 的逐列 bridge 不处理，但 B 侧影
    软边下方同样是 A 人体皮肤语义——按不变量保留 A 原帧，不给墙。
    只取 head 支撑内部底带 ∩ raw skin，不扩大到支撑外。
    """
    if band_px <= 0:
        return np.zeros(alpha_head.shape[:2], dtype=bool)
    head = (alpha_head > 0.02)
    required = np.zeros_like(head)
    has_col = head.any(axis=0)
    if not has_col.any():
        return required
    h = head.shape[0]
    flipped = np.flip(head, axis=0)
    bottom = h - 1 - np.argmax(flipped, axis=0)
    for x in np.flatnonzero(has_col):
        hb = int(bottom[x])
        y0 = max(0, hb - band_px + 1)
        required[y0 : hb + 1, x] = raw_a_skin[y0 : hb + 1, x]
    return required


def build_audit_seam_roi(
    alpha_head: np.ndarray,
    raw_neck: np.ndarray,
    face_box: np.ndarray,
    bottom_px: int = 20,
    neck_band_px: int = 16,
    lower_ratio: float = 0.35,
) -> np.ndarray:
    """独立验收 ROI（§30.6）：不得依赖任何 repair mask（corridor/bridge/
    fill_protect），只读 alpha_head + raw_neck + face_box。

    三部分并集：
    1. B head alpha 每列底部 bottom_px 行；
    2. A raw neck 每列顶部 ±neck_band_px；
    3. 两者的逐列连接带 ∩ 脸框下 lower_ratio 区域。
    为避免远处误检把 ROI 拉宽，第 2/3 部分限制在脸框 x±0.35bw、
    y ∈ [by1-0.55bh, by1+0.65bh] 的几何窗内（纯几何，与 repair 无关）。
    """
    h, w = alpha_head.shape
    head = (alpha_head > 0.02).astype(bool)
    neck = raw_neck.astype(bool)
    bx0, by0, bx1, by1 = [float(v) for v in face_box]
    bw, bh = bx1 - bx0, by1 - by0
    gx0 = max(0, int(bx0 - 0.35 * bw))
    gx1 = min(w, int(bx1 + 0.35 * bw))
    gy0 = max(0, int(by1 - 0.55 * bh))
    gy1 = min(h, int(by1 + 0.65 * bh))
    y_lower = int(by1 - lower_ratio * bh)

    roi = np.zeros((h, w), dtype=bool)
    for x in range(w):
        hy = np.flatnonzero(head[:, x])
        if len(hy):
            hb = int(hy[-1])
            roi[max(0, hb - bottom_px + 1) : hb + 1, x] = True
        if gx0 <= x < gx1:
            ny = np.flatnonzero(neck[gy0:gy1, x])
            if len(ny):
                nt = int(ny[0]) + gy0
                roi[max(0, nt - neck_band_px) : nt + neck_band_px + 1, x] = True
                if len(hy):
                    lo, hi = min(hb, nt), max(hb, nt)
                    y0 = max(lo, y_lower)
                    if y0 <= hi:
                        roi[y0 : hi + 1, x] = True
    return roi


def audit_seam_metrics(
    clean_base: np.ndarray,
    frame_a: np.ndarray,
    audit_roi: np.ndarray,
    raw_a_skin: np.ndarray,
    wall_lab: np.ndarray | None,
    change_de: int = 20,
    wall_de: float = 8.0,
    skin_de: float = 12.0,
) -> dict:
    """独立验收（§30.6）：只比较 clean_base 与 A 原帧 + raw skin 语义。

    - changed_from_skin：A 原帧是皮肤但 clean_base 被改掉的像素（绝对失败）；
    - wall_intrusion：clean_base 接近背景模型、A 原帧是皮肤的像素（绝对失败）；
    - horizontal_wall_component_width：wall_intrusion 连通域的最大横向宽度。
    目标三项全部 = 0。
    """
    diff = np.abs(clean_base.astype(np.int16) - frame_a.astype(np.int16)).max(axis=2)
    changed = audit_roi & raw_a_skin.astype(bool) & (diff > change_de)

    wall_like = np.zeros_like(changed)
    if wall_lab is not None and (audit_roi & raw_a_skin.astype(bool)).any():
        cb_lab = cv2.cvtColor(clean_base, cv2.COLOR_BGR2LAB).astype(np.float32)
        a_lab = cv2.cvtColor(frame_a, cv2.COLOR_BGR2LAB).astype(np.float32)
        de_cb = np.linalg.norm(cb_lab - wall_lab[None, None, :], axis=2)
        de_a = np.linalg.norm(a_lab - wall_lab[None, None, :], axis=2)
        wall_like = (
            audit_roi & raw_a_skin.astype(bool) & (de_cb <= wall_de) & (de_a > skin_de)
        )

    width = 0
    if wall_like.any():
        count, _, stats, _ = cv2.connectedComponentsWithStats(
            wall_like.astype(np.uint8), connectivity=8
        )
        if count > 1:
            width = int(max(stats[i, cv2.CC_STAT_WIDTH] for i in range(1, count)))
    return {
        "audit_changed_from_skin": int(changed.sum()),
        "audit_wall_intrusion": int(wall_like.sum()),
        "audit_horizontal_wall_component_width": width,
    }


def wall_texture_energy(frame: np.ndarray, samples: np.ndarray) -> float:
    """背景纹理能量（§30.8）：墙面样本区的 Laplacian RMS。

    smooth_plane 只适用于纯色/缓变背景；窗帘/书架/条纹的纹理能量高，
    必须显式拒绝，不允许静默生成平色补丁。
    """
    if not samples.any():
        return 0.0
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    lap = cv2.Laplacian(gray, cv2.CV_32F)
    return float(np.sqrt((lap[samples] ** 2).mean()))


def head_bottom_edge_dist(alpha_head: np.ndarray) -> np.ndarray:
    """每列到 head 支撑底边的距离（§30.7）：底边处 0，向上增大；无 head 列为 -1。"""
    h, w = alpha_head.shape
    head = (alpha_head > 0.02)
    edge = np.full((h, w), -1.0, dtype=np.float32)
    has_col = head.any(axis=0)
    if not has_col.any():
        return edge
    # 每列最后一个 True 的行号
    flipped = np.flip(head, axis=0)
    last = h - 1 - np.argmax(flipped, axis=0)
    last = np.where(has_col, last, -1)
    yy = np.arange(h, dtype=np.float32)[:, None]
    dist = last[None, :].astype(np.float32) - yy
    edge[:, has_col] = dist[:, has_col]
    return edge


def jaw_luminance_gradient(
    head_rgb: np.ndarray,
    alpha_f: np.ndarray,
    frame_a: np.ndarray,
    ref_mask: np.ndarray,
    face_box: np.ndarray,
    strength: float,
    band_px: int = 28,
    max_delta_l: float = 30.0,
    max_delta_ab: float = 10.0,
    ema_state: dict | None = None,
    ema: float = 0.9,
) -> tuple[np.ndarray, dict]:
    """下颌局部低频亮度渐变（§30.7 D3）：只改 B head_rgb，不改 A neck。

    band = 下颌底边向上 band_px 内的 head 支撑；权重 smoothstep 从底边 1.0
    衰减到带顶 0；delta = clamp(A 参考 − B band, L±30, ab±10)，逐帧 EMA。
    """
    if strength <= 0:
        return head_rgb, ema_state or {}
    edge = head_bottom_edge_dist(alpha_f)
    band = (edge >= 0) & (edge <= band_px) & (alpha_f > 0.02)
    src_band = band & (alpha_f > 0.5)
    if not band.any() or not src_band.any() or not ref_mask.any():
        return head_rgb, ema_state or {}

    src_stats = lab_stats(np.clip(head_rgb, 0, 255).astype(np.uint8), src_band.astype(np.uint8))
    dst_stats = lab_stats(frame_a, ref_mask.astype(np.uint8))
    if src_stats is None or dst_stats is None:
        return head_rgb, ema_state or {}
    delta = np.clip(
        np.asarray(dst_stats[0], dtype=np.float64) - np.asarray(src_stats[0], dtype=np.float64),
        [-max_delta_l, -max_delta_ab, -max_delta_ab],
        [max_delta_l, max_delta_ab, max_delta_ab],
    )
    if ema_state:
        prev = np.asarray(ema_state.get("delta", delta), dtype=np.float64)
        delta = ema * prev + (1.0 - ema) * delta

    # smoothstep(28, 0, edge)：底边 1.0 → 带顶 0.0
    t = np.clip(edge / max(float(band_px), 1e-6), 0.0, 1.0)
    w = 1.0 - (t * t * (3.0 - 2.0 * t))

    lab = cv2.cvtColor(np.clip(head_rgb, 0, 255).astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)
    add = (w[..., None] * float(strength) * delta[None, None, :].astype(np.float32))
    lab[band] = np.clip(lab[band] + add[band], 0, 255)
    out = cv2.cvtColor(lab.astype(np.uint8), cv2.COLOR_LAB2BGR).astype(np.float32)
    return out, {"delta": delta.tolist()}


def motion_safe_union(
    current_mask: np.ndarray,
    previous_mask: np.ndarray | None,
    prev_kps: np.ndarray | None,
    cur_kps: np.ndarray | None,
    margin_px: int,
) -> np.ndarray:
    """旧头安全清理区：当前帧 mask ∪ 运动补偿后的上一帧 mask，再小膨胀。

    旧版全画布二值 EMA 在头移动时必然"前缘漏出、后缘拖尾"（0.4*255<127）；
    运动补偿并集对删除旧头是安全方向（短暂多删几像素好过漏出旧头边线）。
    """
    h, w = current_mask.shape[:2]
    union = (current_mask > 0).astype(np.uint8) * 255
    if previous_mask is not None and prev_kps is not None and cur_kps is not None:
        params = rigid_from_eyes(prev_kps, cur_kps)
        if params is not None:
            m = rebuild(*params)
            warped_prev = cv2.warpAffine(
                (previous_mask > 0).astype(np.uint8) * 255, m, (w, h),
                flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0,
            )
            union = cv2.max(union, warped_prev)
    if margin_px > 0:
        k = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (margin_px * 2 + 1, margin_px * 2 + 1)
        )
        union = cv2.dilate(union, k)
    return union


# ---------------------------------------------------------------- 主流程

def _ellipse_matrix(mask: np.ndarray) -> np.ndarray:
    """把 bool 转成 uint8 0/255，供形态学/输出复用。"""
    return (mask > 0).astype(np.uint8) * 255


def run_composite(args) -> int:
    # §24.11：B neck collar 与 A 脖子保护互斥；保护模式必须有 necks 目录
    check_neck_mode(bool(args.a_neck_preserve_enabled), bool(args.neck_collar_enabled))
    if args.a_neck_preserve_enabled and (args.necks_dir is None or not Path(args.necks_dir).is_dir()):
        raise RuntimeError("a_neck_preserve_enabled 需要 --necks-dir（先重跑 segment 生成 necks/）")
    if args.neck_pivot_enabled:
        if args.freeze_head_motion:
            raise ValueError("neck-pivot-enabled 与 freeze-head-motion 互斥")
        if args.necks_dir is None or not Path(args.necks_dir).is_dir():
            raise RuntimeError("neck-pivot-enabled 需要 --necks-dir")
        if args.filter_mode != "offline" or args.scale_mode != "const":
            raise ValueError("neck pivot 要求 filter_mode=offline 且 scale_mode=const")
        if abs(float(args.external_rotation_gain)) > 1e-9:
            raise ValueError("neck pivot 下 external_rotation_gain 必须为 0，防止双重旋转")
        if abs(float(args.scale_bias) - 1.0) > 1e-9 or abs(float(args.x_offset)) > 1e-9 or abs(float(args.y_offset)) > 1e-9:
            raise ValueError(
                "neck pivot 下旧 scale_bias/x_offset/y_offset 必须为 1/0/0；请改 attachment_offset"
            )
    meta = json.loads(Path(args.meta_json).read_text(encoding="utf-8"))
    frame_meta = meta["frame_meta"]
    total = meta["frames"]
    width, height = meta["width"], meta["height"]
    fps = meta.get("fps") or 30.0

    plate_img = cv2.imread(str(args.plate), cv2.IMREAD_COLOR) if args.plate and Path(args.plate).is_file() else None
    base_cap = cv2.VideoCapture(str(args.base_video))
    head_cap = cv2.VideoCapture(str(args.head_video))
    if not base_cap.isOpened() or not head_cap.isOpened():
        print("ERROR: cannot open base/head video", file=sys.stderr)
        return 2

    tracker = PrimaryFaceTracker(str(args.insightface_root), det_size=args.det_size)
    parser = BiSeNetParser(str(args.bisenet))
    b_segmenter = HeadSegmenter(
        parser, roi_ratio=args.head_roi_ratio, dilate_px=0, erode_px=0,
        temporal_ema=args.head_ema,
    )
    matcher = ColorMatcher(
        strength=args.color_strength, max_delta_l=args.max_delta_l,
        max_delta_ab=args.max_delta_ab, ema=args.color_ema,
    )
    neck_matcher = ColorMatcher(
        strength=args.neck_color_strength, max_delta_l=args.max_delta_l,
        max_delta_ab=args.max_delta_ab, ema=args.color_ema,
    )

    # ---------- 遍数一：逐帧 B 检测（供离线滤波与缓存，避免二次检测） ----------
    print("pass 1: B landmark scan", flush=True)
    head_cache: list[dict | None] = []  # {"bbox", "kps"}，失败帧 None
    index = 0
    while True:
        ok, frame_b = head_cap.read()
        if not ok:
            break
        t = tracker.track(frame_b)
        head_cache.append(
            {"bbox": [float(v) for v in t[0]], "kps": [[float(x), float(y)] for x, y in t[1]]}
            if t is not None else None
        )
        index += 1
    head_frames = index
    head_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    n_frames = min(total, head_frames)

    # 检测失败帧沿用前后最近的成功帧
    resolved: list[dict | None] = [None] * n_frames
    last = None
    for i in range(n_frames):
        if head_cache[i] is not None:
            last = head_cache[i]
        resolved[i] = last
    nxt = None
    for i in range(n_frames - 1, -1, -1):
        if head_cache[i] is not None:
            nxt = head_cache[i]
        elif resolved[i] is None:
            resolved[i] = nxt
    b_fallback = sum(1 for i in range(n_frames) if head_cache[i] is None)

    # ---------- raw 变换轨迹 ----------
    # 诊断模式：把 B 每帧的稳定锚点对齐到 A 的一个固定参考位置。
    # 这样会抵消 animated_head 自身的全局头动，只保留嘴型/表情；A 的 bbox、
    # neck、skin 仍逐帧读取，所以只冻结 B 头，不冻结身体或脖子。
    frozen_target_kps = None
    if args.freeze_head_motion:
        ref_n = min(max(1, int(args.freeze_reference_frames)), n_frames, len(frame_meta))
        ref_stack = np.asarray(
            [frame_meta[i]["kps"] for i in range(ref_n)], dtype=np.float32
        )
        frozen_target_kps = np.median(ref_stack, axis=0).astype(np.float32)

    raw_params: list[tuple[float, float, float, float]] = []
    pivot_log = None
    if args.neck_pivot_enabled:
        a_track: list[np.ndarray] = []
        b_track: list[np.ndarray] = []
        b_boxes: list[np.ndarray] = []
        p_candidates: list[np.ndarray | None] = []
        p_fallbacks: list[np.ndarray] = []
        for i in range(n_frames):
            fm = frame_meta[min(i, len(frame_meta) - 1)]
            ka = np.asarray(fm["kps"], dtype=np.float64)
            box_a_i = np.asarray(fm["bbox"], dtype=np.float64)
            src = resolved[i]
            if src is None:
                raise RuntimeError(f"neck pivot 无可用 B 检测：frame {i}")
            neck_path = Path(args.necks_dir) / f"neck_{i:06d}.png"
            neck_mask = cv2.imread(str(neck_path), cv2.IMREAD_GRAYSCALE)
            if neck_mask is None:
                raise RuntimeError(f"neck pivot 缺少 mask：{neck_path}")
            p = estimate_neck_pivot(neck_mask, box_a_i, ka)
            a_track.append(ka)
            b_track.append(np.asarray(src["kps"], dtype=np.float64))
            b_boxes.append(np.asarray(src["bbox"], dtype=np.float64))
            p_candidates.append(p)
            p_fallbacks.append(_fallback_neck_pivot(box_a_i, ka))
        p_raw_array, p_sources = resolve_point_track(
            p_candidates, p_fallbacks, max_gap=int(args.neck_pivot_max_gap)
        )
        raw_params, pivot_log = build_neck_pivot_transforms(
            a_track,
            b_track,
            b_boxes,
            p_raw_array,
            smooth_window=int(args.neck_pivot_smooth_window),
            offset=(float(args.attachment_offset_x), float(args.attachment_offset_y)),
        )
        pivot_log["p_neck_raw"] = p_raw_array.tolist()
        pivot_log["p_sources"] = p_sources
        pivot_log["fallback_frames"] = int(sum(s != "raw_neck" for s in p_sources))
    else:
        for i in range(n_frames):
            fm = frame_meta[min(i, len(frame_meta) - 1)]
            kps_a = np.array(fm["kps"], dtype=np.float32)
            target_kps = frozen_target_kps if frozen_target_kps is not None else kps_a
            src = resolved[i]
            if src is None:
                raw_params.append(raw_params[-1] if raw_params else (1.0, 0.0, 0.0, 0.0))
                continue
            if args.transform_mode == "eyes":
                p = rigid_from_eyes(np.array(src["kps"], dtype=np.float32), target_kps)
                raw_params.append(p if p else (raw_params[-1] if raw_params else (1.0, 0.0, 0.0, 0.0)))
            elif args.transform_mode == "eyes_nose":
                # §28.7 快速方案：眼睛+鼻尖（嘴点完全不进），滤波窗口配 5/7
                p = rigid_from_eyes_nose(np.array(src["kps"], dtype=np.float32), target_kps)
                raw_params.append(p if p else (raw_params[-1] if raw_params else (1.0, 0.0, 0.0, 0.0)))
            else:  # five_point（旧版对照）
                m = similarity(np.array(src["kps"], dtype=np.float32), target_kps)
                raw_params.append(decompose(m) if m is not None else (raw_params[-1] if raw_params else (1.0, 0.0, 0.0, 0.0)))

    # ---------- 滤波 ----------
    if args.neck_pivot_enabled:
        # P/Q 已各自做 Hampel + 零相位平滑；再次滤 tx/ty 会破坏逐帧支点不变量。
        filt_params = list(raw_params)
    elif args.filter_mode == "offline":
        filt_params = offline_filter(
            raw_params, hampel_window=args.hampel_window, smooth_window=args.filter_window,
            scale_mode=args.scale_mode, angle_window=args.angle_window,
        )
    else:
        filt_params = raw_params  # 在线模式在循环内用 SmoothedTransform
    online = SmoothedTransform(rot=args.rot_smooth, trans=args.trans_smooth, window=args.transform_window)
    transforms_log = {
        "mode": ("neck_pivot+translation_only" if args.neck_pivot_enabled else f"{args.transform_mode}+{args.filter_mode}"),
        "freeze_head_motion": bool(args.freeze_head_motion),
        "freeze_reference_frames": int(args.freeze_reference_frames),
        "frozen_target_kps": None if frozen_target_kps is None else frozen_target_kps.tolist(),
        "raw": raw_params,
        "filtered": filt_params,
        "pivot": pivot_log,
    }

    # ---------- 遍数二：合成 ----------
    import imageio.v2 as imageio

    args.output.parent.mkdir(parents=True, exist_ok=True)
    writer = imageio.get_writer(
        str(args.output), fps=fps, codec="libx264", quality=None, bitrate=None,
        macro_block_size=None,
        ffmpeg_params=["-crf", str(args.crf), "-preset", "fast",
                       "-pix_fmt", "yuv420p", "-threads", "1"],
    )
    if args.debug_dir:
        args.debug_dir.mkdir(parents=True, exist_ok=True)

    diag = {
        "frames": 0, "b_detect_fallback": int(b_fallback), "align_fail": 0,
        "color_skip": 0, "fill_fallback_frames": 0, "fill_mode_frames": {"plane": 0, "inpaint": 0, "plate": 0},
        "residual_uncovered_max": 0, "alpha_transition_width_px": 0.0,
        "old_head_erased_px_mean": 0.0,
        "wall_delta_e_mean": None, "wall_delta_e_max": None,
        "boundary_delta_e_mean": None, "boundary_delta_e_p95": None,
        "fallback_sources": {},
        "collar_frames": 0, "collar_px_mean": 0, "collar_bottom_y_mean": None,
        "neck_color_skip": 0,
        "a_neck_frames": 0, "a_neck_px_mean": 0, "a_neck_fail_frames": 0,
        "alpha_neck_max": 0.0,
        "jaw_neck_gap_px_mean": None, "jaw_neck_gap_px_max": None,
        "neck_temporal_mad": None,
        "jaw_color_skip": 0,
        # 第五轮（§26.11）：下颌—脖子接合区新指标
        "jaw_soft_wall_overlap_max": 0, "jaw_soft_wall_overlap_mean": None,
        "junction_wall_component_max": 0,
        "orphan_neck_top_max": 0,
        "jaw_underlay_px_mean": None, "neck_visible_px_mean": None,
        "jaw_underlay_frames": 0,
        # 第六轮（§28.5）：白横纹封闭指标（目标全 0，bridge_px 允许非零）
        "junction_bridge_px_mean": None,
        "junction_corridor_residual_max": 0,
        "junction_horizontal_component_max_width": 0,
        "junction_wall_like_max": 0,
        "junction_corridor_frames": 0,
        # 第七轮（§30.6）：独立验收指标（不得依赖 repair mask；目标全 0）
        "audit_changed_from_skin_max": 0,
        "audit_wall_intrusion_max": 0,
        "audit_horizontal_wall_component_width_max": 0,
        "skin_bridge_px_mean": None,
        "audit_frames": 0,
        "jaw_gradient_skip": 0,
        "neck_pivot_enabled": bool(args.neck_pivot_enabled),
        "attachment_error_p95_px": None,
        "attachment_error_max_px": None,
        "attachment_first_last_delta_px": None,
        "neck_pivot_fallback_frames": 0,
        "frame_range": [int(args.start_frame), None],
    }
    if pivot_log is not None:
        pivot_errors = np.asarray(pivot_log["attachment_error_px"], dtype=np.float64)
        diag["attachment_error_p95_px"] = round(float(np.percentile(pivot_errors, 95)), 6)
        diag["attachment_error_max_px"] = round(float(np.max(pivot_errors)), 6)
        diag["attachment_first_last_delta_px"] = round(
            float(abs(pivot_errors[-1] - pivot_errors[0])), 6
        )
        diag["neck_pivot_fallback_frames"] = int(pivot_log["fallback_frames"])
        if float(np.max(pivot_errors)) > float(args.max_attachment_drift_px):
            raise RuntimeError(
                f"头颈支点误差 {float(np.max(pivot_errors)):.3f}px 超过 "
                f"{float(args.max_attachment_drift_px):.3f}px"
            )
    widths_sum = 0
    erased_px_sum = 0
    prev_mask_a = None
    prev_kps_a = None
    prev_head_mask_b = None
    prev_neck_mask_b = None
    prev_box_b = None
    prev_neck_a = None
    prev_a_neck_safe = None
    collar_px_sum = 0
    collar_bottom_sum = 0.0
    a_neck_px_sum = 0
    neck_mad_sum = 0.0
    neck_mad_frames = 0
    gap_mean_sum = 0.0
    gap_max_overall = 0.0
    gap_frames = 0
    jaw_soft_overlap_sum = 0
    jaw_underlay_px_sum = 0
    neck_visible_px_sum = 0
    junction_bridge_sum = 0
    skin_bridge_sum = 0
    jaw_grad_state: dict = {}
    jaw_matcher = ColorMatcher(
        strength=max(args.jaw_color_strength, 1e-6),
        max_delta_l=args.max_delta_l,
        max_delta_ab=args.max_delta_ab,
        ema=args.color_ema,
    )
    wall_state: WallModelState | None = None
    global_wall_state: WallModelState | None = None
    wall_rows: list[dict] = []

    # 帧范围调试（§22.5.5）：pass1 始终全片扫描（离线滤波需要完整轨迹），
    # pass2 只合成 [start, start+max) —— 一个 bug 不该陪上 8 分钟全片
    frame_begin = max(0, int(args.start_frame))
    frame_end = n_frames if args.max_frames <= 0 else min(n_frames, frame_begin + int(args.max_frames))
    diag["frame_range"] = [frame_begin, frame_end]
    if frame_begin > 0:
        base_cap.set(cv2.CAP_PROP_POS_FRAMES, frame_begin)
        head_cap.set(cv2.CAP_PROP_POS_FRAMES, frame_begin)

    for index in range(frame_begin, frame_end):
        ok_a, frame_a = base_cap.read()
        ok_b, frame_b = head_cap.read()
        if not ok_a or not ok_b:
            break
        fm = frame_meta[min(index, len(frame_meta) - 1)]
        box_a = np.array(fm["bbox"], dtype=np.float32)
        kps_a = np.array(fm["kps"], dtype=np.float32)
        mask_a = cv2.imread(str(Path(args.masks_dir) / f"mask_{index:06d}.png"), cv2.IMREAD_GRAYSCALE)
        skins_a = cv2.imread(str(Path(args.skins_dir) / f"skin_{index:06d}.png"), cv2.IMREAD_GRAYSCALE)

        # 变换：离线滤波轨迹直接取；在线模式走旧版 SmoothedTransform
        if args.filter_mode == "offline":
            s0, a0, tx0, ty0 = filt_params[min(index, len(filt_params) - 1)]
            m_final = rebuild(s0 * args.scale_bias, a0, tx0 + args.x_offset, ty0 + args.y_offset)
        else:
            m_raw = rebuild(*raw_params[index])
            m_final = online.update(m_raw, args.scale_bias, args.x_offset, args.y_offset)

        # ---- B 侧 mask（Round G：head+neck 分层；否则 v2/F 路径）----
        src = resolved[index]
        if src is not None:
            box_b = np.array(src["bbox"], dtype=np.float32)
            if args.neck_collar_enabled:
                mask_b, neck_b, _ = b_segmenter.segment_full_parts(frame_b, box_b)
            else:
                mask_b, _ = b_segmenter.segment_full(frame_b, box_b)
                neck_b = None
            prev_head_mask_b = mask_b
            prev_neck_mask_b = neck_b
            prev_box_b = box_b.copy()
        elif prev_head_mask_b is not None:
            # B 检测失败帧：box/head/neck 三者整体沿用上一帧（§22.7.3）
            box_b, mask_b, neck_b = b_fallback_trio(prev_box_b, prev_head_mask_b, prev_neck_mask_b)
        else:
            mask_b = np.zeros(frame_b.shape[:2], np.uint8)
            neck_b = None

        bx0, by0, bx1, by1 = [float(v) for v in box_a]
        bw_a, bh_a = bx1 - bx0, by1 - by0

        alpha_neck = None
        collar_bottom_y = None
        jaw_start_y = None
        if args.neck_collar_enabled:
            # ---- Round G（v4 起 deprecated，仅历史复现）：分层 warp（§22.7.2） ----
            layers = build_warped_head_layers(
                frame_b, box_b, mask_b, neck_b, m_final, (width, height),
                head_erode_px=args.b_mask_erode_px,
                head_feather_px=args.alpha_feather_px,
                neck_collar_ratio=args.neck_collar_ratio,
                neck_collar_soft_px=args.neck_collar_soft_px,
                a_face_box=box_a,
            )
            head_rgb = layers["head_rgb"]
            alpha_f = layers["alpha_final"]
            alpha_neck = layers["alpha_neck"]
            widths_sum += layers["transition_width"]
            collar_bottom_y = layers["collar_bottom_y"]
            if (alpha_neck > 0.05).any():
                diag["collar_frames"] += 1
                collar_px_sum += int((alpha_neck > 0.05).sum())
                collar_bottom_sum += float(collar_bottom_y)
        else:
            # ---- v2/F 路径：预乘 warp + 内距羽化（Round F 起 B 硬 matte 内缩
            #      1~2px 去白色 matte，§20.5.2/§22.6；erode=0 时与 v2 逐位一致）----
            head_trim = trim_hard_matte(mask_b, args.b_mask_erode_px)
            alpha_src = head_trim.astype(np.float32) / 255.0
            head_rgb, warped_alpha = warp_premultiplied(frame_b, alpha_src, m_final, (width, height))
            if args.alpha_mode == "region_aware":
                # ---- Round H2（§24.12）：头发/脸侧 side px，下颌 jaw px，smoothstep 过渡 ----
                alpha_f, jaw_diag = region_aware_head_alpha(
                    warped_alpha, box_a,
                    side_feather_px=args.head_side_feather_px,
                    jaw_feather_px=args.jaw_feather_px,
                    jaw_start_ratio=args.jaw_start_ratio,
                    jaw_full_ratio=args.jaw_full_ratio,
                )
                jaw_start_y = jaw_diag["jaw_start_y"]
                band = (alpha_f > 1e-3) & (alpha_f < 1.0 - 1e-3)
                if band.any():
                    contours, _ = cv2.findContours(
                        (warped_alpha > 0.01).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
                    )
                    perimeter = sum(cv2.arcLength(c, True) for c in contours)
                    widths_sum += float(band.sum() / max(perimeter, 1.0))
            elif args.alpha_mode == "inner":
                alpha_f, twidth = inner_feather_alpha(warped_alpha, args.alpha_feather_px)
                widths_sum += twidth
            else:  # blur（旧版对照：erode + 对称 GaussianBlur）
                a8 = (warped_alpha * 255).astype(np.uint8)
                k = cv2.getStructuringElement(
                    cv2.MORPH_ELLIPSE, (args.alpha_erode_px * 2 + 1, args.alpha_erode_px * 2 + 1))
                a8 = cv2.erode(a8, k)
                ks = args.alpha_feather_px * 2 + 1
                a8 = cv2.GaussianBlur(a8, (ks, ks), 0)
                alpha_f = a8.astype(np.float32) / 255.0
            # 下颌软切割（以 A 人脸框为基准；collar 启用时由 collar 纵向 ramp 取代）
            cut_y = by0 + args.neck_cut_ratio * bh_a
            alpha_f = alpha_f * soft_cut(height, cut_y, args.neck_cut_soft)

        # 旧头安全清理区（运动补偿并集）
        if args.mask_union == "motion_safe":
            old_head_safe = motion_safe_union(
                mask_a if mask_a is not None else np.zeros((height, width), np.uint8),
                prev_mask_a, prev_kps_a, kps_a, margin_px=args.safe_margin_px,
            ) > 0
        else:
            k = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (args.safe_margin_px * 2 + 1, args.safe_margin_px * 2 + 1))
            old_head_safe = cv2.dilate(mask_a, k) > 0 if mask_a is not None else np.zeros((height, width), bool)

        # ---- 差集补洞（颜色参考与补洞保护彻底拆开）----
        color_reference = (
            skins_a > 0 if skins_a is not None else np.zeros((height, width), dtype=bool)
        )
        a_neck_safe = None
        junction = None
        junction_bridge = None
        junction_corridor = None
        jaw_zone = _jaw_zone_mask(alpha_f.shape, box_a)
        if args.a_neck_preserve_enabled:
            # ---- Round H1（§24.10.2）：按 class14 真实轮廓保护 A 原脖子，
            #      不再用水平 neck_keep_y —— 这是 v3 脖子顶部被削平的根因 ----
            neck_path = Path(args.necks_dir) / f"neck_{index:06d}.png"
            neck_a = cv2.imread(str(neck_path), cv2.IMREAD_GRAYSCALE)
            if neck_a is None:
                diag["a_neck_fail_frames"] += 1
                raise RuntimeError(f"A neck mask 缺失: {neck_path}")
            a_neck_safe = motion_safe_neck_union(
                neck_a, prev_neck_a, prev_kps_a, kps_a, upward_px=args.a_neck_upward_px
            ) > 0
            if prev_a_neck_safe is not None:
                neck_mad_sum += float(
                    np.mean(
                        np.abs(a_neck_safe.astype(np.float32) - prev_a_neck_safe.astype(np.float32))
                    )
                )
                neck_mad_frames += 1
            prev_a_neck_safe = a_neck_safe
            prev_neck_a = neck_a
            diag["a_neck_frames"] += 1
            a_neck_px_sum += int(a_neck_safe.sum())
            if args.jaw_underlay_enabled:
                # ---- 第五轮（§26.9）：jaw underlay + neck 顶部 taper 塑形。
                #      fill_protect 不再是整块 a_neck_safe，而是
                #      neck_visible | jaw_underlay —— B 下颌软边下面是 A 原接合
                #      皮肤，不是白墙（§26.2 根因一的修复）。
                junction = build_jaw_neck_junction(
                    alpha_head=alpha_f,
                    a_neck_safe=a_neck_safe,
                    old_head_safe=old_head_safe,
                    face_box=box_a,
                    jaw_underlay_px=args.jaw_underlay_px,
                    neck_taper_height_px=args.neck_taper_height_px,
                    side_px=args.neck_taper_side_px,
                )
                fill_protect = junction["fill_protect"]
                # ---- 第六轮（§28.3/28.4）：逐列封闭下颌—脖子窄缝 + 走廊硬闸门。
                #      mask 接近 ≠ mask 连通：underlay/taper 的 1px 边界差异会留
                #      下水平 residual 缝 → 墙色白横纹（§28.2 根因）。
                junction_corridor = build_junction_corridor(
                    alpha_f, junction["neck_visible"], old_head_safe, jaw_zone
                )
                fill_protect = corridor_close_fill_protect(fill_protect, junction_corridor)
                if args.junction_bridge_max_gap_px > 0:
                    junction_bridge = build_vertical_junction_bridge(
                        alpha_f,
                        junction["neck_visible"],
                        old_head_safe,
                        jaw_zone,
                        max_gap_px=args.junction_bridge_max_gap_px,
                    )
                    fill_protect = fill_protect | junction_bridge
                # §28.4 走廊完备化：走廊（上下同时邻近 head/neck ∩ 旧头安全区）内
                # 非新头核心的像素一律保留 A 原帧——它们要么是接合组织的皮肤底层
                # （正是 underlay 的目标），要么本来就是墙色（保留与墙填充视觉等价）。
                # 这使 residual ∩ corridor ≡ ∅ 成为构造性不变量，不再依赖
                # neck/underlay/bridge 三个 mask 的 1px 边界逐列对齐（§28.2 根因）。
                fill_protect = fill_protect | (junction_corridor & (alpha_f < 0.995))
            else:
                fill_protect = a_neck_safe.copy()
            # 手/证件/衣服 mask 接入点（§24.10.2 extra_masks）：暂无语义模型
        else:
            # 旧路径（v2/v3 复现）：水平线保护。Round G 时与 collar_bottom 同源。
            neck_keep_y = (
                collar_bottom_y if collar_bottom_y is not None else by1 + args.neck_keep_ratio * bh_a
            )
            fill_protect = build_fill_protect_mask(
                frame_a=frame_a,
                face_box=box_a,
                skins_mask=skins_a,
                neck_keep_y=neck_keep_y,
                extra_masks=(),
            ).astype(bool)

        # ---- 第七轮（§30.5）：raw A skin + required skin bridge ----
        required_skin_bridge = None
        raw_skin_a = None
        raw_neck_a = None
        if args.raw_skins_dir is not None:
            rs_path = Path(args.raw_skins_dir) / f"raw_skin_{index:06d}.png"
            rn_path = Path(args.raw_necks_dir) / f"raw_neck_{index:06d}.png"
            rs = cv2.imread(str(rs_path), cv2.IMREAD_GRAYSCALE)
            rn = cv2.imread(str(rn_path), cv2.IMREAD_GRAYSCALE)
            if rs is None or rn is None:
                raise RuntimeError(f"raw skin/neck mask 缺失: {rs_path} / {rn_path}")
            raw_skin_a = rs > 0
            raw_neck_a = rn > 0
            if junction is not None:
                neck_vis_bridge = junction["neck_visible"]
            elif a_neck_safe is not None:
                neck_vis_bridge = a_neck_safe
            else:
                neck_vis_bridge = raw_neck_a
            required_skin_bridge = build_required_skin_bridge(
                alpha_f, neck_vis_bridge, raw_skin_a, box_a,
                max_vertical_gap=args.skin_bridge_max_gap_px,
                no_cap=bool(args.skin_bridge_no_cap),
            )
            # §30.6 第 1 部分语义：head 底带（含下颌角两侧无 neck 列）的
            # raw skin 同样保留为 underlay，不给墙
            jaw_underlay_skin = build_jaw_underlay_skin(
                alpha_f, raw_skin_a, band_px=args.jaw_underlay_band_px
            )
            required_skin_bridge = required_skin_bridge | jaw_underlay_skin
            # 生产不变量：接合区人体像素不得进入 residual、不得调用 wall fill
            fill_protect = fill_protect | required_skin_bridge

        new_core = alpha_f >= 0.995
        residual = None
        if args.fill_mode == "wall_residual":
            residual = old_head_safe & (~new_core) & (~fill_protect)
            clean_base, fill_stats, wall_state = fit_wall_fill(
                frame=frame_a,
                residual=residual,
                old_head_safe=old_head_safe,
                fill_protect=fill_protect,
                face_box=box_a,
                previous_state=wall_state,
                global_state=global_wall_state,
                ring_width=args.ring_width_px,
                wall_delta_e=args.wall_delta_e,
                outer_feather_px=args.fill_outer_feather_px,
                max_texture=args.wall_max_texture,
            )
            if global_wall_state is None and wall_state.source == "current_frame":
                global_wall_state = WallModelState(
                    coef=wall_state.coef.copy(), wall_lab=wall_state.wall_lab.copy(), source="global"
                )
        elif args.fill_mode == "residual":  # v2 对照路径保持原样
            protect = color_reference
            residual = old_head_safe & (~new_core) & (~protect)
            clean_base, fill_stats = fit_plane_fill(frame_a, residual, protect, args.ring_width_px)
        else:  # plate（旧版对照：整块换底板）
            plate = plate_img if plate_img is not None else frame_a
            clean_base = frame_a.copy()
            k = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (args.plate_expand_px * 2 + 1, args.plate_expand_px * 2 + 1))
            hole = cv2.dilate(mask_a, k) if mask_a is not None else None
            if hole is not None:
                rp = hole > 0
                clean_base[rp] = plate[rp]
            fill_stats = {"mode": "plate", "filled_px": int((hole > 0).sum()) if hole is not None else 0}
        samples_dbg = fill_stats.pop("_samples", None)
        seed_dbg = fill_stats.pop("_seed", None)
        bump_mode_count(diag["fill_mode_frames"], fill_stats.get("mode", "unknown"))
        if fill_stats.get("fallback"):
            diag["fill_fallback_frames"] += 1
        if args.fill_mode == "wall_residual":
            wall_rows.append({**strip_debug_arrays(dict(fill_stats)), "i": index})
        erased_px_sum += int(fill_stats.get("filled_px", 0))
        # 旧头漏清理像素：清理区内未被新头覆盖、未进补洞差集、也未被保护（理论上应为 0）
        protect_uncov = fill_protect if args.fill_mode == "wall_residual" else color_reference
        filled_region = residual if residual is not None else np.zeros((height, width), bool)
        uncovered = int(
            (old_head_safe & (alpha_f < 0.05) & (~protect_uncov) & ~filled_region).sum()
        )
        diag["residual_uncovered_max"] = max(diag["residual_uncovered_max"], uncovered)

        # §30.5 硬保险：防止任何上游 mask 误差把接合人体像素写成背景。
        # 不是伪造新脖子，而是恢复 A 原帧本来就存在的下颌/上颈人体像素；
        # B 头仍通过 alpha 位于最前层。
        if required_skin_bridge is not None and required_skin_bridge.any():
            clean_base[required_skin_bridge] = frame_a[required_skin_bridge]

        # ---- §30.6 独立验收：audit ROI 不读取任何 repair mask ----
        audit_changed_dbg = None
        if raw_skin_a is not None:
            diag["audit_frames"] += 1
            skin_bridge_sum += int(required_skin_bridge.sum())
            audit_roi = build_audit_seam_roi(alpha_f, raw_neck_a, box_a)
            audit_seed = wall_seed_mask(
                frame_a.shape, box_a, old_head_safe | (fill_protect > 0)
            )
            audit_wall_lab = None
            if int(audit_seed.sum()) >= 200:
                _lab_seed = cv2.cvtColor(frame_a, cv2.COLOR_BGR2LAB).astype(np.float32)
                audit_wall_lab = np.median(_lab_seed[audit_seed], axis=0)
            am = audit_seam_metrics(clean_base, frame_a, audit_roi, raw_skin_a, audit_wall_lab)
            diag["audit_changed_from_skin_max"] = max(
                diag["audit_changed_from_skin_max"], am["audit_changed_from_skin"]
            )
            diag["audit_wall_intrusion_max"] = max(
                diag["audit_wall_intrusion_max"], am["audit_wall_intrusion"]
            )
            diag["audit_horizontal_wall_component_width_max"] = max(
                diag["audit_horizontal_wall_component_width_max"],
                am["audit_horizontal_wall_component_width"],
            )
            if args.debug_dir and am["audit_changed_from_skin"] > 0:
                diff_dbg = np.abs(clean_base.astype(np.int16) - frame_a.astype(np.int16)).max(axis=2)
                audit_changed_dbg = audit_roi & raw_skin_a & (diff_dbg > 20)
                vis = (0.4 * frame_a).astype(np.uint8)
                vis[audit_roi] = (0, 255, 255)
                vis[audit_changed_dbg] = (0, 0, 255)
                cv2.imwrite(str(args.debug_dir / f"frame_{index:04d}_audit_changed.png"), vis)

        # ---- §26.11 接合区新指标（v4 路径也输出 jaw_soft&residual，供 I0 取证）----
        # 指标 1：下颌 soft alpha 带内被清成墙的像素 —— 白线根因的直接度量，
        # 目标每帧 0（§26.2：白线在 clean_base 中已存在时禁止归因于色彩迁移）
        head_support_dbg = alpha_f > 0.02
        jaw_zone_dbg = jaw_zone
        jaw_soft_dbg = jaw_zone_dbg & head_support_dbg & (~new_core)
        overlap_px = int((jaw_soft_dbg & residual).sum()) if residual is not None else 0
        jaw_soft_overlap_sum += overlap_px
        diag["jaw_soft_wall_overlap_max"] = max(diag["jaw_soft_wall_overlap_max"], overlap_px)
        if junction is not None:
            # 指标 2：接合区 residual 最大连通域（jaw_zone ∩ dilate(neck)∩~head_core）
            # 指标 3：neck 顶部悬空像素（覆盖左右列，不限中部 60%）
            jaw_underlay_px_sum += int(junction["jaw_underlay"].sum())
            neck_visible_px_sum += int(junction["neck_visible"].sum())
            diag["jaw_underlay_frames"] += 1
            if residual is not None:
                jwcm = junction_wall_component_max_px(
                    residual, junction["neck_visible"], new_core, jaw_zone_dbg
                )
                diag["junction_wall_component_max"] = max(diag["junction_wall_component_max"], jwcm)
            ontp = orphan_neck_top_px(
                junction["neck_visible"], head_support_dbg, junction["neck_top"],
                args.neck_taper_height_px, envelope=junction["envelope"],
            )
            diag["orphan_neck_top_max"] = max(diag["orphan_neck_top_max"], ontp)

        # ---- §28.5 白横纹新指标：走廊内禁止任何墙色 residual / 墙色像素 ----
        if junction_corridor is not None:
            diag["junction_corridor_frames"] += 1
            bridge_px = int(junction_bridge.sum()) if junction_bridge is not None else 0
            junction_bridge_sum += bridge_px
            corr_res = int((residual & junction_corridor).sum()) if residual is not None else 0
            diag["junction_corridor_residual_max"] = max(
                diag["junction_corridor_residual_max"], corr_res
            )
            if corr_res > 0:
                comp_target = (residual & junction_corridor).astype(np.uint8)
                comp_count, _, comp_stats, _ = cv2.connectedComponentsWithStats(
                    comp_target, connectivity=8
                )
                comp_width = (
                    int(max(comp_stats[i, cv2.CC_STAT_WIDTH] for i in range(1, comp_count)))
                    if comp_count > 1 else 0
                )
                diag["junction_horizontal_component_max_width"] = max(
                    diag["junction_horizontal_component_max_width"], comp_width
                )
            # 墙色保险：走廊内 clean_base 呈墙色而 A 原帧是肤色的像素（§28.4）
            wall_seed = wall_seed_mask(
                frame_a.shape, box_a, old_head_safe | (fill_protect > 0)
            )
            wall_lab_cur = None
            if int(wall_seed.sum()) >= 200:
                lab_seed = cv2.cvtColor(frame_a, cv2.COLOR_BGR2LAB).astype(np.float32)
                wall_lab_cur = np.median(lab_seed[wall_seed], axis=0)
            wall_like = corridor_wall_like_px(
                clean_base, frame_a, junction_corridor, wall_lab_cur
            )
            diag["junction_wall_like_max"] = max(diag["junction_wall_like_max"], wall_like)

        # 色彩迁移（仅新头有效区；参考 mask 用 color_reference，禁止用 fill_protect）
        head_zone = alpha_f > 0.6
        matcher.feed(lab_stats(head_rgb.astype(np.uint8), head_zone), lab_stats(frame_a, color_reference))
        if not matcher.ready():
            diag["color_skip"] += 1
        if matcher.ready():
            head_rgb = matcher.apply(head_rgb.astype(np.uint8), head_zone).astype(np.float32)

        # Round G（§22.7.5，v4 起 deprecated）：collar 弱局部颜色匹配——仅历史复现
        if (
            alpha_neck is not None
            and args.neck_color_strength > 0
            and (alpha_neck > 0.2).any()
        ):
            yy_grid = np.arange(height, dtype=np.float32)[:, None]
            a_neck_ref = color_reference & (yy_grid >= float(box_a[3])) & (yy_grid <= neck_keep_y + 20)
            neck_zone = alpha_neck > 0.2
            neck_matcher.feed(
                lab_stats(head_rgb.astype(np.uint8), neck_zone),
                lab_stats(frame_a, a_neck_ref),
            )
            if neck_matcher.ready():
                head_rgb = neck_matcher.apply(head_rgb.astype(np.uint8), neck_zone).astype(np.float32)
            else:
                diag["neck_color_skip"] += 1

        # Round H3（§24.13）：jaw 局部颜色匹配——只改 B 下颌带，不修改 A neck；
        # 仅 H2 几何通过后启用（jaw_color_strength=0 时跳过）
        if (
            a_neck_safe is not None
            and args.jaw_color_strength > 0
            and jaw_start_y is not None
        ):
            yy_grid = np.arange(height, dtype=np.float32)[:, None]
            jaw_zone = yy_grid >= jaw_start_y
            src_jaw_band = jaw_zone & (alpha_f > 0.20) & (alpha_f < 0.95)
            # A 上颈部参考：真实 neck mask 顶部带，不是水平全宽矩形（§24.13）
            neck_top_band = a_neck_safe & ~(
                cv2.erode(
                    a_neck_safe.astype(np.uint8) * 255,
                    cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)),
                )
                > 0
            )
            if src_jaw_band.any() and neck_top_band.any():
                jaw_matcher.feed(
                    lab_stats(head_rgb.astype(np.uint8), src_jaw_band),
                    lab_stats(frame_a, neck_top_band),
                )
                if jaw_matcher.ready():
                    head_rgb = jaw_matcher.apply(
                        head_rgb.astype(np.uint8), src_jaw_band
                    ).astype(np.float32)
                else:
                    diag["jaw_color_skip"] += 1

        # §24.19：B 下颌与 A neck 正面缝合区的纵向间隙（目标 mean<1px / max<=2px）
        if a_neck_safe is not None:
            g_mean, g_max = jaw_neck_gap_px(alpha_f, a_neck_safe, by1 - 0.05 * bh_a)
            if g_mean is not None:
                gap_mean_sum += g_mean
                gap_max_overall = max(gap_max_overall, g_max)
                gap_frames += 1

        # ---- 第七轮 M3（§30.7 D3）：下颌局部低频亮度渐变 ----
        # 只改 B head_rgb（smoothstep 从下颌底边向上衰减），不改 A neck；
        # clean_base 几何审核未通过前禁止启用（strength 默认 0）。
        if args.jaw_gradient_strength > 0 and required_skin_bridge is not None:
            if a_neck_safe is not None:
                neck_top_ref = a_neck_safe & ~(
                    cv2.erode(
                        a_neck_safe.astype(np.uint8) * 255,
                        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)),
                    )
                    > 0
                )
                grad_ref = required_skin_bridge | neck_top_ref
            else:
                grad_ref = required_skin_bridge
            head_rgb, jaw_grad_state = jaw_luminance_gradient(
                head_rgb, alpha_f, frame_a, grad_ref, box_a,
                strength=args.jaw_gradient_strength,
                band_px=args.jaw_gradient_band_px,
                ema_state=jaw_grad_state,
            )
        elif args.jaw_gradient_strength > 0:
            diag["jaw_gradient_skip"] += 1

        out = np.clip(
            head_rgb * alpha_f[..., None] + clean_base.astype(np.float32) * (1.0 - alpha_f[..., None]),
            0, 255,
        ).astype(np.uint8)
        writer.append_data(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))
        diag["frames"] += 1

        # ---- 3×3 九格调试图 + 头部局部放大（§24.17 v4 布局 / §22.5.4 v3 布局）----
        if args.debug_dir and index % args.debug_every == 0:
            def b3(g):
                return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)

            if pivot_log is not None:
                anchor = out.copy()
                p = np.rint(np.asarray(pivot_log["p_neck_used"][index])).astype(int)
                q = np.rint(np.asarray(pivot_log["q_attach_full"][index])).astype(int)
                cv2.drawMarker(anchor, tuple(p), (0, 255, 0), cv2.MARKER_CROSS, 28, 3)
                cv2.drawMarker(anchor, tuple(q), (0, 0, 255), cv2.MARKER_TILTED_CROSS, 24, 2)
                cv2.line(anchor, tuple(p), tuple(q), (0, 255, 255), 2)
                cv2.putText(
                    anchor, "P neck=green, Q head=red", (20, 42),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 3, cv2.LINE_AA,
                )
                cv2.putText(
                    anchor, "P neck=green, Q head=red", (20, 42),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 1, cv2.LINE_AA,
                )
                cv2.imwrite(str(args.debug_dir / f"frame_{index:04d}_pivot.png"), anchor)

            rp_vis = np.zeros((height, width), np.uint8)
            rp_vis[fill_protect] = 120  # 补洞保护区（灰）
            if residual is not None:
                rp_vis[residual] = 255  # 补洞差集（白）
            bw_a = bx1 - bx0
            cx0 = max(0, int(bx0 - 0.9 * bw_a))
            cx1 = min(width, int(bx1 + 0.9 * bw_a))
            cy0 = max(0, int(by0 - 1.2 * bh_a))
            cy1 = min(height, int(by1 + 0.6 * bh_a))

            if junction is not None:
                # ---- v5（§26.12）3×3 固定布局 + 4 倍 junction 放大图 ----
                raw_neck_vis = neck_a if neck_a is not None else np.zeros((height, width), np.uint8)
                # jaw_underlay/residual 面板：绿=underlay，灰=neck_visible，红=residual；
                # 红色若穿过 B 下颌软边与 neck 之间，直接失败（§26.12）
                ur_vis = np.zeros((height, width, 3), np.uint8)
                ur_vis[junction["neck_visible"]] = (120, 120, 120)
                ur_vis[junction["jaw_underlay"]] = (0, 255, 0)
                if junction_bridge is not None:
                    ur_vis[junction_bridge] = (255, 0, 0)   # 蓝 = 逐列 bridge（§28.5）
                if residual is not None:
                    ur_vis[residual] = (0, 0, 255)
                # 第 9 格：clean_base 与 final 左右对照（各半宽）
                half = (max(2, width // 6), height // 3)
                two_up = np.hstack(
                    [cv2.resize(clean_base, half), cv2.resize(out, half)]
                )
                grid = compose_debug_grid(
                    [
                        frame_a, b3(raw_neck_vis), b3(_ellipse_matrix(junction["neck_visible"])),
                        np.clip(head_rgb, 0, 255).astype(np.uint8),
                        b3((alpha_f * 255).astype(np.uint8)),
                        b3(_ellipse_matrix(junction["envelope"])),
                        b3(_ellipse_matrix(old_head_safe)), ur_vis, two_up,
                    ],
                    (width, height),
                )
                cv2.imwrite(str(args.debug_dir / f"frame_{index:04d}_grid.png"), grid)
                # 4 倍放大 junction 区（下颌—上颈）：frame 0 为本素材最差帧
                jx0 = max(0, int(bx0 - 0.5 * bw_a))
                jx1 = min(width, int(bx1 + 0.5 * bw_a))
                jy0 = max(0, int(by0 + 0.45 * bh_a))
                jy1 = min(height, int(by1 + 0.55 * bh_a))
                zsize = (max(2, (jx1 - jx0) * 4), max(2, (jy1 - jy0) * 4))

                def zoom4(img):
                    return cv2.resize(np.ascontiguousarray(img), zsize, interpolation=cv2.INTER_NEAREST)

                crop = (slice(jy0, jy1), slice(jx0, jx1))
                cv2.imwrite(str(args.debug_dir / f"frame_{index:04d}_junction_clean_base.png"), zoom4(clean_base[crop]))
                cv2.imwrite(str(args.debug_dir / f"frame_{index:04d}_junction_final.png"), zoom4(out[crop]))
                cv2.imwrite(str(args.debug_dir / f"frame_{index:04d}_junction_masks.png"), zoom4(ur_vis[crop]))
                # §28.5：bridge 最近邻 8× 局部图（frame 0 为白横纹最差帧）
                zsize8 = (max(2, (jx1 - jx0) * 8), max(2, (jy1 - jy0) * 8))
                zoom8 = cv2.resize(np.ascontiguousarray(ur_vis[crop]), zsize8, interpolation=cv2.INTER_NEAREST)
                cv2.imwrite(str(args.debug_dir / f"frame_{index:04d}_junction_bridge8x.png"), zoom8)
                js_vis = (0.4 * frame_a).astype(np.uint8)
                js_vis[jaw_soft_dbg] = (0, 255, 255)  # 黄 = B 下颌软边
                if residual is not None:
                    js_vis[residual] = (0, 0, 255)    # 红 = 被清成墙的区域
                cv2.imwrite(str(args.debug_dir / f"frame_{index:04d}_jaw_soft_vs_residual.png"), zoom4(js_vis[crop]))
                cv2.imwrite(str(args.debug_dir / f"frame_{index:04d}_alpha_crop.png"), (alpha_f * 255).astype(np.uint8)[cy0:cy1, cx0:cx1])
                cv2.imwrite(str(args.debug_dir / f"frame_{index:04d}_a_neck.png"), raw_neck_vis[cy0:cy1, cx0:cx1])
            elif args.a_neck_preserve_enabled:
                # v4（§24.17）：禁止出现 B neck collar 面板；A raw/safe neck 必须在列
                raw_neck_vis = neck_a if neck_a is not None else np.zeros((height, width), np.uint8)
                safe_neck_vis = a_neck_safe.astype(np.uint8) * 255
                grid = compose_debug_grid(
                    [
                        frame_a, b3(raw_neck_vis), b3(safe_neck_vis),
                        b3(_ellipse_matrix(old_head_safe)), b3(rp_vis), clean_base,
                        np.clip(head_rgb, 0, 255).astype(np.uint8),
                        b3((alpha_f * 255).astype(np.uint8)), out,
                    ],
                    (width, height),
                )
                cv2.imwrite(str(args.debug_dir / f"frame_{index:04d}_grid.png"), grid)
                cv2.imwrite(str(args.debug_dir / f"frame_{index:04d}_a_neck.png"), raw_neck_vis[cy0:cy1, cx0:cx1])
                cv2.imwrite(str(args.debug_dir / f"frame_{index:04d}_alpha_crop.png"), (alpha_f * 255).astype(np.uint8)[cy0:cy1, cx0:cx1])
            else:
                wall_vis = np.zeros((height, width), np.uint8)
                if seed_dbg is not None:
                    wall_vis[seed_dbg] = 255
                if samples_dbg is not None and samples_dbg.any():
                    wall_vis[samples_dbg] = 160
                patch_vis = np.full_like(frame_a, 128)
                if residual is not None and residual.any():
                    patch_vis[residual] = clean_base[residual]
                grid = compose_debug_grid(
                    [
                        frame_a, b3(_ellipse_matrix(old_head_safe)), b3(wall_vis),
                        patch_vis, clean_base, np.clip(head_rgb, 0, 255).astype(np.uint8),
                        b3((alpha_f * 255).astype(np.uint8)), b3(rp_vis), out,
                    ],
                    (width, height),
                )
                cv2.imwrite(str(args.debug_dir / f"frame_{index:04d}_grid.png"), grid)
                cv2.imwrite(str(args.debug_dir / f"frame_{index:04d}_wall_samples.png"), wall_vis[cy0:cy1, cx0:cx1])
            cv2.imwrite(str(args.debug_dir / f"frame_{index:04d}_clean_base_crop.png"), clean_base[cy0:cy1, cx0:cx1])
            cv2.imwrite(str(args.debug_dir / f"frame_{index:04d}_final_crop.png"), out[cy0:cy1, cx0:cx1])
            # §30.6 五联独立验收图：A原帧 | raw_skin | clean_base | final | diff（3×）
            if raw_skin_a is not None:
                jw, jh = cx1 - cx0, cy1 - cy0
                pw, ph = max(2, jw), max(2, jh)
                diff_gray = np.abs(out.astype(np.int16) - frame_a.astype(np.int16)).max(axis=2)
                diff_gray = np.clip(diff_gray * 3, 0, 255).astype(np.uint8)
                panels = [
                    frame_a[cy0:cy1, cx0:cx1],
                    cv2.cvtColor(raw_skin_a[cy0:cy1, cx0:cx1].astype(np.uint8) * 255, cv2.COLOR_GRAY2BGR),
                    clean_base[cy0:cy1, cx0:cx1],
                    out[cy0:cy1, cx0:cx1],
                    cv2.applyColorMap(diff_gray[cy0:cy1, cx0:cx1], cv2.COLORMAP_JET),
                ]
                five = np.hstack(
                    [cv2.resize(np.ascontiguousarray(p), (pw * 3, ph * 3), interpolation=cv2.INTER_NEAREST) for p in panels]
                )
                cv2.imwrite(str(args.debug_dir / f"frame_{index:04d}_audit5.png"), five)

        prev_mask_a = mask_a
        prev_kps_a = kps_a
        if index % 100 == 0:
            print(f"composited frame {index}", flush=True)

    writer.close()
    base_cap.release()
    head_cap.release()
    diag["alpha_transition_width_px"] = round(widths_sum / max(diag["frames"], 1), 2)
    diag["old_head_erased_px_mean"] = int(erased_px_sum / max(diag["frames"], 1))
    if diag["collar_frames"] > 0:
        diag["collar_px_mean"] = int(collar_px_sum / diag["collar_frames"])
        diag["collar_bottom_y_mean"] = round(collar_bottom_sum / diag["collar_frames"], 1)
    if diag["a_neck_frames"] > 0:
        diag["a_neck_px_mean"] = int(a_neck_px_sum / diag["a_neck_frames"])
    if gap_frames > 0:
        diag["jaw_neck_gap_px_mean"] = round(gap_mean_sum / gap_frames, 3)
        diag["jaw_neck_gap_px_max"] = round(gap_max_overall, 3)
    diag["jaw_soft_wall_overlap_mean"] = round(jaw_soft_overlap_sum / max(diag["frames"], 1), 3)
    if diag["jaw_underlay_frames"] > 0:
        diag["jaw_underlay_px_mean"] = int(jaw_underlay_px_sum / diag["jaw_underlay_frames"])
        diag["neck_visible_px_mean"] = int(neck_visible_px_sum / diag["jaw_underlay_frames"])
    if diag["junction_corridor_frames"] > 0:
        diag["junction_bridge_px_mean"] = round(
            junction_bridge_sum / diag["junction_corridor_frames"], 2
        )
    if diag["audit_frames"] > 0:
        diag["skin_bridge_px_mean"] = int(skin_bridge_sum / diag["audit_frames"])
    if neck_mad_frames > 0:
        diag["neck_temporal_mad"] = round(neck_mad_sum / neck_mad_frames, 6)
    if wall_rows:
        de_means = [r["fill_wall_delta_e_mean"] for r in wall_rows if r.get("fill_wall_delta_e_mean") is not None]
        de_maxes = [r["fill_wall_delta_e_max"] for r in wall_rows if r.get("fill_wall_delta_e_max") is not None]
        bd_means = [r["fill_boundary_delta_e_mean"] for r in wall_rows if r.get("fill_boundary_delta_e_mean") is not None]
        bd_p95s = [r["fill_boundary_delta_e_p95"] for r in wall_rows if r.get("fill_boundary_delta_e_p95") is not None]
        if de_means:
            diag["wall_delta_e_mean"] = round(float(np.mean(de_means)), 3)
            diag["wall_delta_e_max"] = round(float(np.max(de_maxes)), 3)
        if bd_means:
            diag["boundary_delta_e_mean"] = round(float(np.mean(bd_means)), 3)
            diag["boundary_delta_e_p95"] = round(float(np.max(bd_p95s)), 3)
        for r in wall_rows:
            if r.get("fallback"):
                src = str(r.get("fallback_source", "?"))
                diag["fallback_sources"][src] = diag["fallback_sources"].get(src, 0) + 1
        args.output.with_suffix(".fills.json").write_text(
            json.dumps(wall_rows, ensure_ascii=False), encoding="utf-8")
    args.output.with_suffix(".transforms.json").write_text(
        json.dumps(transforms_log), encoding="utf-8")
    if pivot_log is not None:
        pivot_csv = args.output.with_suffix(".pivots.csv")
        with pivot_csv.open("w", newline="", encoding="utf-8-sig") as fp:
            fields = [
                "frame", "p_raw_x", "p_raw_y", "p_used_x", "p_used_y",
                "q_raw_x", "q_raw_y", "q_used_x", "q_used_y",
                "q_full_x", "q_full_y", "error_px", "p_source",
            ]
            writer_csv = csv.DictWriter(fp, fieldnames=fields)
            writer_csv.writeheader()
            for i in range(len(pivot_log["p_neck_used"])):
                writer_csv.writerow(
                    {
                        "frame": i,
                        "p_raw_x": pivot_log["p_neck_raw"][i][0],
                        "p_raw_y": pivot_log["p_neck_raw"][i][1],
                        "p_used_x": pivot_log["p_neck_used"][i][0],
                        "p_used_y": pivot_log["p_neck_used"][i][1],
                        "q_raw_x": pivot_log["q_attach_raw"][i][0],
                        "q_raw_y": pivot_log["q_attach_raw"][i][1],
                        "q_used_x": pivot_log["q_attach_used"][i][0],
                        "q_used_y": pivot_log["q_attach_used"][i][1],
                        "q_full_x": pivot_log["q_attach_full"][i][0],
                        "q_full_y": pivot_log["q_attach_full"][i][1],
                        "error_px": pivot_log["attachment_error_px"][i],
                        "p_source": pivot_log["p_sources"][i],
                    }
                )
    args.output.with_suffix(".diag.json").write_text(
        json.dumps(diag, ensure_ascii=False), encoding="utf-8")
    print(f"OK: composited {diag['frames']} frames -> {args.output}")
    print(json.dumps(diag, ensure_ascii=False))
    return 0


def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="新头对齐回贴 worker（第二轮重构版）")
    ap.add_argument("--base-video", required=True, type=Path)
    ap.add_argument("--head-video", required=True, type=Path)
    ap.add_argument("--masks-dir", required=True, type=Path)
    ap.add_argument("--skins-dir", required=True, type=Path)
    ap.add_argument("--meta-json", required=True, type=Path)
    ap.add_argument("--plate", type=Path, default=None, help="仅 fill_mode=plate 的旧版对照使用")
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--bisenet", required=True, type=Path)
    ap.add_argument("--insightface-root", required=True, type=Path)
    ap.add_argument("--det-size", type=int, default=640)
    ap.add_argument("--head-roi-ratio", type=float, default=2.4)
    ap.add_argument("--head-ema", type=float, default=0.5)
    # 变换与滤波（§17.5 / §28.7 / §28.10）
    ap.add_argument("--transform-mode", choices=["eyes", "eyes_nose", "five_point"], default="eyes",
                    help="eyes=v4/v5；eyes_nose=§28.7 K1/K2（嘴点完全不进）；five_point=旧版对照")
    ap.add_argument(
        "--neck-pivot-enabled", action="store_true",
        help="第八轮：常量 scale/angle，只用 P_A-Q_B 动态平移锁定头颈连接支点",
    )
    ap.add_argument("--neck-pivot-smooth-window", type=int, default=7,
                    help="P/Q 的 Hampel + 零相位平滑奇数窗口")
    ap.add_argument("--neck-pivot-max-gap", type=int, default=5,
                    help="预留的 neck 低置信短缺失插值上限；超限使用显式 fallback")
    ap.add_argument("--attachment-offset-x", type=float, default=0.0,
                    help="头颈支点固定视觉标定 X，不允许逐帧变化")
    ap.add_argument("--attachment-offset-y", type=float, default=0.0,
                    help="头颈支点固定视觉标定 Y，不允许逐帧变化")
    ap.add_argument("--external-rotation-gain", type=float, default=0.0,
                    help="rotation_exp/neck pivot 下必须为 0，防止 LivePortrait 与外部双旋转")
    ap.add_argument("--max-attachment-drift-px", type=float, default=3.0,
                    help="对齐后 P/Q 最大允许误差，超限直接失败")
    ap.add_argument(
        "--freeze-head-motion", action="store_true",
        help="诊断：把 B 每帧锚点对齐到 A 前若干帧的固定中位锚点，只保留嘴型/表情",
    )
    ap.add_argument(
        "--freeze-reference-frames", type=int, default=30,
        help="freeze-head-motion 使用的 A 参考帧数（逐点取中位数）",
    )
    ap.add_argument("--filter-mode", choices=["offline", "online"], default="offline")
    ap.add_argument("--hampel-window", type=int, default=7)
    ap.add_argument("--filter-window", type=int, default=11)
    ap.add_argument("--scale-mode", choices=["smooth", "const", "smooth_clamped"], default="smooth",
                    help="smooth_clamped=§28.10 K2：smooth21 + 中位数±1% clamp")
    ap.add_argument("--angle-window", type=int, default=0, help="roll 强平滑窗口，0=同 filter_window")
    ap.add_argument("--transform-window", type=int, default=9, help="在线模式中位数窗口")
    ap.add_argument("--rot-smooth", type=float, default=0.8)
    ap.add_argument("--trans-smooth", type=float, default=0.8)
    # alpha（§17.4 / §20.5.2 / §24.12）
    ap.add_argument("--alpha-mode", choices=["inner", "blur", "region_aware"], default="inner")
    ap.add_argument("--alpha-feather-px", type=float, default=6.0, help="内距羽化过渡宽 4~8px")
    ap.add_argument("--alpha-erode-px", type=int, default=4, help="仅 blur 对照模式")
    ap.add_argument("--b-mask-erode-px", type=int, default=0,
                    help="B 硬 mask 内缩去白色 matte（Round F=1；0=与 v2 一致）")
    ap.add_argument("--head-side-feather-px", type=float, default=4.0, help="region_aware 头发/脸侧羽化")
    ap.add_argument("--jaw-feather-px", type=float, default=8.0, help="region_aware 下颌羽化 6~10px")
    ap.add_argument("--jaw-start-ratio", type=float, default=0.68)
    ap.add_argument("--jaw-full-ratio", type=float, default=0.82)
    # 补洞（§17.3 / §20.3 / §22.3）
    ap.add_argument("--fill-mode", choices=["wall_residual", "residual", "plate"], default="wall_residual")
    ap.add_argument("--ring-width-px", type=int, default=30)
    ap.add_argument("--wall-delta-e", type=float, default=10.0, help="墙面样本 ΔE 门限（§20.3.4）")
    ap.add_argument("--fill-outer-feather-px", type=int, default=0, help="Round E 固定 0：只 100% 替换")
    ap.add_argument("--neck-keep-ratio", type=float, default=0.05,
                    help="A 脖子保护线 = 下颌 + ratio×脸高（§22.5.3；collar 启用时被 collar_bottom 覆盖）")
    ap.add_argument("--plate-expand-px", type=int, default=10, help="仅 plate 对照模式")
    # 帧范围调试（§22.5.5）
    ap.add_argument("--start-frame", type=int, default=0)
    ap.add_argument("--max-frames", type=int, default=0, help="0 = 到结尾")
    # 旧头清理（§17.6）
    ap.add_argument("--mask-union", choices=["motion_safe", "current"], default="motion_safe")
    ap.add_argument("--safe-margin-px", type=int, default=8)
    # 下颌—脖子（Round G，§20.6/§22.7；v4 起 deprecated）
    ap.add_argument("--neck-collar-enabled", action="store_true",
                    help="⚠️ v4 已废弃（§24）：B neck collar 被人工否决，仅历史复现")
    ap.add_argument("--neck-collar-ratio", type=float, default=0.12, help="collar 高 = ratio×脸高（0.10~0.14）")
    ap.add_argument("--neck-collar-soft-px", type=float, default=14.0, help="下颌→A 脖子纵向过渡 10~18px")
    ap.add_argument("--neck-color-strength", type=float, default=0.0,
                    help="collar 弱局部颜色匹配强度；0=先出 nocolor 版（§22.7.5）")
    # A 脖子保护（Round H，§24.10/§24.11）
    ap.add_argument("--a-neck-preserve-enabled", action="store_true",
                    help="按 class14 真实轮廓保留 A 原脖子（与 neck_collar 互斥）")
    ap.add_argument("--necks-dir", type=Path, default=None, help="work/segment/necks（A neck masks）")
    ap.add_argument("--a-neck-upward-px", type=int, default=3, help="neck 保护只向上延展 px（>6 须人工确认）")
    ap.add_argument("--jaw-color-strength", type=float, default=0.0,
                    help="H3：B 下颌带局部调色 0.15~0.20（几何过闸后才启用）")
    # 下颌—脖子接合（第五轮，§26.10）
    ap.add_argument("--jaw-underlay-enabled", action="store_true",
                    help="§26.9：A jaw/neck junction underlay + neck 顶部 taper 塑形"
                         "（v5 主路径；关闭则完全回退 v4 行为）")
    ap.add_argument("--jaw-underlay-px", type=int, default=10,
                    help="A 接合皮肤向上保留距离 8~12（小了仍白裂，大了可能露 A 双下巴）")
    ap.add_argument("--neck-taper-height-px", type=int, default=16,
                    help="A neck 顶部从 B 下颌宽过渡到 A 原宽的高度 12~20；0=不 taper（I1 隔离变量）")
    ap.add_argument("--neck-taper-side-px", type=int, default=2,
                    help="下颌包络左右扩展 1~4（过大重现脖子侧尖，过小脖子太细）")
    # 白横纹封闭（第六轮，§28.3/28.5）
    ap.add_argument("--junction-bridge-max-gap-px", type=int, default=6,
                    help="逐列封闭下颌—脖子窄缝的最大 gap 4/6/8；0=关闭 bridge；"
                         ">8 必须人工确认（大间隙可能是真背景）")
    # 人体接合桥 + 独立验收（第七轮，§30.5/30.6）
    ap.add_argument("--raw-skins-dir", type=Path, default=None,
                    help="work/segment/raw_skins（class1∪14 不减 head_pad）；提供后启用 skin bridge + audit")
    ap.add_argument("--raw-necks-dir", type=Path, default=None,
                    help="work/segment/raw_necks（class14 无组件过滤）；audit ROI 独立取 neck 顶部")
    ap.add_argument("--skin-bridge-max-gap-px", type=int, default=14,
                    help="§30.5 逐列 skin bridge 的最大垂向 gap 8/12/14")
    ap.add_argument("--skin-bridge-no-cap", action="store_true",
                    help="gap 超限列仍保留 span 内 raw skin 像素（不变量字面；锚点外侧细 skin 列片）")
    ap.add_argument("--jaw-underlay-band-px", type=int, default=20,
                    help="§30.6 第 1 部分语义：head 底带内 raw skin 铺垫宽度；0=关闭")
    ap.add_argument("--wall-max-texture", type=float, default=0.0,
                    help="§30.8 smooth_plane 纹理能量上限（Laplacian RMS）；>0 启用，超阈显式报错拒绝")
    # 下颌局部亮度渐变（第七轮 M3，§30.7）
    ap.add_argument("--jaw-gradient-strength", type=float, default=0.0,
                    help="D3：B 下颌底带低频 LAB 向 A 接合皮肤匹配强度 0.5/0.75/1.0；0=关")
    ap.add_argument("--jaw-gradient-band-px", type=int, default=28,
                    help="下颌亮度渐变带宽 20~32px")
    # 几何微调
    ap.add_argument("--scale-bias", type=float, default=1.0)
    ap.add_argument("--x-offset", type=float, default=0.0)
    ap.add_argument("--y-offset", type=float, default=0.0)
    ap.add_argument("--neck-cut-ratio", type=float, default=1.35)
    ap.add_argument("--neck-cut-soft", type=float, default=25.0)
    # 色彩
    ap.add_argument("--color-strength", type=float, default=0.55)
    ap.add_argument("--max-delta-l", type=float, default=20.0)
    ap.add_argument("--max-delta-ab", type=float, default=12.0)
    ap.add_argument("--color-ema", type=float, default=0.9)
    # 输出
    ap.add_argument("--crf", type=int, default=14)
    ap.add_argument("--debug-dir", type=Path, default=None)
    ap.add_argument("--debug-every", type=int, default=50)
    return ap


def main(argv=None) -> int:
    args = build_argparser().parse_args(argv)
    return run_composite(args)


if __name__ == "__main__":
    raise SystemExit(main())
