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


# ---------------- 第四轮 Round H（docs §24.18） ----------------

from headswap.composite_head import (
    check_neck_mode,
    extend_mask_upward,
    jaw_neck_gap_px,
    motion_safe_neck_union,
    region_aware_head_alpha,
)
from headswap.segment_head import filter_a_neck_near_primary_face


def _a_canvas_labels():
    """A 侧 ROI 场景：脸 + 单连通 V 形顶部脖子 + 远处 cloth。
    脖子必须在 filter_a_neck 窗口内（by1=85, bh=50 → y ∈ [81, 107]）。"""
    labels = np.zeros((160, 160), np.uint8)
    labels[40:80, 50:110] = 1    # 脸 skin
    labels[15:45, 40:120] = 17   # hair
    # neck：单连通、顶边 V 形（x=80 顶 84，向两侧降到 ~98/94）
    for x in range(52, 108):
        top = int(84 + abs(x - 80) * 0.5)
        labels[top:130, x] = 14
    labels[155:160, :] = 16      # cloth（必须剔除）
    return labels


def test_segment_old_api_matches_v3_reference():
    # §24.18-1：segment() 旧 API 的 head/skins 与 v3 实现逐像素一致
    labels = _a_canvas_labels()
    box = np.array([45, 35, 115, 85], np.float32)
    frame = np.zeros((160, 160, 3), np.uint8)
    seg = HeadSegmenter(_StubParser(labels), roi_ratio=2.6, dilate_px=8, erode_px=0, temporal_ema=0.0)
    head_new, skins_new = seg.segment(frame, box)

    # v3 参考实现（重构前 segment() 逐行复刻）
    from headswap.segment_head import square_roi
    h, w = frame.shape[:2]
    x0, y0, size = square_roi(frame.shape, box, 2.6)
    head_roi = np.isin(labels, HEAD_CLASSES).astype(np.uint8) * 255
    head_roi = filter_components(head_roi, box - np.array([x0, y0, x0, y0], dtype=np.float32))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    head_roi = cv2.morphologyEx(head_roi, cv2.MORPH_CLOSE, k, iterations=1)
    head_roi = cv2.morphologyEx(head_roi, cv2.MORPH_OPEN, k, iterations=1)
    kd = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17))
    head_roi = cv2.dilate(head_roi, kd)
    ref_mask = np.zeros((h, w), np.uint8)
    ref_mask[y0 : y0 + size, x0 : x0 + size] = head_roi
    assert (head_new == ref_mask).all()
    assert skins_new.shape == (160, 160) and (skins_new > 0).any()


def test_segment_parts_returns_raw_neck_not_head_padded():
    # §24.18-2/3/4：neck 为独立 class14 轮廓——不减 head_pad、无 cloth、顶部形状来自 class14
    labels = _a_canvas_labels()
    box = np.array([45, 35, 115, 85], np.float32)
    frame = np.zeros((160, 160, 3), np.uint8)
    seg = HeadSegmenter(_StubParser(labels), roi_ratio=2.6, dilate_px=8, erode_px=0, temporal_ema=0.0)
    head, skins, neck = seg.segment_parts(frame, box)

    assert neck.shape == (160, 160)
    assert neck[100, 80] > 0                     # 窗口内脖子主体保留（未减 head_pad）
    assert neck[157, 80] == 0                    # cloth 类 16 不进 neck
    # 顶部形状来自 class14（V 形），不是水平线：三列的顶边 y 各不相同
    tops = {int(x): int(np.argmax(neck[:, x] > 0)) for x in (56, 80, 100)}
    assert tops[80] < tops[56] and tops[80] < tops[100]
    assert len(set(tops.values())) == 3


def test_filter_a_neck_picks_center_component():
    labels = np.zeros((160, 160), np.uint8)
    labels[86:107, 60:100] = 14                  # 主脖子（窗口内，脸正下方）
    labels[130:150, 140:158] = 14                # 远处误检脖子（窗口 x/y 之外）
    box = np.array([45, 35, 115, 85], np.float32)
    neck = filter_a_neck_near_primary_face((labels == 14).astype(np.uint8) * 255, box)
    assert neck[95, 80] > 0                      # 保留主脖子
    assert neck[:, 140:].max() == 0              # 剔除远处误检
    assert neck[120:, :].max() == 0              # 窗口以下不含（y1=107 截断）


def test_motion_safe_neck_union_covers_leading_edge():
    # §24.18-6：neck 随头右移时，运动补偿并集不漏前缘
    prev = np.zeros((100, 100), np.uint8)
    prev[60:90, 30:70] = 255
    cur = np.zeros((100, 100), np.uint8)
    cur[60:90, 35:75] = 255
    prev_kps = np.array([[30.0, 50.0], [50.0, 50.0]], np.float32)
    cur_kps = np.array([[35.0, 50.0], [55.0, 50.0]], np.float32)
    safe = motion_safe_neck_union(cur, prev, prev_kps, cur_kps, upward_px=0) > 0
    assert safe[60:90, 70:75].all()              # 前缘被覆盖
    assert safe[60:90, 35:75].all()              # 当前帧本体在保护内


def test_extend_mask_upward_only_grows_upward():
    # §24.18-7：只向上延展，不扩左右
    mask = np.zeros((100, 100), np.uint8)
    mask[50:100, 40:60] = 255
    out = extend_mask_upward(mask, 3)
    assert (out[47:50, 40:60] == 255).all()      # 上移 3px 支撑
    assert out[:47].max() == 0
    assert out[:, :40].max() == 0 and out[:, 60:].max() == 0  # 左右宽度不变


def test_check_neck_mode_mutual_exclusion():
    # §24.18-9：A 脖子保护与 B collar 互斥
    check_neck_mode(True, False)
    check_neck_mode(False, True)
    check_neck_mode(False, False)
    with pytest.raises(ValueError, match="不能同时开启"):
        check_neck_mode(True, True)


