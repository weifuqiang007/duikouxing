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
       Round E 固定为 0（只做 100% 替换）。

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

    - collar 的纵向 ramp 在 A 画布施加（§22 Q2-2 裁准），不会被
      inner_feather_alpha 的 support 二值化截断；
    - head 内距羽化用 epsilon support（§22.4），保留软尾部；
    - B neck 缺失时 collar_src 为空，自动退化为纯 head（§20.6.5 回退）。
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
    raw_params: list[tuple[float, float, float, float]] = []
    for i in range(n_frames):
        fm = frame_meta[min(i, len(frame_meta) - 1)]
        kps_a = np.array(fm["kps"], dtype=np.float32)
        src = resolved[i]
        if src is None:
            raw_params.append(raw_params[-1] if raw_params else (1.0, 0.0, 0.0, 0.0))
            continue
        if args.transform_mode == "eyes":
            p = rigid_from_eyes(np.array(src["kps"], dtype=np.float32), kps_a)
            raw_params.append(p if p else (raw_params[-1] if raw_params else (1.0, 0.0, 0.0, 0.0)))
        else:  # five_point（旧版对照）
            m = similarity(np.array(src["kps"], dtype=np.float32), kps_a)
            raw_params.append(decompose(m) if m is not None else (raw_params[-1] if raw_params else (1.0, 0.0, 0.0, 0.0)))

    # ---------- 滤波 ----------
    if args.filter_mode == "offline":
        filt_params = offline_filter(
            raw_params, hampel_window=args.hampel_window, smooth_window=args.filter_window,
            scale_mode=args.scale_mode, angle_window=args.angle_window,
        )
    else:
        filt_params = raw_params  # 在线模式在循环内用 SmoothedTransform
    online = SmoothedTransform(rot=args.rot_smooth, trans=args.trans_smooth, window=args.transform_window)
    transforms_log = {"mode": f"{args.transform_mode}+{args.filter_mode}", "raw": raw_params, "filtered": filt_params}

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
        "frame_range": [int(args.start_frame), None],
    }
    widths_sum = 0
    erased_px_sum = 0
    prev_mask_a = None
    prev_kps_a = None
    prev_head_mask_b = None
    prev_neck_mask_b = None
    prev_box_b = None
    collar_px_sum = 0
    collar_bottom_sum = 0.0
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
        if args.neck_collar_enabled:
            # ---- Round G：分层 warp（§22.7.2），collar 纵向 ramp 在 A 画布施加 ----
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
            if args.alpha_mode == "inner":
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

        # ---- 差集补洞（§22.5.3：颜色参考与补洞保护彻底拆开）----
        color_reference = (
            skins_a > 0 if skins_a is not None else np.zeros((height, width), dtype=bool)
        )
        # Round G（§22.7.4）：collar 结束线以下才保护 A 脖子——与 collar_bottom 同源
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

        # 色彩迁移（仅新头有效区；参考 mask 用 color_reference，禁止用 fill_protect）
        head_zone = alpha_f > 0.6
        matcher.feed(lab_stats(head_rgb.astype(np.uint8), head_zone), lab_stats(frame_a, color_reference))
        if not matcher.ready():
            diag["color_skip"] += 1
        if matcher.ready():
            head_rgb = matcher.apply(head_rgb.astype(np.uint8), head_zone).astype(np.float32)

        # Round G（§22.7.5）：collar 弱局部颜色匹配——几何过闸后才启用
        # （neck_color_strength=0 时跳过，即先出 nocolor 版）
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

        out = np.clip(
            head_rgb * alpha_f[..., None] + clean_base.astype(np.float32) * (1.0 - alpha_f[..., None]),
            0, 255,
        ).astype(np.uint8)
        writer.append_data(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))
        diag["frames"] += 1

        # ---- 3×3 九格调试图 + 头部局部放大（§20.8 / §22.5.4）----
        if args.debug_dir and index % args.debug_every == 0:
            def b3(g):
                return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)

            wall_vis = np.zeros((height, width), np.uint8)
            if seed_dbg is not None:
                wall_vis[seed_dbg] = 255  # 墙面种子（白）
            if samples_dbg is not None and samples_dbg.any():
                wall_vis[samples_dbg] = 160  # 通过 ΔE 门限的墙面样本（灰）
            rp_vis = np.zeros((height, width), np.uint8)
            rp_vis[fill_protect] = 120  # 补洞保护区（灰）
            if residual is not None:
                rp_vis[residual] = 255  # 补洞差集（白）
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
            bw_a = bx1 - bx0
            cx0 = max(0, int(bx0 - 0.9 * bw_a))
            cx1 = min(width, int(bx1 + 0.9 * bw_a))
            cy0 = max(0, int(by0 - 1.2 * bh_a))
            cy1 = min(height, int(by1 + 0.6 * bh_a))
            cv2.imwrite(str(args.debug_dir / f"frame_{index:04d}_clean_base_crop.png"), clean_base[cy0:cy1, cx0:cx1])
            cv2.imwrite(str(args.debug_dir / f"frame_{index:04d}_final_crop.png"), out[cy0:cy1, cx0:cx1])
            cv2.imwrite(str(args.debug_dir / f"frame_{index:04d}_wall_samples.png"), wall_vis[cy0:cy1, cx0:cx1])

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
    # 变换与滤波（§17.5）
    ap.add_argument("--transform-mode", choices=["eyes", "five_point"], default="eyes")
    ap.add_argument("--filter-mode", choices=["offline", "online"], default="offline")
    ap.add_argument("--hampel-window", type=int, default=7)
    ap.add_argument("--filter-window", type=int, default=11)
    ap.add_argument("--scale-mode", choices=["smooth", "const"], default="smooth")
    ap.add_argument("--angle-window", type=int, default=0, help="roll 强平滑窗口，0=同 filter_window")
    ap.add_argument("--transform-window", type=int, default=9, help="在线模式中位数窗口")
    ap.add_argument("--rot-smooth", type=float, default=0.8)
    ap.add_argument("--trans-smooth", type=float, default=0.8)
    # alpha（§17.4 / §20.5.2）
    ap.add_argument("--alpha-mode", choices=["inner", "blur"], default="inner")
    ap.add_argument("--alpha-feather-px", type=float, default=6.0, help="内距羽化过渡宽 4~8px")
    ap.add_argument("--alpha-erode-px", type=int, default=4, help="仅 blur 对照模式")
    ap.add_argument("--b-mask-erode-px", type=int, default=0,
                    help="B 硬 mask 内缩去白色 matte（Round F=1；0=与 v2 一致）")
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
    # 下颌—脖子（Round G，§20.6/§22.7）
    ap.add_argument("--neck-collar-enabled", action="store_true")
    ap.add_argument("--neck-collar-ratio", type=float, default=0.12, help="collar 高 = ratio×脸高（0.10~0.14）")
    ap.add_argument("--neck-collar-soft-px", type=float, default=14.0, help="下颌→A 脖子纵向过渡 10~18px")
    ap.add_argument("--neck-color-strength", type=float, default=0.0,
                    help="collar 弱局部颜色匹配强度；0=先出 nocolor 版（§22.7.5）")
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
