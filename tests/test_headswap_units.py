"""headswap 纯函数单元测试（编排环境 pytest）。"""

from __future__ import annotations

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from headswap.color_transfer import ColorMatcher, lab_stats
from headswap.composite_head import decompose, rebuild, similarity, soft_cut
from headswap.segment_head import rect_iou, square_roi


def test_rect_iou_basic():
    a = (0, 0, 10, 10)
    assert rect_iou(a, a) == pytest.approx(1.0)
    assert rect_iou(a, (5, 0, 15, 10)) == pytest.approx(5 * 10 / (100 + 100 - 50))
    assert rect_iou(a, (20, 20, 30, 30)) == 0.0


def test_square_roi_clamped_inside_frame():
    # 人脸贴近左上角，ROI 仍完整落在画面内
    x0, y0, size = square_roi((1000, 1000, 3), np.array([0, 0, 100, 120], dtype=np.float32), 2.6)
    assert 0 <= x0 and 0 <= y0 and x0 + size <= 1000 and y0 + size <= 1000
    assert size == 312  # 120 * 2.6


def test_similarity_maps_points():
    src = np.array([[0.0, 0.0], [10.0, 0.0], [5.0, 5.0]], dtype=np.float32)
    # 目标 = 放大2倍 + 平移(3,4)
    dst = np.array([[3.0, 4.0], [23.0, 4.0], [13.0, 14.0]], dtype=np.float32)
    m = similarity(src, dst)
    assert m is not None
    mapped = cv2.transform(src.reshape(-1, 1, 2), m).reshape(-1, 2)
    assert np.allclose(mapped, dst, atol=1e-3)
    s, angle, tx, ty = decompose(m)
    assert s == pytest.approx(2.0, abs=1e-3)
    assert angle == pytest.approx(0.0, abs=1e-6)
    assert tx == pytest.approx(3.0, abs=1e-3)
    # roundtrip
    m2 = rebuild(*decompose(m))
    assert np.allclose(m, m2, atol=1e-6)


def test_similarity_collinear_still_solves():
    # 共线三点 LMEDS 仍给出合理（近似恒等）解；similarity 只拒绝解失败/极端 scale
    src = np.array([[0.0, 0.0], [10.0, 0.0], [5.0, 0.0]], dtype=np.float32)
    m = similarity(src, src)
    assert m is not None
    s, _, _, _ = decompose(m)
    assert s == pytest.approx(1.0, abs=1e-3)


def test_soft_cut_bounds():
    ramp = soft_cut(100, cut_y=50.0, soft=10.0)
    assert ramp[0, 0] == 1.0
    assert ramp[99, 0] == 0.0
    assert ramp[50, 0] == pytest.approx(0.5)


def _solid_image(bgr, size=64):
    return np.full((size, size, 3), bgr, dtype=np.uint8)


def test_lab_stats_requires_samples():
    img = _solid_image((120, 100, 90))
    empty = np.zeros((64, 64), dtype=np.uint8)
    assert lab_stats(img, empty) is None
    stats = lab_stats(img, np.full((64, 64), 255, dtype=np.uint8))
    assert stats is not None
    mean, std = stats
    assert mean.shape == (3,) and (std > 0).all()


def test_color_matcher_moves_toward_reference():
    src = _solid_image((60, 60, 200))   # 偏红
    ref_frame = _solid_image((200, 60, 60))  # 偏蓝
    mask = np.full((64, 64), 255, dtype=np.uint8)
    matcher = ColorMatcher(strength=1.0, ema=0.0)
    matcher.feed(lab_stats(src, mask), lab_stats(ref_frame, mask))
    out = matcher.apply(src.copy(), mask)
    # BGR：B(通道0)应上升（向参考的蓝色靠近），R(通道2)应下降；幅度受 delta 钳制
    assert out[:, :, 0].mean() >= src[:, :, 0].mean()
    assert out[:, :, 2].mean() <= src[:, :, 2].mean()
    # mask 外不动
    partial = np.zeros((64, 64), dtype=np.uint8)
    partial[:32] = 255
    matcher2 = ColorMatcher(strength=1.0, ema=0.0)
    matcher2.feed(lab_stats(src, mask), lab_stats(ref_frame, mask))
    out2 = matcher2.apply(src.copy(), partial)
    assert (out2[32:] == src[32:]).all()