def test_residual_never_covers_preserved_neck():
    # §24.18-8：fill_protect 含 A neck 时 residual 不得覆盖 neck（§24.10.2 表达式锁定）
    old_head_safe = np.zeros((80, 80), bool)
    old_head_safe[20:60, 20:60] = True
    alpha = np.zeros((80, 80), np.float32)
    alpha[20:50, 20:60] = 1.0                    # 新头核心只到 y=50
    a_neck = np.zeros((80, 80), bool)
    a_neck[52:70, 30:50] = True                  # 脖子在头下方
    new_core = alpha >= 0.995
    fill_protect = a_neck.copy()
    residual = old_head_safe & (~new_core) & (~fill_protect)
    assert not (residual & a_neck).any()         # neck 绝不进补洞差集
    assert residual[50:52, 20:60].all()          # neck 之外的旧头区域正常清理


def test_region_aware_alpha_side_vs_jaw_feather():
    # §24.18-11：侧脸 4px、下颌 8px；support 外严格 0（§24.18-13）
    wa = np.zeros((160, 160), np.float32)
    wa[40:140, 50:150] = 1.0
    a_box = np.array([50, 30, 150, 130], np.float32)  # jaw_start=98, jaw_full=112
    support = (wa > 0.01).astype(np.uint8)
    dist = cv2.distanceTransform(support, cv2.DIST_L2, 3)  # 3x3 mask 为亚像素近似
    alpha, diag = region_aware_head_alpha(
        wa, a_box, side_feather_px=4.0, jaw_feather_px=8.0,
        jaw_start_ratio=0.68, jaw_full_ratio=0.82,
    )
    assert diag["jaw_start_y"] == pytest.approx(98.0) and diag["jaw_full_y"] == pytest.approx(112.0)
    assert alpha[100, 100] == 1.0                # 内部满 alpha
    assert alpha[wa <= 0.01].max() == 0.0        # mask 外严格 0
    # 侧脸区（row 70，feather=4）：alpha = dist/4
    assert alpha[70, 52] == pytest.approx(float(dist[70, 52]) / 4.0, abs=0.02)
    # jaw 区（row ≥112，feather=8）：alpha = dist/8
    assert alpha[139, 100] == pytest.approx(float(dist[139, 100]) / 8.0, abs=0.02)
    # 同等边界距离下，侧脸羽化（窄）比 jaw（宽）更不透明
    assert alpha[70, 52] > alpha[139, 100]


def test_region_aware_feather_smoothstep_no_horizontal_jump():
    # §24.18-12：feather_map 从侧脸到 jaw 用 smoothstep，相邻行无水平突变
    wa = np.zeros((120, 80), np.float32)
    wa[0:100, :] = 1.0                           # 半平面：每行到下缘距离恒定
    a_box = np.array([0, 0, 80, 100], np.float32)  # jaw_start=68, jaw_full=82
    alpha, _ = region_aware_head_alpha(
        wa, a_box, side_feather_px=4.0, jaw_feather_px=8.0,
        jaw_start_ratio=0.68, jaw_full_ratio=0.82,
    )
    col = alpha[:, 40]
    diffs = np.abs(np.diff(col[60:99]))
    # smoothstep 下相邻行变化 ≤0.12（硬切换会在 jaw_start 处产生 ~0.2+ 的断层）
    assert diffs.max() < 0.15


def test_jaw_neck_gap_metric():
    # §24.19 度量语义：正间隙计入、重叠为 0、无 neck 返回 None
    alpha = np.zeros((200, 200), np.float32)
    alpha[40:100, 50:150] = 1.0
    neck_gap = np.zeros((200, 200), bool)
    neck_gap[110:160, 50:150] = True
    g_mean, g_max = jaw_neck_gap_px(alpha, neck_gap)
    assert g_mean == pytest.approx(11.0) and g_max == pytest.approx(11.0)  # 头底边 99，neck 顶 110
    neck_touch = np.zeros((200, 200), bool)
    neck_touch[95:160, 50:150] = True            # 与 jaw 重叠
    g_mean2, g_max2 = jaw_neck_gap_px(alpha, neck_touch)
    assert g_mean2 == 0.0 and g_max2 == 0.0
    assert jaw_neck_gap_px(alpha, np.zeros((200, 200), bool))[0] is None


def test_v4_configs_outputs_do_not_overlap():
    # §24.18-16：H1/H2/H3 三份配置产物互不覆盖；v4 默认路径禁 collar（§24.11）
    root = _Path(__file__).resolve().parents[1] / "config"
    names = ["headswap.hs-p1-0004.yaml", "headswap.hs-p1-0004-jaw-blend.yaml", "headswap.hs-p1-0004-jaw-color.yaml"]
    files = [root / n for n in names]
    if not all(p.is_file() for p in files):
        pytest.skip("hs-p1-0004 三份配置尚未全部创建")
    finals, silents = set(), set()
    for p in files:
        cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
        comp = cfg["composite"]
        assert comp.get("a_neck_preserve_enabled") is True
        assert comp.get("neck_collar_enabled") in (False, None)
        assert comp.get("neck_color_strength", 0.0) == 0.0 or "jaw-color" in p.name
        finals.add(cfg["video"]["final_name"])
        silents.add(comp["silent_name"])
    assert len(finals) == 3 and len(silents) == 3


# ---------------- 第五轮 Round I（docs §26.13） ----------------

from headswap.composite_head import (
    build_jaw_neck_junction,
    directional_dilate_down,
    junction_wall_component_max_px,
    orphan_neck_top_px,
)


