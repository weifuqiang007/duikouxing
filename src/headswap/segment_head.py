"""头部解析分割 worker（运行在 liveportrait 环境：cv2 + numpy + onnxruntime + insightface）。

对输入视频每帧：
1. insightface buffalo_l 检测人脸，锁定"主脸"（与上一帧主脸 IoU 最大的检测框），
   避免把手持身份证上的照片人脸当作目标；
2. 在主脸周围取正方形 ROI 裁剪后送 BiSeNet 解析（512x512）。全帧直接解析会因
   人脸占比过小而输出垃圾类目（实测 hat 类淹没全帧），必须裁剪；
3. 按类目聚合头部 mask（皮肤+五官+耳朵+头发+帽子，排除 neck/cloth），
   只保留与主脸邻近的连通域，去掉远处误检；
4. 形态学 close/open + 时序 EMA 平滑，降低发丝边缘闪烁；
5. 输出 masks/mask_%06d.png（头部 mask）、skins/skin_%06d.png（头外皮肤+脖子，
   供色彩迁移参考）、meta.json（逐帧 bbox/kps/失败标记，合成阶段直接复用）。

类目序（yakhyo face-parsing，CelebAMask-HQ 19 类）与 FaceFusion 一致：
0 bg 1 skin 2 l_brow 3 r_brow 4 l_eye 5 r_eye 6 glasses 7 l_ear 8 r_ear
9 ear_r 10 nose 11 mouth 12 u_lip 13 l_lip 14 neck 15 necklace 16 cloth 17 hair 18 hat
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

HEAD_CLASSES = (1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 17, 18)
NECK_CLASS = 14  # neck（§20.6.2）：不进头部 mask，但 Round G 用于 B neck collar
# 色彩参考：脸外皮肤 + 脖子（脖子在 14 类，不进头部 mask，但颜色可参考）
SKIN_REF_CLASSES = (1, 14)


def rect_iou(a, b) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


class PrimaryFaceTracker:
    """逐帧锁定主脸。首帧取面积最大的检测；之后取与上一帧框 IoU 最高者。"""

    def __init__(self, insightface_root: str, det_size: int = 640) -> None:
        from insightface.app import FaceAnalysis

        self.app = FaceAnalysis(
            name="buffalo_l",
            root=insightface_root,
            allowed_modules=["detection"],
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
        )
        self.app.prepare(ctx_id=0, det_size=(det_size, det_size))
        self.prev_box = None
        self.last_meta = {"detected": False, "score": None, "candidates": 0}

    def track(self, frame: np.ndarray):
        faces = self.app.get(frame)
        if not faces:
            self.last_meta = {"detected": False, "score": None, "candidates": 0}
            return None
        if self.prev_box is None:
            best = max(faces, key=lambda f: float(f.bbox[2] - f.bbox[0]) * float(f.bbox[3] - f.bbox[1]))
        else:
            best = max(faces, key=lambda f: rect_iou(f.bbox, self.prev_box))
        self.prev_box = best.bbox.copy()
        self.last_meta = {
            "detected": True,
            "score": float(getattr(best, "det_score", 0.0)),
            "candidates": int(len(faces)),
        }
        return best.bbox, best.kps


def mask_geometry(mask: np.ndarray) -> dict[str, float | int]:
    """二值 mask 的面积与外接框，供时序轮廓遥测。"""
    support = np.asarray(mask) > 0
    ys, xs = np.nonzero(support)
    if len(xs) == 0:
        return {"area": 0, "bbox_w": 0, "bbox_h": 0, "cx": float("nan"), "cy": float("nan")}
    return {
        "area": int(len(xs)),
        "bbox_w": int(xs.max() - xs.min() + 1),
        "bbox_h": int(ys.max() - ys.min() + 1),
        "cx": float(xs.mean()),
        "cy": float(ys.mean()),
    }


class BiSeNetParser:
    """BiSeNet 脸部解析。parse() 返回与输入同尺寸的类目图（uint8）。"""

    MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    def __init__(self, model_path: str) -> None:
        import onnxruntime as ort

        self.session = ort.InferenceSession(
            model_path, providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
        )

    def parse(self, image_bgr: np.ndarray) -> np.ndarray:
        x = cv2.resize(image_bgr, (512, 512))[:, :, ::-1].astype(np.float32) / 255.0
        x = (x - self.MEAN) / self.STD
        x = np.transpose(x[None], (0, 3, 1, 2))
        logits = self.session.run(None, {"input": x})[0][0]
        labels = logits.argmax(0).astype(np.uint8)
        return cv2.resize(
            labels, (image_bgr.shape[1], image_bgr.shape[0]), interpolation=cv2.INTER_NEAREST
        )


def square_roi(frame_shape, box, ratio: float):
    """以人脸框为中心的正方形 ROI，返回 (x0, y0, size)，自动收回画面内。"""
    h, w = frame_shape[:2]
    bw, bh = float(box[2] - box[0]), float(box[3] - box[1])
    cx = (box[0] + box[2]) / 2.0
    cy = (box[1] + box[3]) / 2.0 - 0.18 * bh  # 头发在上方，中心上移
    size = int(max(bw, bh) * ratio)
    size = min(size, w, h)
    x0 = int(round(min(max(cx - size / 2.0, 0), w - size)))
    y0 = int(round(min(max(cy - size / 2.0, 0), h - size)))
    return x0, y0, size


def filter_components(head_mask: np.ndarray, box) -> np.ndarray:
    """只保留与主脸扩展矩形相交且面积足够的连通域，去掉远处误检。"""
    bw, bh = float(box[2] - box[0]), float(box[3] - box[1])
    ex0 = max(0, int(box[0] - 0.7 * bw))
    ex1 = int(box[2] + 0.7 * bw)
    ey0 = max(0, int(box[1] - 1.0 * bh))  # 头发向上多留
    ey1 = int(box[3] + 0.5 * bh)
    rect_mask = np.zeros(head_mask.shape, dtype=np.uint8)
    rect_mask[ey0:ey1, ex0:ex1] = 255
    keep = cv2.bitwise_and(head_mask, rect_mask)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(keep, connectivity=8)
    if count <= 2:
        return keep
    window = (ex0, ey0, ex1, ey1)
    out = np.zeros_like(keep)
    for i in range(1, count):
        lx, ly, lw, lh, area = stats[i]
        if area < 64:
            continue
        if rect_iou((lx, ly, lx + lw, ly + lh), window) > 0:
            out[labels == i] = 255
    return out


def filter_neck_near_primary_face(neck_mask: np.ndarray, box) -> np.ndarray:
    """只保留主脸正下方窄窗内的 neck（§22.7.1 原样），不允许把肩膀、
    衣服或远处皮肤并入。

    ⚠️ v4 起 deprecated 于默认路径：B neck collar 已被 §24 人工复审否决，
    本函数仅供历史复现。A 侧脖子保护改用 filter_a_neck_near_primary_face。
    """
    h, w = neck_mask.shape[:2]
    bx0, by0, bx1, by1 = [float(v) for v in box]
    bw, bh = bx1 - bx0, by1 - by0

    x0 = max(0, int(bx0 - 0.10 * bw))
    x1 = min(w, int(bx1 + 0.10 * bw))
    y0 = max(0, int(by1 - 0.05 * bh))
    y1 = min(h, int(by1 + 0.20 * bh))

    allowed = np.zeros_like(neck_mask, dtype=np.uint8)
    allowed[y0:y1, x0:x1] = 255
    return cv2.bitwise_and(neck_mask, allowed)


def filter_a_neck_near_primary_face(neck_roi: np.ndarray, box_in_roi) -> np.ndarray:
    """A 视频的 neck mask：class 14 真实轮廓，非水平线（§24.8.2 原样）。

    窗口比 B collar 的 0.20bh 更深（保留到衣领附近），只用于排除远处误检；
    最终顶部/左右轮廓由 class 14 本身决定。保留位于脸正下方、与中心轴
    最近的主要连通域。
    """
    h, w = neck_roi.shape[:2]
    bx0, by0, bx1, by1 = [float(v) for v in box_in_roi]
    bw, bh = bx1 - bx0, by1 - by0

    x0 = max(0, int(bx0 - 0.25 * bw))
    x1 = min(w, int(bx1 + 0.25 * bw))
    y0 = max(0, int(by1 - 0.08 * bh))
    y1 = min(h, int(by1 + 0.45 * bh))

    allowed = np.zeros_like(neck_roi, dtype=np.uint8)
    allowed[y0:y1, x0:x1] = 255
    candidate = cv2.bitwise_and(neck_roi, allowed)

    count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        (candidate > 0).astype(np.uint8), connectivity=8
    )
    if count <= 1:
        return candidate

    face_cx = (bx0 + bx1) * 0.5
    best = None
    best_score = -1e18
    for i in range(1, count):
        x, y, cw, ch, area = stats[i]
        cx, cy = centroids[i]
        if area < 50 or cy < by1 - 0.10 * bh:
            continue
        score = float(area) - 4.0 * abs(float(cx) - face_cx)
        if score > best_score:
            best_score = score
            best = i

    out = np.zeros_like(candidate)
    if best is not None:
        out[labels == best] = 255
    return out


class HeadSegmenter:
    def __init__(
        self,
        parser: BiSeNetParser,
        roi_ratio: float = 2.6,
        dilate_px: int = 8,
        erode_px: int = 2,
        temporal_ema: float = 0.6,
    ) -> None:
        self.parser = parser
        self.roi_ratio = roi_ratio
        self.dilate_px = dilate_px
        self.erode_px = erode_px
        self.ema = temporal_ema
        self._prev_mask = None
        self.last_diag: dict[str, float | int] = {}

    def segment(self, frame: np.ndarray, box):
        """旧 API：返回 (head, skins)。v4 起委托 segment_parts，行为逐位一致。"""
        head, skins, _neck = self.segment_parts(frame, box)
        return head, skins

    def segment_parts(self, frame: np.ndarray, box, return_raw_skin: bool = False):
        """返回 (head, skins, neck[, raw_skin, raw_neck])——A 侧 ROI 解析（§24.8.2/§30.5）。

        head/skins 与 v3 segment() 逐位一致（同操作同顺序）；neck 为独立
        class 14 轮廓 mask（不减 head_pad、不按水平线裁、不含 cloth），
        专供第四轮 A 脖子保护使用。

        return_raw_skin=True 时额外返回（第七轮 §30.5）：
        - raw_skin：class 1（face skin）∪ class 14（neck），**不减 head_pad、
          不做连通域过滤、不含 cloth/背景**——下颌下/上颈处被 head_pad 或
          组件过滤吞掉的人体像素在此保留，供 skin bridge 与独立验收使用；
        - raw_neck：class 14 原始轮廓（无组件过滤），供 audit ROI 独立取
          neck 顶部（不读取 repair 侧的 neck_visible）。
        """
        h, w = frame.shape[:2]
        x0, y0, size = square_roi(frame.shape, box, self.roi_ratio)
        roi = frame[y0 : y0 + size, x0 : x0 + size]
        labels = self.parser.parse(roi)

        head_roi = np.isin(labels, HEAD_CLASSES).astype(np.uint8) * 255
        head_roi = filter_components(head_roi, box - np.array([x0, y0, x0, y0], dtype=np.float32))
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        head_roi = cv2.morphologyEx(head_roi, cv2.MORPH_CLOSE, k, iterations=1)
        head_roi = cv2.morphologyEx(head_roi, cv2.MORPH_OPEN, k, iterations=1)
        if self.erode_px > 0:
            ke = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (self.erode_px * 2 + 1, self.erode_px * 2 + 1)
            )
            head_roi = cv2.erode(head_roi, ke)
        if self.dilate_px > 0:
            kd = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (self.dilate_px * 2 + 1, self.dilate_px * 2 + 1)
            )
            head_roi = cv2.dilate(head_roi, kd)

        mask = np.zeros((h, w), dtype=np.uint8)
        mask[y0 : y0 + size, x0 : x0 + size] = head_roi

        if self._prev_mask is not None and self.ema > 0:
            blended = (
                self.ema * self._prev_mask.astype(np.float32)
                + (1 - self.ema) * mask.astype(np.float32)
            )
            mask = (blended >= 127).astype(np.uint8) * 255
        self._prev_mask = mask.copy()
        skins = self._build_skins(labels, x0, y0, size, head_roi, h, w)

        # A neck：class 14 真实轮廓（§24.8.2），close 后直接贴回全画布。
        # 不减 head_pad、不水平裁切、无 EMA——时序安全在 composite 侧
        # 用 motion_safe_neck_union 处理（§24.9）。
        neck_roi = (labels == NECK_CLASS).astype(np.uint8) * 255
        neck_roi = filter_a_neck_near_primary_face(
            neck_roi, box - np.array([x0, y0, x0, y0], dtype=np.float32)
        )
        neck_roi = cv2.morphologyEx(
            neck_roi, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        )
        neck = np.zeros((h, w), dtype=np.uint8)
        neck[y0 : y0 + size, x0 : x0 + size] = neck_roi

        if not return_raw_skin:
            return mask, skins, neck

        raw_skin_roi = np.isin(labels, (1, NECK_CLASS)).astype(np.uint8) * 255
        raw_skin = np.zeros((h, w), dtype=np.uint8)
        raw_skin[y0 : y0 + size, x0 : x0 + size] = raw_skin_roi
        raw_neck_roi = (labels == NECK_CLASS).astype(np.uint8) * 255
        raw_neck = np.zeros((h, w), dtype=np.uint8)
        raw_neck[y0 : y0 + size, x0 : x0 + size] = raw_neck_roi
        return mask, skins, neck, raw_skin, raw_neck

    def segment_full(self, frame: np.ndarray, box):
        """全画布解析（不做方形 ROI 裁剪）。

        B 的再演画布（约 470x679）比 2.4 倍脸高的方形 ROI 还小，square_roi
        会被 min(w,h) 钳制，纵向盖不住整张脸——解析在 ROI 下缘截断，mask
        缺下颌，warp 后正好缺失嘴部区域（实测嘴部贴成底板）。小画布必须
        全图解析；画布接近模型原生 512，效果反而更稳。
        """
        h, w = frame.shape[:2]
        head, _neck, _labels = self.segment_full_parts(frame, box)
        return head, np.zeros((h, w), dtype=np.uint8)

    def _postprocess_mask(self, mask: np.ndarray) -> np.ndarray:
        """close/open + erode/dilate——v2 segment_full 头部后处理，逐位保留。"""
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k, iterations=1)
        self.last_diag.update({f"morph_{k}": v for k, v in mask_geometry(mask).items()})
        if self.erode_px > 0:
            ke = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (self.erode_px * 2 + 1, self.erode_px * 2 + 1)
            )
            mask = cv2.erode(mask, ke)
        self.last_diag.update({f"erode_{k}": v for k, v in mask_geometry(mask).items()})
        if self.dilate_px > 0:
            kd = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (self.dilate_px * 2 + 1, self.dilate_px * 2 + 1)
            )
            mask = cv2.dilate(mask, kd)
        self.last_diag.update({f"post_{k}": v for k, v in mask_geometry(mask).items()})
        return mask

    def segment_full_parts(self, frame: np.ndarray, box):
        """全画布解析，返回 (head, neck, labels)（§20.6.2/§22.7.1）。

        head 路径与 v2 segment_full 逐位一致（含 EMA），由单测
        test_segment_full_parts_head_matches_v2 保证；neck 只保留主脸正下方
        小块（filter_neck_near_primary_face）+ close，不加 EMA——collar 的
        时序稳定由 A 画布侧的几何窗口 + 纵向 ramp 天然提供。
        """
        labels = self.parser.parse(frame)

        head = np.isin(labels, HEAD_CLASSES).astype(np.uint8) * 255
        self.last_diag = {f"parser_raw_{k}": v for k, v in mask_geometry(head).items()}
        head = filter_components(head, box)
        self.last_diag.update({f"component_{k}": v for k, v in mask_geometry(head).items()})
        head = self._postprocess_mask(head)
        if self._prev_mask is not None and self.ema > 0:
            blended = (
                self.ema * self._prev_mask.astype(np.float32)
                + (1 - self.ema) * head.astype(np.float32)
            )
            head = (blended >= 127).astype(np.uint8) * 255
        self._prev_mask = head.copy()
        self.last_diag.update({f"ema_{k}": v for k, v in mask_geometry(head).items()})
        self.last_diag["ema_weight"] = float(self.ema)

        neck = (labels == NECK_CLASS).astype(np.uint8) * 255
        neck = filter_neck_near_primary_face(neck, box)
        neck = cv2.morphologyEx(
            neck, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        )
        return head, neck, labels

    @staticmethod
    def _build_skins(labels, x0, y0, size, head_roi, h, w):
        """头外皮肤+脖子参考 mask（供色彩迁移与补洞保护）。"""
        skin_roi = np.isin(labels, SKIN_REF_CLASSES).astype(np.uint8) * 255
        kd = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))
        head_pad = cv2.dilate(head_roi, kd)
        skin_roi = cv2.subtract(skin_roi, head_pad)  # 头部周边一圈不算参考
        skins = np.zeros((h, w), dtype=np.uint8)
        skins[y0 : y0 + size, x0 : x0 + size] = skin_roi
        return skins



def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="主脸锁定的头部解析分割 worker")
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--output-dir", required=True, type=Path)
    ap.add_argument("--bisenet", required=True, type=Path)
    ap.add_argument("--insightface-root", required=True, type=Path)
    ap.add_argument("--roi-ratio", type=float, default=2.6)
    ap.add_argument("--dilate-px", type=int, default=8)
    ap.add_argument("--erode-px", type=int, default=2)
    ap.add_argument("--temporal-ema", type=float, default=0.6)
    ap.add_argument("--det-size", type=int, default=640)
    ap.add_argument("--max-fail-ratio", type=float, default=0.05)
    ap.add_argument("--output-raw-skins", action="store_true",
                    help="额外输出 raw_skins/（class1∪14 不减 head_pad）与 raw_necks/（class14 无组件过滤），"
                         "供第七轮 skin bridge 与独立验收使用（§30.5/§30.6）")
    args = ap.parse_args(argv)

    if not args.video.is_file():
        print(f"ERROR: video not found: {args.video}", file=sys.stderr)
        return 2

    masks_dir = args.output_dir / "masks"
    skins_dir = args.output_dir / "skins"
    necks_dir = args.output_dir / "necks"
    raw_skins_dir = args.output_dir / "raw_skins"
    raw_necks_dir = args.output_dir / "raw_necks"
    masks_dir.mkdir(parents=True, exist_ok=True)
    skins_dir.mkdir(parents=True, exist_ok=True)
    necks_dir.mkdir(parents=True, exist_ok=True)
    if args.output_raw_skins:
        raw_skins_dir.mkdir(parents=True, exist_ok=True)
        raw_necks_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        print(f"ERROR: cannot open video: {args.video}", file=sys.stderr)
        return 2
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    tracker = PrimaryFaceTracker(str(args.insightface_root), det_size=args.det_size)
    parser = BiSeNetParser(str(args.bisenet))
    segmenter = HeadSegmenter(
        parser,
        roi_ratio=args.roi_ratio,
        dilate_px=args.dilate_px,
        erode_px=args.erode_px,
        temporal_ema=args.temporal_ema,
    )

    frames_meta = []
    index = 0
    fails = 0
    neck_fails = 0
    neck_px_total = 0
    prev_result = None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        tracked = tracker.track(frame)
        if tracked is None:
            fails += 1
            if prev_result is None:
                print("ERROR: 首帧即检测不到人脸，无法继续", file=sys.stderr)
                return 3
            mask, skins, neck, box, kps = prev_result
            neck_fails += 1
            if args.output_raw_skins:
                raw_skin, raw_neck = prev_raw
        else:
            box, kps = tracked
            # 检测失败帧 head/skins/neck 三者一起沿用上一帧（§24.8.2 关键限制）
            if args.output_raw_skins:
                mask, skins, neck, raw_skin, raw_neck = segmenter.segment_parts(
                    frame, box, return_raw_skin=True
                )
                prev_raw = (raw_skin, raw_neck)
            else:
                mask, skins, neck = segmenter.segment_parts(frame, box)
            prev_result = (mask, skins, neck, box, kps)
        neck_px_total += int((neck > 0).sum())
        frames_meta.append(
            {
                "i": index,
                "ok": tracked is not None,
                "bbox": [float(v) for v in box],
                "kps": [[float(x), float(y)] for x, y in kps],
            }
        )
        cv2.imwrite(str(masks_dir / f"mask_{index:06d}.png"), mask)
        cv2.imwrite(str(skins_dir / f"skin_{index:06d}.png"), skins)
        cv2.imwrite(str(necks_dir / f"neck_{index:06d}.png"), neck)
        if args.output_raw_skins:
            cv2.imwrite(str(raw_skins_dir / f"raw_skin_{index:06d}.png"), raw_skin)
            cv2.imwrite(str(raw_necks_dir / f"raw_neck_{index:06d}.png"), raw_neck)
        if index % 100 == 0:
            print(f"segmented frame {index}", flush=True)
        index += 1
    cap.release()

    if index == 0:
        print("ERROR: empty video", file=sys.stderr)
        return 3
    if fails / index > args.max_fail_ratio:
        print(
            f"ERROR: 检测失败帧占比 {fails / index:.3f} 超过 {args.max_fail_ratio}",
            file=sys.stderr,
        )
        return 4

    meta = {
        "video": str(args.video),
        "frames": index,
        "fps": float(fps),
        "width": width,
        "height": height,
        "fail_frames": fails,
        "neck_masks": True,
        "raw_skin_masks": bool(args.output_raw_skins),
        "neck_fail_frames": neck_fails,
        "neck_px_mean": int(neck_px_total / max(index, 1)),
        "params": {
            "roi_ratio": args.roi_ratio,
            "dilate_px": args.dilate_px,
            "erode_px": args.erode_px,
            "temporal_ema": args.temporal_ema,
        },
        "frame_meta": frames_meta,
    }
    (args.output_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )
    print(f"OK: {index} frames, {fails} fallback, fps={fps}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