# ---------------- 第二轮重构（docs §17.11） ----------------
import numpy.testing as npt

from headswap.composite_head import (
    centered_smooth,
    fit_plane_fill,
    hampel,
    inner_feather_alpha,
    motion_safe_union,
    offline_filter,
    rigid_from_eyes,
    warp_premultiplied,
)


def _kps():
    b = np.array([[100.0, 100.0], [200.0, 102.0], [150.0, 150.0], [130.0, 180.0], [170.0, 182.0]])
    a = b * 2.0 + np.array([10.0, 20.0])
    return b.astype(np.float32), a.astype(np.float32)


def test_rigid_from_eyes_maps_eyes_exactly():
    b, a = _kps()
    m = rebuild(*rigid_from_eyes(b, a))
    mapped = cv2.transform(b.reshape(-1, 1, 2), m).reshape(-1, 2)
    npt.assert_allclose(mapped[:2], a[:2], atol=0.1)  # 双眼精确对齐


def test_mouth_corners_do_not_affect_transform():
    b, a = _kps()
    b2 = b.copy()
    b2[3] += np.array([30.0, -20.0])  # 嘴角大幅移动
    b2[4] -= np.array([10.0, 25.0])
    assert rigid_from_eyes(b2, a) == rigid_from_eyes(b, a)  # 变换完全不变


def test_premultiplied_warp_blocks_background():
    frame = np.zeros((64, 64, 3), np.uint8)
    frame[..., 1] = 255  # 全绿背景
    frame[:32, ..., 2] = 255  # 上半红前景
    frame[:32, ..., 1] = 0
    alpha_src = np.zeros((64, 64), np.float32)
    alpha_src[:32] = 1.0
    m = np.array([[1.0, 0.0, 8.0], [0.0, 1.0, 4.0]])  # 平移 warp
    rgb, walpha = warp_premultiplied(frame, alpha_src, m, (64, 64))
    outside = walpha < 0.02
    # mask 外（含 warp 插值边界）不得出现绿色前景背景混合
    assert rgb[outside].max() == 0
    # 边界带内也不应有纯绿像素（前景是红，背景被预乘为零）
    band = (walpha >= 0.02) & (walpha < 0.98)
    if band.any():
        assert rgb[band][..., 1].max() < 200  # G 通道不被绿色背景主导


def test_inner_feather_strictly_zero_outside_mask():
    alpha_src = np.zeros((128, 128), np.float32)
    alpha_src[20:100, 20:100] = 1.0
    wa = alpha_src.copy()  # 恒等 warp
    alpha, width = inner_feather_alpha(wa, feather_px=6)
    outside = wa < 0.5
    assert alpha[outside].max() == 0.0  # 原 mask 外严格为 0
    assert alpha[60, 60] == 1.0  # 内部核心为 1
    assert 0 < width < 12  # 过渡带量级与 feather 一致


def test_residual_fill_only_touches_residual_and_protect():
    frame = np.full((80, 80, 3), 120, np.uint8)
    residual = np.zeros((80, 80), bool)
    residual[30:50, 30:50] = True
    protect = np.zeros((80, 80), np.uint8)
    protect[30:35, 30:50] = 255  # residual 内的一块保护区
    frame[30:35, 30:50] = 200  # 保护区内 distinctive 像素
    filled, stats = fit_plane_fill(frame, residual.copy(), protect, ring_width=10)
    npt.assert_array_equal(filled[20:30, :], frame[20:30, :])  # 非差集区域不动
    npt.assert_array_equal(filled[30:35, 30:50], frame[30:35, 30:50])  # protect 绝不修改
    assert stats["filled_px"] > 0
    changed = (filled.astype(int) - frame.astype(int)).sum(axis=2) != 0
    assert not (changed & ~residual).any()