def _junction_scene(size=200):
    """§26 合成场景：B 头（窄）+ A 脖子（宽）+ 下颌软边带 + 旧头安全区。

    face_box=[55,40,145,140]（bh=100）→ jaw_zone 行 [110,170]。
    B alpha：核心行 40..138，行 139..146 为 8px 下颌软边（0.95→0.03 线性）。
    A neck：行 144..190、列 50..150（比 B 头支撑列 58..141 宽 → 左右有尖端）。
    old_head_safe：行 40..160、列 40..160 的旧头大块（含下颌与上颈）。
    """
    face_box = np.array([55, 40, 145, 140], np.float32)
    alpha = np.zeros((size, size), np.float32)
    alpha[40:139, 58:142] = 1.0
    ramp = np.linspace(0.95, 0.03, 8, dtype=np.float32)
    for i, v in enumerate(ramp):
        alpha[139 + i, 58:142] = v
    neck = np.zeros((size, size), bool)
    neck[144:190, 50:150] = True
    old_head_safe = np.zeros((size, size), bool)
    old_head_safe[40:160, 40:160] = True
    return alpha, neck, old_head_safe, face_box


def test_directional_dilate_down_never_grows_upward():
    # §26.13-1：只向下扩，上方行严格保持为 0
    mask = np.zeros((120, 120), np.uint8)
    mask[60:65, 50:70] = 255
    out = directional_dilate_down(mask, down_px=8, side_px=2)
    assert out[:60].max() == False or not out[:60].any()
    assert out[60:73].any()               # 向下扩展生效
    assert out[80:].max() == False or not out[80:].any()  # 不超过 down_px（含核高 3）


def test_directional_dilate_down_side_growth_bounded():
    # §26.13-2：横向扩张不超过 side_px
    mask = np.zeros((120, 120), np.uint8)
    mask[60:65, 50:70] = 255
    out = directional_dilate_down(mask, down_px=8, side_px=2)
    xs = np.nonzero(out.any(axis=0))[0]
    assert xs.min() >= 50 - 2 and xs.max() <= 70 + 2
    # 顶行（dy=0）宽度不变
    assert out[60, 45:50].any() == False and out[60, 70:75].any() == False


def test_junction_neck_midlow_identical_to_original():
    # §26.13-3：neck 中下段（top_band 以下）逐像素保持原 mask
    alpha, neck, old_safe, box = _junction_scene()
    j = build_jaw_neck_junction(alpha, neck, old_safe, box,
                                jaw_underlay_px=10, neck_taper_height_px=16, side_px=2)
    neck_top = j["neck_top"]
    below = np.zeros_like(neck)
    below[neck_top + 16:, :] = True
    npt.assert_array_equal(j["neck_visible"][below], neck[below])


def test_junction_taper_removes_orphan_side_tips():
    # §26.13-4：neck 顶部左右悬空尖端（B 下颌包络外）被去掉，中部保留
    alpha, neck, old_safe, box = _junction_scene()
    j = build_jaw_neck_junction(alpha, neck, old_safe, box,
                                jaw_underlay_px=10, neck_taper_height_px=16, side_px=2)
    top_band_rows = slice(j["neck_top"], j["neck_top"] + 16)
    nv = j["neck_visible"]
    assert nv[top_band_rows, 50:56].any() == False   # 左尖端（envelope 列外）
    assert nv[top_band_rows, 145:150].any() == False # 右尖端
    assert nv[top_band_rows, 70:130].all()           # 中部（下颌正下方）保留
    # 塑形是斜向过渡：顶部窄、向下放宽（左右边缘逐行外扩）
    mid_rows = slice(j["neck_top"] + 18, j["neck_top"] + 30)  # top_band 以下已全宽
    assert nv[mid_rows, 50:150].all()


def test_junction_legitimate_area_protected_by_underlay():
    # §26.13-5：B 下颌软边正下方的 A 接合皮肤进入 jaw_underlay
    alpha, neck, old_safe, box = _junction_scene()
    j = build_jaw_neck_junction(alpha, neck, old_safe, box,
                                jaw_underlay_px=10, neck_taper_height_px=16, side_px=2)
    under = j["jaw_underlay"]
    assert under.any()
    head_support = alpha > 0.02
    jaw_zone = (np.arange(200)[:, None] >= 110) & (np.arange(200)[:, None] <= 170)
    jaw_soft = jaw_zone & head_support & ~(alpha >= 0.995)
    # 下颌软边 ∩ 旧头安全区 必须被 underlay ∪ neck_visible 完全接住（→ §26.13-7）
    protected = j["fill_protect"]
    assert not (jaw_soft & old_safe & ~protected).any()


def test_junction_far_old_face_excluded_from_underlay():
    # §26.13-6：远离 B 下颌的 A 旧脸（下颌区左侧远处）不进 underlay
    alpha, neck, old_safe, box = _junction_scene()
    j = build_jaw_neck_junction(alpha, neck, old_safe, box,
                                jaw_underlay_px=10, neck_taper_height_px=16, side_px=2)
    under = j["jaw_underlay"]
    assert under[112:126, 8:36].sum() == 0     # 旧脸远离 head_near
    assert under[:110, :].sum() == 0           # jaw_zone 外严格没有 underlay


def test_junction_residual_never_covers_jaw_soft():
    # §26.13-7：主路径表达式下 jaw_soft & residual == 0（接合范围内无墙）
    alpha, neck, old_safe, box = _junction_scene()
    j = build_jaw_neck_junction(alpha, neck, old_safe, box,
                                jaw_underlay_px=10, neck_taper_height_px=16, side_px=2)
    new_core = alpha >= 0.995
    residual = old_safe & (~new_core) & (~j["fill_protect"])
    jaw_zone = (np.arange(200)[:, None] >= 110) & (np.arange(200)[:, None] <= 170)
    jaw_soft = jaw_zone & (alpha > 0.02) & (~new_core)
    assert int((jaw_soft & residual).sum()) == 0


