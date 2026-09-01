"""LivePortrait 运动职责控制的纯 NumPy 实现。

本模块不依赖 LivePortrait/PyTorch，既可由运行在 liveportrait conda 环境中的
``scripts/liveportrait_runner.py`` 调用，也便于编排环境直接做单元测试。

LivePortrait 的 ``animation_region=all`` 会同时传递 R/exp/t/scale。整头贴回 A
身体时，外部合成还要负责定位，因此 ``rotation_exp`` 模式只保留相对旋转和表达，
并把 driving 的 t/scale 固定到首帧，使官方 pipeline 自然得到零相对平移和单位
尺度比，而无需修改被 .gitignore 排除的 vendor checkout。
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class RotationControl:
    pitch_gain: float = 0.65
    yaw_gain: float = 0.75
    roll_gain: float = 0.65
    pitch_limit_deg: float = 3.0
    yaw_limit_deg: float = 5.0
    roll_limit_deg: float = 3.0
    smooth_window: int = 7

    def validate(self) -> None:
        gains = (self.pitch_gain, self.yaw_gain, self.roll_gain)
        limits = (self.pitch_limit_deg, self.yaw_limit_deg, self.roll_limit_deg)
        if not all(np.isfinite(g) and 0.0 <= g <= 2.0 for g in gains):
            raise ValueError(f"pose gain 必须在 [0,2]：{gains}")
        if not all(np.isfinite(v) and 0.0 < v <= 30.0 for v in limits):
            raise ValueError(f"pose limit 必须在 (0,30] 度：{limits}")
        if self.smooth_window < 1 or self.smooth_window % 2 == 0:
            raise ValueError("pose_smooth_window 必须为正奇数")


def _centered_smooth_columns(values: np.ndarray, window: int) -> np.ndarray:
    """对 N×D 数组做边缘缩窗的对称平滑，不产生因果相位滞后。"""
    src = np.asarray(values, dtype=np.float64)
    if window <= 1 or len(src) <= 1:
        return src.copy()
    half = window // 2
    out = np.empty_like(src)
    for i in range(len(src)):
        lo, hi = max(0, i - half), min(len(src), i + half + 1)
        out[i] = np.mean(src[lo:hi], axis=0)
    return out


def _nearest_rotation(matrix: np.ndarray) -> np.ndarray:
    """SVD 投影到 SO(3)，清除数值误差，不允许隐式 shear/scale。"""
    u, _, vt = np.linalg.svd(np.asarray(matrix, dtype=np.float64))
    r = u @ vt
    if np.linalg.det(r) < 0:
        u[:, -1] *= -1
        r = u @ vt
    return r


def scale_relative_rotations(
    rotations: np.ndarray,
    control: RotationControl,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """缩放相对旋转并保持首帧为零运动。

    输入/输出均为 LivePortrait 使用的 N×3×3 旋转矩阵。其矩阵是常规欧拉矩阵的
    转置，但 Rodrigues 旋转向量仍保持三个轴的一致方向；正增益不会反转视觉运动。
    对每帧先计算 ``R_i @ R_0.T``，转 axis-angle，再按 x/y/z（pitch/yaw/roll）
    分轴平滑、增益和限幅，最后恢复为正交矩阵。禁止逐元素插值 3×3 矩阵。
    """
    control.validate()
    mats = np.asarray(rotations, dtype=np.float64)
    if mats.ndim != 3 or mats.shape[1:] != (3, 3) or len(mats) == 0:
        raise ValueError(f"rotations 应为非空 N×3×3，实际 {mats.shape}")
    r0 = _nearest_rotation(mats[0])
    raw = np.empty((len(mats), 3), dtype=np.float64)
    for i, matrix in enumerate(mats):
        rel = _nearest_rotation(matrix) @ r0.T
        raw[i] = cv2.Rodrigues(rel)[0].reshape(3)

    smooth = _centered_smooth_columns(raw, control.smooth_window)
    # 对称窗口会让边缘首帧混入后续运动；重新锚定，保证 frame0 严格为 I。
    smooth -= smooth[0]
    gains = np.array(
        [control.pitch_gain, control.yaw_gain, control.roll_gain], dtype=np.float64
    )
    limits = np.deg2rad(
        [control.pitch_limit_deg, control.yaw_limit_deg, control.roll_limit_deg]
    )
    used = np.clip(smooth * gains, -limits, limits)

    out = np.empty_like(mats)
    for i, rotvec in enumerate(used):
        rel = cv2.Rodrigues(rotvec.reshape(3, 1))[0]
        out[i] = _nearest_rotation(rel @ r0)
    diagnostics = {
        "raw_rotvec_deg": np.rad2deg(raw),
        "smoothed_rotvec_deg": np.rad2deg(smooth),
        "used_rotvec_deg": np.rad2deg(used),
    }
    return out.astype(np.float32), diagnostics


def control_motion_template(
    template: dict,
    control: RotationControl,
    expression_indices: tuple[int, ...] | None = None,
) -> tuple[dict, list[dict]]:
    """原位改写 LivePortrait driving template 为 rotation_exp 运动职责。

    表达 ``exp`` 和关键点保持官方结果；R 使用受控相对旋转；所有帧 t/scale 复制
    首帧。官方相对 ``all`` 路径因此仍转移 R+exp，但 t 差恒为 0、scale 比恒为 1。
    返回逐帧可 JSON/CSV 序列化的诊断行。
    """
    motion = template.get("motion") or []
    if not motion:
        raise ValueError("LivePortrait motion template 为空")
    rotations = np.concatenate(
        [np.asarray(item["R"], dtype=np.float32).reshape(1, 3, 3) for item in motion],
        axis=0,
    )
    controlled, rot_diag = scale_relative_rotations(rotations, control)
    t0 = np.asarray(motion[0]["t"], dtype=np.float32).copy()
    scale0 = np.asarray(motion[0]["scale"], dtype=np.float32).copy()
    exp0 = np.asarray(motion[0]["exp"], dtype=np.float32).copy()
    rows: list[dict] = []
    for i, item in enumerate(motion):
        t_raw = np.asarray(item["t"], dtype=np.float32).reshape(-1)
        scale_raw = np.asarray(item["scale"], dtype=np.float32).reshape(-1)
        item["R"] = controlled[i].reshape(np.asarray(item["R"]).shape)
        item["t"] = t0.copy()
        item["scale"] = scale0.copy()
        if expression_indices is not None:
            exp_raw = np.asarray(item["exp"], dtype=np.float32)
            exp_used = exp0.copy()
            valid = [idx for idx in expression_indices if 0 <= idx < exp_raw.shape[-2]]
            exp_used[..., valid, :] = exp_raw[..., valid, :]
            item["exp"] = exp_used
        rows.append(
            {
                "frame": i,
                "raw_pitch_rotvec_deg": float(rot_diag["raw_rotvec_deg"][i, 0]),
                "raw_yaw_rotvec_deg": float(rot_diag["raw_rotvec_deg"][i, 1]),
                "raw_roll_rotvec_deg": float(rot_diag["raw_rotvec_deg"][i, 2]),
                "used_pitch_rotvec_deg": float(rot_diag["used_rotvec_deg"][i, 0]),
                "used_yaw_rotvec_deg": float(rot_diag["used_rotvec_deg"][i, 1]),
                "used_roll_rotvec_deg": float(rot_diag["used_rotvec_deg"][i, 2]),
                "raw_tx": float(t_raw[0]) if len(t_raw) > 0 else 0.0,
                "raw_ty": float(t_raw[1]) if len(t_raw) > 1 else 0.0,
                "used_tx": float(t0.reshape(-1)[0]),
                "used_ty": float(t0.reshape(-1)[1]),
                "raw_scale": float(scale_raw[0]),
                "used_scale": float(scale0.reshape(-1)[0]),
                "expression_mode": "full" if expression_indices is None else "indices:" + ",".join(map(str, expression_indices)),
            }
        )
    return template, rows