def test_motion_safe_union_covers_leading_edge():
    prev = np.zeros((100, 100), np.uint8)
    prev[30:70, 20:60] = 255
    cur = np.zeros((100, 100), np.uint8)
    cur[30:70, 25:65] = 255  # 头右移 5px：前缘 60~65 是新进入区域
    prev_kps = np.array([[30.0, 40.0], [50.0, 40.0]], np.float32)
    cur_kps = np.array([[35.0, 40.0], [55.0, 40.0]], np.float32)  # 眼睛同步右移 5px
    union = motion_safe_union(cur, prev, prev_kps, cur_kps, margin_px=2) > 0
    assert union[30:70, 62:66].all()  # 运动补偿后前缘被覆盖
    # 并集必须包含当前帧 mask（本体永远在清理区内）
    assert (union[30:70, 25:65]).all()


def test_hampel_replaces_spike_but_keeps_signal():
    t = np.arange(80, dtype=np.float64)
    sig = np.sin(t / 6.0) * 5 + 100
    spiked = sig.copy()
    spiked[40] += 60  # 单点尖峰
    fixed = hampel(spiked, window=7, sigma=3.0)
    assert abs(fixed[40] - sig[40]) < 1.0  # 尖峰被拉回
    npt.assert_allclose(fixed[:30], sig[:30], atol=1e-9)  # 正常段保持


def test_centered_smooth_has_zero_phase_lag():
    t = np.arange(200, dtype=np.float64)
    sig = np.sin(t / 9.0)
    smoothed = centered_smooth(sig, window=11)
    # 互相关峰值应在 lag 0（居中滤波无相位延迟）
    lags = range(-4, 5)
    corrs = [float(np.corrcoef(sig[: -abs(l) or None], np.roll(smoothed, l)[: -abs(l) or None])[0, 1]) for l in lags]
    assert int(np.argmax(corrs)) == 4  # lags[4] == 0


def test_offline_filter_outputs_valid_params():
    raw = [(1.0 + 0.01 * np.sin(i / 5.0), 0.01 * np.sin(i / 7.0), 400 + 2 * np.sin(i / 6.0), 600) for i in range(60)]
    out = offline_filter(raw, hampel_window=7, smooth_window=11)
    assert len(out) == 60
    s = np.array([p[0] for p in out])
    assert 0.98 < s.mean() < 1.02 and s.std() < 0.02  # 幅度不被放大


# ---------------- 第三轮 Round E（docs §22.9） ----------------

import json as _json
from pathlib import Path as _Path

import yaml

from headswap.composite_head import (
    WallModelState,
    bump_mode_count,
    build_fill_protect_mask,
    build_neck_keep_mask,
    choose_fallback_wall_model,
    compose_debug_grid,
    fit_wall_fill,
    select_wall_samples,
    strip_debug_arrays,
    trim_hard_matte,
    wall_seed_mask,
)

WALL_BGR = (205, 210, 215)   # 浅冷灰白墙
SKIN_BGR = (110, 150, 190)   # 米黄肤色（v2 光晕的颜色）


def _wall_scene(size=200):
    """白墙 + 旧头肤色块 + 环带内独立肤色补丁的合成场景。"""
    frame = np.full((size, size, 3), WALL_BGR, np.uint8)
    old_head_safe = np.zeros((size, size), bool)
    old_head_safe[70:130, 60:120] = True          # 旧头（含肤色像素）
    frame[old_head_safe] = SKIN_BGR
    skin_patch = np.zeros((size, size), bool)     # 环带内、旧头安全区外的肤色补丁：
    skin_patch[85:105, 130:155] = True            # 只能靠 ΔE 门限拒绝
    frame[skin_patch] = SKIN_BGR
    residual = np.zeros((size, size), bool)
    residual[75:125, 65:115] = True
    face_box = np.array([65, 70, 115, 120], np.float32)
    fill_protect = np.zeros((size, size), bool)
    return frame, old_head_safe, fill_protect, face_box, residual, skin_patch


def test_wall_seed_lies_above_head_and_outside_old_head():
    frame, old_head_safe, fill_protect, face_box, *_ = _wall_scene()
    seed = wall_seed_mask(frame.shape, face_box, old_head_safe | fill_protect)
    assert seed.sum() > 200
    assert not (seed & old_head_safe).any()       # 种子不得落在旧头安全区
    yy = np.nonzero(seed)[0]
    assert yy.max() < 70                          # 种子在头顶上方墙面带