def test_junction_empty_neck_degrades_safely():
    # §26.13-9：空 neck 安全退化——不得整帧保护
    alpha, neck, old_safe, box = _junction_scene()
    empty = np.zeros_like(neck)
    j = build_jaw_neck_junction(alpha, empty, old_safe, box)
    assert j["neck_visible"].sum() == 0
    assert j["jaw_underlay"].sum() == 0
    assert j["fill_protect"].sum() == 0        # 不得把整帧当保护区
    assert j["jaw_soft"].any() and j["envelope"].any()  # 诊断量仍可用


def test_junction_underlay_monotonic_no_lateral_blowup():
    # §26.13-10：jaw_underlay_px 8/10/12 单调增长但不横向失控
    alpha, neck, old_safe, box = _junction_scene()
    masks = [
        build_jaw_neck_junction(alpha, neck, old_safe, box,
                                jaw_underlay_px=p, neck_taper_height_px=16, side_px=2)
        for p in (8, 10, 12)
    ]
    for a, b in zip(masks, masks[1:]):
        # 集合单调：小参数的 underlay ⊆ 大参数的 underlay
        assert not (a["jaw_underlay"] & ~b["jaw_underlay"]).any()
    for m in masks:
        under = m["jaw_underlay"]
        xs = np.nonzero(under.any(axis=0))[0]
        # 横向不超出 A neck 本身宽度（underlay 是 neck 向上延展，不是横向膨胀）
        assert xs.min() >= 50 - 1 and xs.max() <= 150 + 1


def test_orphan_metric_catches_side_orphan_blind_to_old_gap_metric():
    # §26.13-11：中部 gap=0、侧边仍悬空的反例——旧 gap 指标测 0，新 orphan 指标必须抓到
    alpha = np.zeros((200, 200), np.float32)
    alpha[40:139, 58:142] = 1.0
    alpha[139:147, 58:142] = np.linspace(0.95, 0.03, 8, dtype=np.float32)[:, None]
    head_support = alpha > 0.02
    neck_visible = np.zeros((200, 200), bool)
    neck_visible[144:190, 58:142] = True   # 主脖子与下颌零间隙
    neck_visible[150:170, 8:26] = True     # 左侧悬空块：上方永远没有 B 头
    envelope = directional_dilate_down(head_support, 16, side_px=2)
    g_mean, g_max = jaw_neck_gap_px(alpha, neck_visible)
    assert g_mean == 0.0 and g_max == 0.0  # 旧指标：中部 60% 列全缝合 → 盲区
    orphan = orphan_neck_top_px(
        neck_visible, head_support, neck_top=144,
        neck_taper_height_px=16, envelope=envelope,
    )
    assert orphan > 0                       # 新指标覆盖左右列，抓到悬空块


def test_junction_wall_component_metric_finds_wall_blob():
    # §26.11 指标 2 语义自检：接合区内的 residual 连通域被度量，区域外不计
    neck_visible = np.zeros((200, 200), bool)
    neck_visible[144:190, 58:142] = True
    head_core = np.zeros((200, 200), bool)
    head_core[40:139, 58:142] = True
    jaw_zone = (np.arange(200)[:, None] >= 110) & (np.arange(200)[:, None] <= 170)
    residual = np.zeros((200, 200), bool)
    residual[150:158, 70:100] = True       # 接合区内 30x8 墙块
    residual[10:40, 10:40] = True          # 远处 residual（不在度量区域）
    val = junction_wall_component_max_px(residual, neck_visible, head_core, jaw_zone)
    assert val == 30 * 8
    empty = np.zeros((200, 200), bool)
    assert junction_wall_component_max_px(empty, neck_visible, head_core, jaw_zone) == 0


def test_v5_configs_outputs_do_not_overlap():
    # §26.13-8/12：v5 三份配置禁 collar、jaw 调色为 0（I3 才允许非 0）、产物互不覆盖；
    # I3 允许写 final（§26.14"最终候选才写 output/final.mp4"），probe 两轮不得碰它
    root = _Path(__file__).resolve().parents[1] / "config"
    names = [
        "headswap.hs-p1-0004-v5-underlay.yaml",
        "headswap.hs-p1-0004-v5-junction.yaml",
        "headswap.hs-p1-0004-v5.yaml",
    ]
    files = [root / n for n in names]
    if not all(p.is_file() for p in files):
        pytest.skip("v5 三份配置尚未全部创建")
    v4_finals = {"final-v4-neck-preserve", "final-v4-jaw-blend"}
    probe_finals, silents, final_writers = set(), set(), []
    for p in files:
        cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
        comp = cfg["composite"]
        assert comp.get("a_neck_preserve_enabled") is True
        assert comp.get("neck_collar_enabled") in (False, None)   # §26.13-8
        assert comp.get("jaw_underlay_enabled") is True
        assert comp.get("jaw_color_strength", 0.0) == 0.0 or p.name.endswith("-v5.yaml")
        silents.add(comp["silent_name"])
        fname = cfg["video"]["final_name"]
        if p.name.endswith("-v5.yaml"):
            final_writers.append(fname)
        else:
            probe_finals.add(fname)
    assert len(silents) == 3                       # silent 中间产物互不覆盖
    assert probe_finals & v4_finals == set()       # probe 轮不得覆盖 v4 产物
    assert len(probe_finals) == 2
    assert final_writers == ["final"]              # 只有 I3 终选写 final.mp4


# ---------------- 第六轮 Round L（docs §28.13） ----------------

from headswap.composite_head import (
    build_argparser as _build_composite_argparser,
    build_junction_corridor,
    build_vertical_junction_bridge,
    corridor_close_fill_protect,
    offline_filter as _offline_filter,
    rigid_from_eyes_nose,
    weighted_similarity,
)


