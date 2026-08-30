"""LAB 空间 Reinhard 色彩迁移 + 时序 EMA。

只在 import 处依赖 cv2/numpy，可同时在编排环境和 liveportrait 环境运行。
"""

from __future__ import annotations

import cv2
import numpy as np


def lab_stats(image_bgr: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """统计 mask 区域的 LAB 均值/标准差。样本太少时返回 None。"""
    if image_bgr is None or mask is None:
        return None
    m = mask.astype(bool)
    if m.sum() < 200:
        return None
    lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    pixels = lab[m]
    return pixels.mean(axis=0), pixels.std(axis=0) + 1e-6


class ColorMatcher:
    """逐帧色彩匹配器：对参考统计量做 EMA，避免成片色彩闪烁。

    用法：每帧先 feed(src_stats, ref_stats)，再 apply(image, mask)。
    参考统计断档（比如参考皮肤区域被完全遮挡）时沿用上一次平滑值。
    """

    def __init__(
        self,
        strength: float = 0.55,
        max_delta_l: float = 20.0,
        max_delta_ab: float = 12.0,
        ema: float = 0.90,
    ) -> None:
        self.strength = float(np.clip(strength, 0.0, 1.0))
        self.max_delta_l = float(max_delta_l)
        self.max_delta_ab = float(max_delta_ab)
        self.ema = float(np.clip(ema, 0.0, 0.99))
        self._src: tuple[np.ndarray, np.ndarray] | None = None
        self._ref: tuple[np.ndarray, np.ndarray] | None = None

    def feed(
        self,
        src_stats: tuple[np.ndarray, np.ndarray] | None,
        ref_stats: tuple[np.ndarray, np.ndarray] | None,
    ) -> None:
        if src_stats is not None:
            self._src = self._blend(self._src, src_stats)
        if ref_stats is not None:
            self._ref = self._blend(self._ref, ref_stats)

    def ready(self) -> bool:
        return self._src is not None and self._ref is not None

    def _blend(
        self, prev: tuple[np.ndarray, np.ndarray] | None, cur: tuple[np.ndarray, np.ndarray]
    ) -> tuple[np.ndarray, np.ndarray]:
        if prev is None:
            return cur
        a = self.ema
        return (a * prev[0] + (1 - a) * cur[0], a * prev[1] + (1 - a) * cur[1])

    def apply(self, image_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """把 image_bgr 的 mask 区域向参考颜色靠拢，其余像素不动。"""
        if not self.ready() or self.strength <= 0:
            return image_bgr
        m = mask.astype(bool)
        if not m.any():
            return image_bgr
        lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
        src_mean, src_std = self._src
        ref_mean, ref_std = self._ref
        transferred = (lab - src_mean) / src_std * ref_std + ref_mean
        delta = transferred - lab
        delta[..., 0] = np.clip(delta[..., 0], -self.max_delta_l, self.max_delta_l)
        delta[..., 1] = np.clip(delta[..., 1], -self.max_delta_ab, self.max_delta_ab)
        delta[..., 2] = np.clip(delta[..., 2], -self.max_delta_ab, self.max_delta_ab)
        out = lab.copy()
        out[m] = lab[m] + delta[m] * self.strength
        out = np.clip(out, 0, 255).astype(np.uint8)
        return cv2.cvtColor(out, cv2.COLOR_LAB2BGR)