def test_wall_samples_reject_old_head_and_skin():
    # §22.9-1：样本不得选中 old_head_safe；§22.9-2：肤色簇即使内部一致（低 RMS）
    # 也必须被 ΔE 门限拒绝
    frame, old_head_safe, fill_protect, face_box, residual, skin_patch = _wall_scene()
    res_u8 = residual.astype(np.uint8) * 255
    k_out = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (61, 61))
    k_in = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    ring = (cv2.dilate(res_u8, k_out) > 0) & ~(cv2.dilate(res_u8, k_in) > 0)
    ring &= ~(old_head_safe | fill_protect)
    samples, wall_lab, seed = select_wall_samples(
        frame, ring, old_head_safe, fill_protect, face_box, delta_e_threshold=10.0
    )
    assert samples is not None and samples.sum() >= 300
    assert not (samples & old_head_safe).any()
    assert not (samples & skin_patch).any()       # ΔE 门限拒绝肤色


def test_fit_wall_fill_fills_residual_with_wall_color():
    # §22.9-3：residual 内 100% 替换，不混回原帧（肤色值必须全部消失）
    frame, old_head_safe, fill_protect, face_box, residual, _ = _wall_scene()
    out, stats, state = fit_wall_fill(
        frame, residual, old_head_safe, fill_protect, face_box,
        ring_width=30, wall_delta_e=10.0, outer_feather_px=0,
    )
    assert stats["mode"] == "wall_plane" and not stats["fallback"]
    assert stats["wall_sample_px"] >= 300
    filled = out[residual].astype(int)
    wall = np.array(WALL_BGR, int)
    assert np.abs(filled - wall).max() <= 2        # 填成墙色，不是肤色
    orig = frame[residual].astype(int)
    assert not (np.abs(filled - orig) <= 1).all(axis=1).any()  # 无原帧回混
    assert state.source == "current_frame"
    assert stats["fill_wall_delta_e_mean"] is not None and stats["fill_wall_delta_e_mean"] < 3
    # 非 residual 且非差集区域不动
    untouched = (~residual) & (~old_head_safe)
    npt.assert_array_equal(out[untouched], frame[untouched])


def test_fit_wall_fill_fallback_reuses_previous_model():
    # §22.9-4：当前帧种子/样本失败时复用上一帧墙面平面，禁止原帧 TELEA
    frame, old_head_safe, fill_protect, face_box, residual, _ = _wall_scene()
    _, _, prev_state = fit_wall_fill(
        frame, residual, old_head_safe, fill_protect, face_box,
    )
    # 把 face box 推到画面顶部使种子带不足 200px
    bad_box = np.array([65, 10, 115, 60], np.float32)
    out, stats, state = fit_wall_fill(
        frame, residual, old_head_safe, fill_protect, bad_box,
        previous_state=prev_state,
    )
    assert stats["fallback"] and stats["fallback_source"] == "previous_frame"
    assert stats["mode"] == "previous_frame"
    assert stats["mode"] != "inpaint"              # TELEA 路径已删除
    assert np.abs(out[residual].astype(int) - np.array(WALL_BGR, int)).max() <= 2


def test_fit_wall_fill_fallback_global_then_seed_median():
    frame, old_head_safe, fill_protect, face_box, residual, _ = _wall_scene()
    # global 链：无 previous，但有任务级全局模型
    _, _, good = fit_wall_fill(frame, residual, old_head_safe, fill_protect, face_box)
    g = WallModelState(coef=good.coef.copy(), wall_lab=good.wall_lab.copy(), source="global")
    bad_box = np.array([65, 10, 115, 60], np.float32)
    _, stats, _ = fit_wall_fill(
        frame, residual, old_head_safe, fill_protect, bad_box, global_state=g
    )
    assert stats["fallback"] and stats["fallback_source"] == "global"
    # seed_median 链：种子可用但采样环被保护区吞掉
    cover = np.ones((200, 200), bool)
    cover[20:45, 30:170] = False                   # 只留种子带
    _, stats, _ = fit_wall_fill(
        frame, residual, old_head_safe, cover.astype(np.uint8), face_box,
    )
    assert stats["fallback"] and stats["fallback_source"] == "seed_median"