def _bridge_scene(size=200, gap=2, head_bottom=140):
    """下颌—脖子窄缝场景：head 支撑到 head_bottom，neck 从 head_bottom+1+gap 起。

    真实素材中 B 头比 A 脖子宽，old_head_safe 与 head/neck 同宽（不再留侧缝）。"""
    alpha = np.zeros((size, size), np.float32)
    alpha[: head_bottom + 1, 60:140] = 1.0
    nt = head_bottom + 1 + gap
    neck = np.zeros((size, size), bool)
    neck[nt:190, 60:140] = True
    old_head_safe = np.zeros((size, size), bool)
    old_head_safe[40 : nt + 30, 60:140] = True
    yy = np.arange(size)[:, None]
    jaw_zone = (yy >= 100) & (yy <= 180)
    return alpha, neck, old_head_safe, jaw_zone


def test_bridge_fills_legal_gaps_per_column():
    # §28.13-1：1/3/6px 合法 gap 逐列被填满
    for gap in (1, 3, 6):
        alpha, neck, old_safe, jz = _bridge_scene(gap=gap)
        bridge = build_vertical_junction_bridge(alpha, neck, old_safe, jz, max_gap_px=6)
        assert bridge[141 : 141 + gap, 60:140].all()   # 缝隙行全部补上
        assert not bridge[:140, :].any()               # head 区不加
        assert not bridge[147:, :].any()               # neck 区不加


def test_bridge_skips_large_gap_and_true_background():
    # §28.13-2：gap>max 不填；只有 head / 只有 neck 的真实背景列不填
    alpha, neck, old_safe, jz = _bridge_scene(gap=7)
    bridge = build_vertical_junction_bridge(alpha, neck, old_safe, jz, max_gap_px=6)
    assert not bridge[141:148, 60:140].any()           # 7px 缝不填（可能是真背景）
    alpha2, neck2, old2, jz2 = _bridge_scene(gap=2)
    neck2[:, 120:] = False                             # 右侧只有 head
    b2 = build_vertical_junction_bridge(alpha2, neck2, old2, jz2, max_gap_px=6)
    assert not b2[141:143, 120:140].any()
    alpha3, neck3, old3, jz3 = _bridge_scene(gap=2)
    alpha3[:, :80] = 0.0                               # 左侧只有 neck
    b3 = build_vertical_junction_bridge(alpha3, neck3, old3, jz3, max_gap_px=6)
    assert not b3[:, :80].any()


def test_bridge_stays_inside_jaw_zone_and_old_head_safe():
    # §28.13-3：bridge 不超出 jaw_zone / old_head_safe
    alpha, neck, old_safe, jz = _bridge_scene(gap=2)
    old_safe[:, 100:110] = False                       # 挖掉几列旧头安全区
    bridge = build_vertical_junction_bridge(alpha, neck, old_safe, jz, max_gap_px=6)
    assert not bridge[:, 100:110].any()                # old_head_safe 外不填
    yy = np.arange(200)[:, None]
    jz2 = jz & (yy <= 142)                             # jaw_zone 只到缝上沿
    b2 = build_vertical_junction_bridge(alpha, neck, old_safe, jz2, max_gap_px=6)
    assert not b2[143:, :].any()                       # jaw_zone 外不填


def test_central_10x1_residual_eliminated_by_bridge():
    # §28.13-4：中央 10×1 水平 residual 反例被 bridge 消除，走廊硬闸门归零
    alpha, neck, old_safe, jz = _bridge_scene(gap=1)
    underlay = np.zeros_like(neck)                     # underlay 差 1px 没接上（§28.2 反例）
    fill_protect = neck | underlay
    new_core = alpha >= 0.995
    residual = old_safe & (~new_core) & (~fill_protect)
    stripe = np.zeros((200, 200), bool)
    stripe[141, 90:100] = True
    assert residual[stripe].all()                      # 反例成立：10×1 横纹在 residual
    corridor = build_junction_corridor(alpha, neck, old_safe, jz)
    assert corridor[stripe].all()                      # 且落在接合走廊内
    bridge = build_vertical_junction_bridge(alpha, neck, old_safe, jz, max_gap_px=6)
    fp2 = corridor_close_fill_protect(fill_protect, corridor) | bridge
    residual2 = old_safe & (~new_core) & (~fp2)
    assert not (residual2 & corridor).any()            # 修复后走廊内 residual = 0


def test_corridor_gate_catches_1px_horizontal_stripe():
    # §28.13-5：走廊 residual hard gate 能抓到 1px 横纹（不修时必须报警）
    alpha, neck, old_safe, jz = _bridge_scene(gap=2)
    corridor = build_junction_corridor(alpha, neck, old_safe, jz)
    residual = np.zeros((200, 200), bool)
    residual[141, 70:130] = True                       # 1px 横纹
    caught = int((residual & corridor).sum())
    assert caught >= 50                                # 闸门命中横纹主体
    target = (residual & corridor).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(target, connectivity=8)
    widths = [stats[i, cv2.CC_STAT_WIDTH] for i in range(1, count)]
    assert max(widths) >= 40                           # 横向宽度语义可度量


def test_eyes_nose_never_reads_mouth_points():
    # §28.13-6：eyes_nose 不读取 kps[3:5]——嘴点 NaN/随机，结果不变
    b = np.array([[100.0, 100.0], [200.0, 102.0], [150.0, 150.0], [130.0, 180.0], [170.0, 182.0]])
    a = b * 2.0 + np.array([10.0, 20.0])
    ref = rigid_from_eyes_nose(b, a)
    b_nan = b.copy()
    b_nan[3] = [np.nan, np.nan]
    b_nan[4] = [np.inf, -3.0]
    assert rigid_from_eyes_nose(b_nan, a) == ref
    b_rnd = b.copy()
    b_rnd[3] += np.array([50.0, -40.0])
    b_rnd[4] -= np.array([70.0, 25.0])
    assert rigid_from_eyes_nose(b_rnd, a) == ref
    # 加权重心精确对齐（眼中点 0.7 + 鼻尖 0.3）
    m = rebuild(*ref)
    mapped = cv2.transform(b[:3].reshape(-1, 1, 2).astype(np.float32), m).reshape(-1, 2)
    mapped_w = 0.7 * (mapped[0] + mapped[1]) / 2 + 0.3 * mapped[2]
    wgt_a = 0.7 * (a[0] + a[1]) / 2 + 0.3 * a[2]
    npt.assert_allclose(mapped_w, wgt_a, atol=0.05)