def test_fit_wall_fill_raises_when_no_model_available():
    # §22.9-5：previous/global/seed 全缺失时明确失败
    frame, old_head_safe, fill_protect, face_box, residual, _ = _wall_scene()
    bad_box = np.array([65, 10, 115, 60], np.float32)
    with pytest.raises(RuntimeError, match="墙面模型"):
        fit_wall_fill(frame, residual, old_head_safe, fill_protect, bad_box)


def test_bump_mode_count_accepts_new_keys():
    # §22.9-6：wall_plane/previous_frame/global/seed_median 不 KeyError
    frames = {"plane": 0, "inpaint": 0, "plate": 0}
    bump_mode_count(frames, "wall_plane")
    bump_mode_count(frames, "previous_frame")
    bump_mode_count(frames, "global")
    bump_mode_count(frames, "seed_median")
    bump_mode_count(frames, "")
    assert frames["wall_plane"] == 1 and frames["global"] == 1
    assert frames["unknown"] == 1 and "" not in frames


def test_strip_debug_arrays_keeps_stats_json_safe():
    # §22.9-7：_samples/_seed 不进入 JSON
    frame, old_head_safe, fill_protect, face_box, residual, _ = _wall_scene()
    _, stats, _ = fit_wall_fill(frame, residual, old_head_safe, fill_protect, face_box)
    assert "_samples" in stats and "_seed" in stats
    clean = strip_debug_arrays(stats)
    assert "_samples" not in clean and "_seed" not in clean
    _json.dumps(clean)  # 不抛异常


def test_inner_feather_preserves_soft_tail_below_half():
    # §22.9-8：<0.5、>support_eps 的软 alpha 尾部必须保留（Round G 前置能力）
    wa = np.zeros((80, 80), np.float32)
    wa[20:60, 20:50] = 1.0                    # 硬主体
    wa[20:60, 50] = 0.4                       # 渐变尾部
    wa[20:60, 51] = 0.2
    wa[20:60, 52] = 0.1
    alpha, _ = inner_feather_alpha(wa, feather_px=4.0, support_eps=0.01)
    assert alpha[40, 50] > 0.0 and alpha[40, 51] > 0.0 and alpha[40, 52] > 0.0
    assert alpha[40, 52] == pytest.approx(min(1.0 / 4.0, 0.1), abs=1e-4)  # min(内距, wa)
    # 默认 support_eps=0.5（v2 行为）：尾部清零
    alpha_v2, _ = inner_feather_alpha(wa, feather_px=4.0)
    assert alpha_v2[40, 50] == 0.0 and alpha_v2[40, 52] == 0.0


def test_neck_keep_mask_splits_color_ref_and_fill_protect():
    # §20.4 / §22.9 前置：颜色参考 ≠ 补洞保护——缝合线以上旧皮肤允许清理
    skins = np.zeros((100, 100), np.uint8)
    skins[40:90, 30:70] = 255               # 旧下颌(40~60) + A 脖子(60~90)
    keep = build_neck_keep_mask(skins, neck_keep_y=60.0)
    assert not keep[50, 50]                 # 缝合线以上：不保护（可清理）
    assert keep[70, 50]                     # 缝合线以下：保护 A 脖子
    protect = build_fill_protect_mask(
        np.zeros((100, 100, 3), np.uint8), np.array([20, 20, 80, 60], np.float32),
        skins, 60.0,
    )
    color_ref = skins > 0
    assert (protect.astype(bool) & color_ref).any()   # 保护集是参考集的真子集区段
    assert (color_ref & ~protect.astype(bool)).any()  # 旧下颌皮肤留在参考、不在保护


def test_trim_hard_matte_removes_only_boundary():
    # §20.11-6：白色 matte 边界被删，核心保留
    mask = np.zeros((60, 60), np.uint8)
    mask[10:50, 10:50] = 255
    trimmed = trim_hard_matte(mask, 1)
    assert trimmed[10, 10] == 0 and trimmed[10, 30] == 0    # 边界一圈删除
    assert trimmed[12:48, 12:48].min() == 255               # 核心完整
    assert (trim_hard_matte(mask, 0) == mask).all()         # erode 0 = 恒等二值化


def test_compose_debug_grid_requires_nine_panels_with_out():
    # §22.9-14：3×3 网格必须包含最终 out，不得再"声称 7 联实际 6 格"
    canvas = np.zeros((300, 300, 3), np.uint8)
    panels = [canvas.copy() for _ in range(9)]
    panels[8][...] = (0, 0, 255)             # 最终 out = 纯蓝
    grid = compose_debug_grid(panels, (300, 300))
    assert grid.shape[:2] == (300, 300)      # 3x3，每格 100x100
    assert grid[250, 250, 2] == 255 and grid[250, 250, 0] == 0  # 右下角是 out（BGR 红）
    with pytest.raises(ValueError):
        compose_debug_grid(panels[:8], (300, 300))


def test_round_e_config_must_not_touch_b_alpha():
    # §22.9-13：Round E 配置不得意外启用 B erosion / collar / 非 v2 羽化
    p = _Path(__file__).resolve().parents[1] / "config" / "headswap.hs-p1-0003-fill.yaml"
    if not p.is_file():
        pytest.skip("config/headswap.hs-p1-0003-fill.yaml 尚未创建（Write 待批准）")
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    comp = cfg["composite"]
    assert comp["fill_mode"] == "wall_residual"
    assert comp.get("b_mask_erode_px", 0) == 0
    assert not comp.get("neck_collar_enabled", False)
    assert comp["alpha_mode"] == "inner" and comp["alpha_feather_px"] == 6


# ---------------- 第三轮 Round G（docs §22.9 第 9~12 条） ----------------

from headswap.composite_head import (
    b_fallback_trio,
    build_neck_collar_mask,
    build_warped_head_layers,
    collar_bottom_of,
    neck_vertical_ramp,
)
from headswap.segment_head import (
    HEAD_CLASSES,
    NECK_CLASS,
    HeadSegmenter,
    filter_components,
    filter_neck_near_primary_face,
)


class _StubParser:
    """返回固定 labels 的假 BiSeNet（不加载 onnx）。"""

    def __init__(self, labels):
        self.labels = labels

    def parse(self, image_bgr):
        return self.labels


def _b_canvas_labels():
    labels = np.zeros((160, 160), np.uint8)
    labels[30:70, 30:90] = 1     # 脸 skin
    labels[10:35, 25:95] = 17    # hair
    labels[70:90, 40:80] = 14    # neck（主脸正下方）
    labels[95:160, 0:160] = 16   # cloth（远处，必须剔除）
    labels[50:55, 138:158] = 1   # 远处皮肤（窗口外，必须剔除）
    return labels


def test_segment_full_parts_head_matches_v2():
    # §22.9-9：重构后 head 与 v2 segment_full 逐像素一致（含 erode/dilate/EMA 路径）
    labels = _b_canvas_labels()
    box = np.array([28, 30, 92, 72], np.float32)
    frame = np.zeros((160, 160, 3), np.uint8)
    for erode, dilate, ema in ((0, 0, 0.0), (2, 8, 0.6)):
        seg = HeadSegmenter(_StubParser(labels), dilate_px=dilate, erode_px=erode, temporal_ema=ema)
        head_new, neck_new, _ = seg.segment_full_parts(frame, box)

        head = np.isin(labels, HEAD_CLASSES).astype(np.uint8) * 255
        head = filter_components(head, box)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        head = cv2.morphologyEx(head, cv2.MORPH_CLOSE, k, iterations=1)
        head = cv2.morphologyEx(head, cv2.MORPH_OPEN, k, iterations=1)
        if erode > 0:
            head = cv2.erode(head, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (erode * 2 + 1, erode * 2 + 1)))
        if dilate > 0:
            head = cv2.dilate(head, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate * 2 + 1, dilate * 2 + 1)))
        assert (head_new == head).all()

        expected_neck = filter_neck_near_primary_face((labels == NECK_CLASS).astype(np.uint8) * 255, box)
        expected_neck = cv2.morphologyEx(
            expected_neck, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        )
        assert (neck_new == expected_neck).all()
        assert head_new[95:160, :].max() == 0            # cloth 不进 head
        assert head_new[50:55, 138:158].max() == 0       # 扩展窗口外的皮肤不进 head
        assert neck_new[80:, 40:80].max() == 0           # 窗口以下的 neck 不进 collar