def test_weighted_similarity_survives_brow_outlier():
    # §28.13-7：一个眉点异常时仍恢复已知 tx/ty/roll（加权 + MAD 降权）
    rng = np.random.default_rng(7)
    s_true, ang_deg, tx_true, ty_true = 1.73, 6.0, 41.0, -12.0
    ang = np.deg2rad(ang_deg)
    R = np.array([[np.cos(ang), -np.sin(ang)], [np.sin(ang), np.cos(ang)]])
    src = rng.uniform(-50, 50, size=(12, 2))
    dst = src @ R.T * s_true + np.array([tx_true, ty_true]) + rng.normal(0, 0.05, size=(12, 2))
    dst[11] += np.array([18.0, -14.0])                 # 眉点离群
    weights = np.array([1.0] * 4 + [0.7] * 4 + [0.3] * 4)
    sol = weighted_similarity(src, dst, weights)
    assert sol is not None
    s, a_out, tx, ty = sol
    assert s == pytest.approx(s_true, abs=0.02)
    assert np.rad2deg(a_out) == pytest.approx(ang_deg, abs=0.3)
    assert tx == pytest.approx(tx_true, abs=1.0)
    assert ty == pytest.approx(ty_true, abs=1.0)


def test_weighted_similarity_rejects_insufficient_points():
    # §28.13-8：有效锚点不足 → None（调用方回退 eyes_nose）
    pts = np.array([[0.0, 0.0], [10.0, 0.0]])
    assert weighted_similarity(pts, pts, np.ones(2)) is None
    assert weighted_similarity(np.zeros((4, 2)), np.zeros((4, 2)), np.zeros(4)) is None


def test_no_full_affine_shear_path_in_production():
    # §28.13-9：full affine/shear 不可进入生产配置；weighted_similarity 只产 4 自由度
    parser = _build_composite_argparser()
    action = next(a for a in parser._actions if a.dest == "transform_mode")
    assert set(action.choices) == {"eyes", "eyes_nose", "five_point"}
    assert not any("affine" in c or "shear" in c for c in action.choices)
    rng = np.random.default_rng(3)
    src = rng.uniform(-30, 30, size=(8, 2))
    sol = weighted_similarity(src, src + np.array([5.0, 7.0]), np.ones(8))
    m = rebuild(*sol)
    npt.assert_allclose(m[0, 0], m[1, 1], atol=1e-9)   # 无 shear：旋转矩阵结构
    npt.assert_allclose(m[0, 1], -m[1, 0], atol=1e-9)


def test_motion_filter_preserves_1hz_amplitude_zero_lag():
    # §28.13-10：1Hz 正弦经 K1 滤波（Hampel5+smooth5/angle7）幅度保持、lag=0
    n = 180
    raw = [
        (1.0, 0.02 * float(np.sin(i / 9.0)),
         10.0 * float(np.sin(2 * np.pi * i / 30.0)),
         6.0 * float(np.sin(2 * np.pi * i / 30.0 + 0.4)))
        for i in range(n)
    ]
    out = _offline_filter(raw, hampel_window=5, smooth_window=5, scale_mode="const", angle_window=7)
    tx = np.array([p[2] for p in out])
    ref = np.array([p[2] for p in raw])
    gain = tx.std() / ref.std()
    assert 0.85 <= gain <= 1.10                        # 幅度不被吞
    lags = range(-3, 4)
    corrs = [
        float(np.corrcoef(ref[: -abs(l) or None], np.roll(tx, l)[: -abs(l) or None])[0, 1])
        for l in lags
    ]
    assert int(np.argmax(corrs)) == 3                  # lags[3] == 0：零相位


def test_scale_clamp_within_one_percent():
    # §28.13-11：smooth_clamped 的 scale 不超过中位数 ±1%
    rng = np.random.default_rng(11)
    t = np.arange(150)
    scale_series = 1.0 + 0.02 * np.sin(t / 17.0) + rng.normal(0, 0.01, size=len(t))
    raw = [(float(scale_series[i]), 0.0, 400.0, 600.0) for i in t]
    out = _offline_filter(raw, hampel_window=5, smooth_window=5, scale_mode="smooth_clamped")
    s = np.array([p[0] for p in out])
    med = float(np.median(s))
    assert np.abs(s - med).max() <= 0.01 * med + 1e-9
    assert s.std() > 0                                 # clamp 不拍成常量（保留前后摆）


def test_v6_configs_do_not_overwrite_previous_products():
    # §28.13-12：v6 产物命名不覆盖 v4/v5/final
    root = _Path(__file__).resolve().parents[1] / "config"
    names = ["headswap.hs-p1-0004-v6-seam.yaml", "headswap.hs-p1-0004-v6-motion.yaml"]
    files = [root / n for n in names]
    if not all(p.is_file() for p in files):
        pytest.skip("v6 两份配置尚未全部创建")
    taken = {"final", "final-v4-neck-preserve", "final-v4-jaw-blend",
             "final-v5-underlay", "final-v5-junction"}
    finals, silents = set(), set()
    expect = {"final-v6-seam-closed", "final-v6-motion"}
    for p in files:
        cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
        comp = cfg["composite"]
        assert comp.get("jaw_underlay_enabled") is True
        assert comp.get("junction_bridge_max_gap_px", 6) > 0
        assert comp.get("neck_collar_enabled") in (False, None)
        finals.add(cfg["video"]["final_name"])
        silents.add(comp["silent_name"])
    assert finals == expect
    assert not (finals & taken)
    assert len(silents) == 2


# ---------------- 第七轮 Round M（docs §30） ----------------

from headswap.composite_head import (
    audit_seam_metrics,
    build_audit_seam_roi,
    build_jaw_underlay_skin,
    build_required_skin_bridge,
    head_bottom_edge_dist,
    jaw_luminance_gradient,
    wall_texture_energy,
)

WALL2_BGR = np.array([205, 210, 215], np.uint8)
SKIN2_BGR = np.array([110, 150, 190], np.uint8)


def _seam_scene(size=200):
    """§30 接合场景：B 头（含 8px 下颌软边）+ 下颌下皮肤补丁 + A raw neck。

    face_box=[55,40,145,140]（bh=100）；head 支撑行 40..146（139..146 软边）；
    submental 皮肤补丁行 148..156（class1 语义、neck 类之外——§30.2 反例同构）；
    raw_neck 行 164..190。head 底(146)→neck 顶(164) gap=17。
    """
    alpha = np.zeros((size, size), np.float32)
    alpha[40:140, 58:142] = 1.0
    for i, v in enumerate(np.linspace(0.95, 0.03, 8, dtype=np.float32)):
        alpha[139 + i, 58:142] = v
    raw_neck = np.zeros((size, size), bool)
    raw_neck[164:190, 60:140] = True
    submental = np.zeros((size, size), bool)
    submental[148:157, 80:120] = True              # §30.2 反例：head_pad 吞掉的 class1
    face_skin = np.zeros((size, size), bool)
    face_skin[60:147, 58:142] = True               # A 面部 class1（head 底带内亦有皮肤）
    raw_skin = raw_neck | submental | face_skin
    old_head_safe = np.zeros((size, size), bool)
    old_head_safe[40:165, 55:145] = True
    yy = np.arange(size)[:, None]
    jaw_zone = (yy >= 110) & (yy <= 180)
    face_box = np.array([55, 40, 145, 140], np.float32)
    frame_a = np.full((size, size, 3), WALL2_BGR, np.uint8)
    frame_a[raw_skin] = SKIN2_BGR
    return alpha, raw_neck, raw_skin, old_head_safe, jaw_zone, face_box, frame_a, submental


def _wall_fill_sim(frame_a, residual):
    clean = frame_a.copy()
    clean[residual] = WALL2_BGR
    return clean


def test_audit_catches_wall_seam_that_corridor_misses():
    # §30.4 自证循环的单元复现（M0）：v6 修复链（corridor 完备化）下
    # corridor 指标 = 0，但独立 audit 抓到 submental 皮肤被写成墙
    alpha, raw_neck, raw_skin, old_safe, jz, box, frame_a, submental = _seam_scene()
    neck_visible = raw_neck.copy()
    corridor = build_junction_corridor(alpha, neck_visible, old_safe, jz)
    fp_v6 = neck_visible | (corridor & (alpha < 0.995))   # v6 走廊完备化
    residual = old_safe & ~(alpha >= 0.995) & ~fp_v6
    assert int((residual & corridor).sum()) == 0            # 旧指标：全 0（自证）
    assert residual[submental].all()                        # 反例皮肤仍被清成墙
    clean = _wall_fill_sim(frame_a, residual)
    audit_roi = build_audit_seam_roi(alpha, raw_neck, box)
    wall_lab = cv2.cvtColor(WALL2_BGR.reshape(1, 1, 3), cv2.COLOR_BGR2LAB)[0, 0].astype(np.float32)
    am = audit_seam_metrics(clean, frame_a, audit_roi, raw_skin, wall_lab)
    assert am["audit_changed_from_skin"] >= submental.sum()  # 独立 audit 抓到
    assert am["audit_wall_intrusion"] >= submental.sum()
    assert am["audit_horizontal_wall_component_width"] >= 30


def test_skin_bridge_protects_and_audit_zero():
    # §30.5 M1：raw skin bridge + jaw 底带铺垫后，audit 三指标归零
    alpha, raw_neck, raw_skin, old_safe, jz, box, frame_a, submental = _seam_scene()
    neck_visible = raw_neck.copy()
    bridge = build_required_skin_bridge(
        alpha, neck_visible, raw_skin, box, max_vertical_gap=14, no_cap=True
    )
    ju = build_jaw_underlay_skin(alpha, raw_skin, band_px=20)
    assert bridge[submental].all()                          # 反例皮肤被 bridge 接住
    fp = neck_visible | bridge | ju
    residual = old_safe & ~(alpha >= 0.995) & ~fp
    clean = _wall_fill_sim(frame_a, residual)
    clean[bridge | ju] = frame_a[bridge | ju]               # §30.5 硬保险
    audit_roi = build_audit_seam_roi(alpha, raw_neck, box)
    wall_lab = cv2.cvtColor(WALL2_BGR.reshape(1, 1, 3), cv2.COLOR_BGR2LAB)[0, 0].astype(np.float32)
    am = audit_seam_metrics(clean, frame_a, audit_roi, raw_skin, wall_lab)
    assert am["audit_changed_from_skin"] == 0
    assert am["audit_wall_intrusion"] == 0
    assert am["audit_horizontal_wall_component_width"] == 0


def test_bridge_is_skin_gated():
    # §30.5 不变量：span 内只有 raw skin 语义的像素被保留，非 skin（背景）不填
    alpha, raw_neck, raw_skin, old_safe, jz, box, frame_a, _ = _seam_scene()
    raw_skin_no_sub = raw_neck.copy()                       # 无 submental 皮肤
    bridge = build_required_skin_bridge(
        alpha, raw_neck, raw_skin_no_sub, box, max_vertical_gap=20, no_cap=True
    )
    assert not bridge[148:157, 80:120].any()                # 无皮肤语义 → 不填