def test_b_fallback_trio_reuses_all_three():
    # §22.9-10：B 检测失败时 head/neck/box 三者一起沿用
    box = np.array([1.0, 2.0, 3.0, 4.0], np.float32)
    head = np.full((8, 8), 255, np.uint8)
    neck = np.zeros((8, 8), np.uint8)
    b, h, n = b_fallback_trio(box, head, neck)
    assert b is box and h is head and n is neck
    with pytest.raises(RuntimeError):
        b_fallback_trio(None, head, neck)
    with pytest.raises(RuntimeError):
        b_fallback_trio(box, None, neck)


def test_neck_collar_window_and_ramp_monotonic():
    # §22.9-11：collar 窗口外严格 0；纵向 ramp 自上而下单调下降、bottom 以下 0
    neck = np.zeros((200, 160), np.uint8)
    neck[85:150, 50:110] = 255
    box_b = np.array([40, 30, 120, 90], np.float32)  # bh=60 → 窗口 y∈[88, 97]
    collar = build_neck_collar_mask(neck, box_b, ratio=0.12)
    assert collar[:88].max() == 0 and collar[98:].max() == 0
    assert collar[95, 60] == 255 and collar[87, 60] == 0  # 窗口 y∈[88,97]，neck 从 85 起被截到 88

    a_box = np.array([440, 300, 640, 560], np.float32)
    bottom = collar_bottom_of(a_box, 0.12)           # 560 + 0.12*260 = 591.2
    ramp = neck_vertical_ramp((1000, 1080), bottom, 14.0)
    col = ramp[:, 0]                                # ramp 形状 (H,1)，逐行取值
    assert (np.diff(col) <= 1e-9).all()              # 单调不增
    assert col[int(bottom) + 1] == 0.0               # bottom 以下严格 0
    assert col[int(bottom) - 30] == 1.0


def test_build_warped_head_layers_returns_consistent_bottom():
    # §22.9-12：collar_bottom_y 与 collar_bottom_of 完全一致（fill_protect 同源取线）
    frame_b = np.full((200, 160, 3), (10, 60, 90), np.uint8)
    head = np.zeros((200, 160), np.uint8)
    head[20:90, 40:120] = 255
    neck = np.zeros((200, 160), np.uint8)
    neck[85:140, 60:100] = 255
    box_b = np.array([40, 30, 120, 90], np.float32)
    a_box = np.array([440, 300, 640, 560], np.float32)
    m = np.array([[2.0, 0.0, 400.0], [0.0, 2.0, 200.0]])  # 放大 2 倍平移
    layers = build_warped_head_layers(
        frame_b, box_b, head, neck, m, (1080, 1000),
        head_erode_px=1, head_feather_px=4.0,
        neck_collar_ratio=0.12, neck_collar_soft_px=14.0, a_face_box=a_box,
    )
    assert layers["collar_bottom_y"] == pytest.approx(collar_bottom_of(a_box, 0.12))
    # head 核心不透明；collar 区（A 画布 y≈376..394, x≈520..600）alpha 接近 1
    assert layers["alpha_final"][280, 540] == pytest.approx(1.0, abs=0.02)
    assert layers["alpha_neck"][385, 560] > 0.9
    # B neck 缺失时自动退化：alpha_neck 全 0，alpha_final == alpha_head
    layers2 = build_warped_head_layers(
        frame_b, box_b, head, None, m, (1080, 1000),
        head_erode_px=1, head_feather_px=4.0,
        neck_collar_ratio=0.12, neck_collar_soft_px=14.0, a_face_box=a_box,
    )
    assert layers2["alpha_neck"].max() == 0.0
    npt.assert_array_equal(layers2["alpha_final"], layers2["alpha_head"])