def test_bridge_connects_through_taper_holes():
    # 主体段连接修复：taper 在列内部留"顶部条+洞+主体"时，洞内 raw skin 不许漏保护
    alpha, raw_neck, raw_skin, old_safe, jz, box, frame_a, _ = _seam_scene()
    neck_visible = raw_neck.copy()
    neck_visible[150:157, 60:140] = True                    # envelope 保留的顶部条
    neck_visible[157:172, 60:140] = False                   # taper 洞 + 主体 172+
    bridge = build_required_skin_bridge(
        alpha, neck_visible, raw_skin, box, max_vertical_gap=30, no_cap=False
    )
    # 洞内的 raw skin 部分（164..171，neck 类）经主体段（172+）span 覆盖；
    # 若取第一个 neck 像素（150），span 只到 153，洞会漏（157..163 非 skin 语义，
    # 按不变量本就不填）
    assert bridge[164:172, 80:120].all()
    assert not bridge[157:164, 80:120].any()                # 非皮肤语义不填


def test_bridge_gap_cap_vs_no_cap():
    # cap 语义：gap=17 > 14 时 cap 跳过；no_cap 仍保留 span 内 raw skin
    alpha, raw_neck, raw_skin, old_safe, jz, box, frame_a, submental = _seam_scene()
    capped = build_required_skin_bridge(alpha, raw_neck, raw_skin, box, max_vertical_gap=14)
    assert not capped[submental].any()                      # 17 > 14：整段跳过
    nc = build_required_skin_bridge(alpha, raw_neck, raw_skin, box, max_vertical_gap=14, no_cap=True)
    assert nc[submental].all()


def test_jaw_underlay_band_protects_head_bottom_skin():
    # §30.6 第 1 部分语义：head 底带内的 raw skin 被铺垫，带外不扩
    alpha, raw_neck, raw_skin, old_safe, jz, box, frame_a, _ = _seam_scene()
    ju = build_jaw_underlay_skin(alpha, raw_skin, band_px=20)
    # head 底边 ≈ 行 146（支撑行 40..146），底带 = 127..146
    assert ju[130:146, 80:120].all()                        # 带内 raw skin 铺垫
    assert not ju[148:, :].any()                            # 带外（向下）不扩
    assert not ju[:, :58].any() and not ju[:, 143:].any()   # head 支撑列外不扩


def test_wall_fill_rejects_textured_background():
    # §30.8 M4：smooth_plane 遇复杂纹理（窗帘）必须显式报错，不得静默出平色块
    frame, old_head_safe, fill_protect, face_box, residual, _ = _wall_scene()
    # 平滑墙应通过（含肤色补丁边界的少量梯度，阈值需高于该基线）
    out, stats, st = fit_wall_fill(
        frame, residual, old_head_safe, fill_protect, face_box, max_texture=15.0
    )
    assert stats["mode"] == "wall_plane"
    # 窗帘：竖条纹 + 噪声，纹理能量高 → ValueError
    curtain = frame.copy()
    xs = np.arange(curtain.shape[1])
    stripes = (np.sin(xs / 4.0) * 25 + 25).astype(np.float32)
    curtain = np.clip(curtain.astype(np.float32) + stripes[None, :, None], 0, 255).astype(np.uint8)
    noise = np.random.default_rng(3).normal(0, 6, curtain.shape[:2])
    curtain = np.clip(curtain.astype(np.float32) + noise[..., None], 0, 255).astype(np.uint8)
    energy = wall_texture_energy(curtain, np.ones(curtain.shape[:2], bool))
    assert energy > 15.0
    with pytest.raises(ValueError, match="纹理能量"):
        fit_wall_fill(curtain, residual, old_head_safe, fill_protect, face_box, max_texture=15.0)


def test_jaw_luminance_gradient_band_only_and_smoothstep():
    # §30.7 D3：只改 band 内 head_rgb，底边权重最强、带顶衰减到 0，不动参考帧
    alpha, raw_neck, raw_skin, old_safe, jz, box, frame_a, _ = _seam_scene()
    head = np.full((200, 200, 3), (200, 195, 190), np.uint8)  # B 下颌偏亮
    ref = raw_skin & (np.arange(200)[:, None] >= 140)         # A 接合皮肤参考
    out, state = jaw_luminance_gradient(
        head.astype(np.float32), alpha, frame_a, ref, box, strength=1.0, band_px=28
    )
    edge = head_bottom_edge_dist(alpha)
    band = (edge >= 0) & (edge <= 28) & (alpha > 0.02)
    # band 内像素被压暗（L 向 A 皮肤靠拢）
    lab_in = cv2.cvtColor(np.clip(out, 0, 255).astype(np.uint8), cv2.COLOR_BGR2LAB)[:, :, 0]
    lab_out0 = cv2.cvtColor(head, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.float32)
    darkened = (lab_out0 - lab_in.astype(np.float32)) > 5
    assert (darkened[band]).mean() > 0.5
    # band 外（头部高处）不动（LAB 往返有 ±1 量化，阈值放宽到 2）
    assert (np.abs(out[(edge > 28)] - head[(edge > 28)].astype(np.float32)).max(axis=-1) < 2).all()
    # 底边（edge≈0）比带顶（edge≈28）压得更暗：smoothstep 单调
    bottom_dark = float((lab_out0 - lab_in.astype(np.float32))[(edge < 4) & band].mean())
    top_dark = float((lab_out0 - lab_in.astype(np.float32))[(edge > 24) & band].mean())
    assert bottom_dark > top_dark
    # EMA：第二帧 delta 向第一帧平滑（变化幅度受限）
    out2, state2 = jaw_luminance_gradient(
        head.astype(np.float32), alpha, frame_a, ref, box, strength=1.0, band_px=28, ema_state=state
    )
    d1 = np.asarray(state["delta"]); d2 = np.asarray(state2["delta"])
    assert np.linalg.norm(d2 - d1) < np.linalg.norm(d1) * 0.2 + 1e-6
