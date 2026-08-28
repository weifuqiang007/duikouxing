from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np


DEFAULT_IMAGE = Path(r"G:\duikouxing\samples\sfztest.jpg")
DEFAULT_OUTPUT_DIR = Path(r"G:\duikouxing\tests\id_card_demo_outputs")
MAX_DISPLAY_WIDTH = 1320
MAX_DISPLAY_HEIGHT = 820
RECTIFIED_SIZE = (856, 540)


Point = tuple[float, float]
Quad = list[Point]


@dataclass(frozen=True)
class QuadCandidate:
    points: Quad
    score: float
    source: str
    reason: str


def read_image_unicode(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"无法读取图片: {path}")
    return image


def write_image_unicode(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buffer = cv2.imencode(path.suffix or ".png", image)
    if not ok:
        raise RuntimeError(f"无法编码图片: {path}")
    buffer.tofile(str(path))


def scale_to_fit(width: int, height: int, max_width: int, max_height: int) -> float:
    return min(max_width / width, max_height / height, 1.0)


def display_image(image: np.ndarray, scale: float) -> np.ndarray:
    if scale == 1.0:
        return image.copy()
    width = max(1, int(round(image.shape[1] * scale)))
    height = max(1, int(round(image.shape[0] * scale)))
    return cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)


def denormalize_points(points: Quad, width: int, height: int) -> np.ndarray:
    return np.array(
        [[x * (width - 1), y * (height - 1)] for x, y in points],
        dtype=np.float32,
    )


def normalize_points(points: np.ndarray, width: int, height: int) -> Quad:
    return [
        (
            round(float(x) / max(width - 1, 1), 6),
            round(float(y) / max(height - 1, 1), 6),
        )
        for x, y in points
    ]


def order_quad_points(points: np.ndarray) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float32).reshape(4, 2)
    sums = pts.sum(axis=1)
    diffs = np.diff(pts, axis=1).reshape(-1)
    top_left = pts[np.argmin(sums)]
    bottom_right = pts[np.argmax(sums)]
    top_right = pts[np.argmin(diffs)]
    bottom_left = pts[np.argmax(diffs)]
    return np.array([top_left, top_right, bottom_right, bottom_left], dtype=np.float32)


def quad_area(points: np.ndarray) -> float:
    return abs(float(cv2.contourArea(points.astype(np.float32))))


def validate_quad_px(points: np.ndarray) -> bool:
    if points.shape != (4, 2):
        return False
    if quad_area(points) < 10:
        return False
    return bool(cv2.isContourConvex(points.astype(np.float32)))


def padded_roi_from_quad(
    quad_px: np.ndarray,
    image_width: int,
    image_height: int,
    padding_ratio: float,
) -> tuple[int, int, int, int]:
    x, y, w, h = cv2.boundingRect(quad_px.astype(np.int32))
    pad = int(max(w, h) * padding_ratio)
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(image_width, x + w + pad)
    y1 = min(image_height, y + h + pad)
    return x0, y0, x1, y1


def polygon_iou(a: np.ndarray, b: np.ndarray, width: int, height: int) -> float:
    ma = np.zeros((height, width), dtype=np.uint8)
    mb = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(ma, [a.astype(np.int32)], 255)
    cv2.fillPoly(mb, [b.astype(np.int32)], 255)
    inter = np.logical_and(ma > 0, mb > 0).sum()
    union = np.logical_or(ma > 0, mb > 0).sum()
    return float(inter / union) if union else 0.0


def id_card_aspect_score(quad_px: np.ndarray, expected: float, tolerance: float) -> float:
    tl, tr, br, bl = quad_px
    top = np.linalg.norm(tr - tl)
    bottom = np.linalg.norm(br - bl)
    left = np.linalg.norm(bl - tl)
    right = np.linalg.norm(br - tr)
    width = max((top + bottom) / 2.0, 1.0)
    height = max((left + right) / 2.0, 1.0)
    ratio = width / height
    error = abs(ratio - expected) / max(tolerance, 1e-6)
    return float(np.clip(1.0 - error, 0.0, 1.0))


def average_edge_strength_on_quad(image_bgr: np.ndarray, quad_px: np.ndarray) -> float:
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(gray, 40, 120)
    samples: list[float] = []
    pts = quad_px.astype(np.float32)
    for index in range(4):
        p0 = pts[index]
        p1 = pts[(index + 1) % 4]
        for t in np.linspace(0, 1, 120):
            p = p0 * (1 - t) + p1 * t
            x = int(round(p[0]))
            y = int(round(p[1]))
            if 0 <= x < edges.shape[1] and 0 <= y < edges.shape[0]:
                samples.append(float(edges[y, x]) / 255.0)
    return float(np.mean(samples)) if samples else 0.0


def score_quad_candidate(
    image_bgr: np.ndarray,
    quad_px: np.ndarray,
    manual_quad_px: np.ndarray,
    options: dict[str, Any],
) -> float:
    height, width = image_bgr.shape[:2]
    iou = polygon_iou(quad_px, manual_quad_px, width, height)
    edge_strength = average_edge_strength_on_quad(image_bgr, quad_px)
    aspect = id_card_aspect_score(
        quad_px,
        float(options.get("expected_aspect_ratio", 1.585)),
        float(options.get("aspect_ratio_tolerance", 0.45)),
    )
    convex = 1.0 if cv2.isContourConvex(quad_px.astype(np.float32)) else 0.0
    area_ratio = min(
        quad_area(quad_px) / max(quad_area(manual_quad_px), 1.0),
        quad_area(manual_quad_px) / max(quad_area(quad_px), 1.0),
    )
    return float(
        0.34 * iou
        + 0.24 * edge_strength
        + 0.18 * aspect
        + 0.14 * area_ratio
        + 0.10 * convex
    )


def find_edge_candidates(
    image_bgr: np.ndarray,
    manual_quad_px: np.ndarray,
    options: dict[str, Any],
) -> list[QuadCandidate]:
    height, width = image_bgr.shape[:2]
    x0, y0, x1, y1 = padded_roi_from_quad(
        manual_quad_px,
        width,
        height,
        float(options.get("padding_ratio", 0.16)),
    )
    roi = image_bgr[y0:y1, x0:x1]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(
        gray,
        int(options.get("canny_low", 35)),
        int(options.get("canny_high", 125)),
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    manual_area = quad_area(manual_quad_px)
    candidates: list[QuadCandidate] = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < manual_area * float(options.get("min_area_ratio", 0.28)):
            continue

        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.025 * perimeter, True)
        if len(approx) == 4:
            pts = approx.reshape(4, 2).astype(np.float32)
        else:
            pts = cv2.boxPoints(cv2.minAreaRect(contour)).astype(np.float32)

        pts[:, 0] += x0
        pts[:, 1] += y0
        pts = order_quad_points(pts)
        if not validate_quad_px(pts):
            continue

        candidates.append(
            QuadCandidate(
                points=normalize_points(pts, width, height),
                score=score_quad_candidate(image_bgr, pts, manual_quad_px, options),
                source="edge",
                reason="Canny 边缘 + 轮廓四边形拟合",
            )
        )

    return sorted(candidates, key=lambda candidate: candidate.score, reverse=True)


def find_color_candidate(
    image_bgr: np.ndarray,
    manual_quad_px: np.ndarray,
    options: dict[str, Any],
) -> QuadCandidate | None:
    height, width = image_bgr.shape[:2]
    x0, y0, x1, y1 = padded_roi_from_quad(
        manual_quad_px,
        width,
        height,
        float(options.get("padding_ratio", 0.16)),
    )
    roi = image_bgr[y0:y1, x0:x1]
    lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB).astype(np.float32)

    local_manual = manual_quad_px.copy().astype(np.float32)
    local_manual[:, 0] -= x0
    local_manual[:, 1] -= y0
    local_mask = np.zeros(roi.shape[:2], dtype=np.uint8)
    cv2.fillPoly(local_mask, [local_manual.astype(np.int32)], 255)

    mx, my, mw, mh = cv2.boundingRect(local_manual.astype(np.int32))
    cx0 = max(0, mx + int(mw * 0.38))
    cy0 = max(0, my + int(mh * 0.38))
    cx1 = min(roi.shape[1], mx + int(mw * 0.62))
    cy1 = min(roi.shape[0], my + int(mh * 0.62))
    center = lab[cy0:cy1, cx0:cx1]
    if center.size == 0:
        return None

    reference = np.median(center.reshape(-1, 3), axis=0)
    distance = np.linalg.norm(lab - reference[None, None, :], axis=2)
    inside_distance = distance[local_mask > 0]
    if inside_distance.size < 120:
        return None

    threshold = max(18.0, float(np.percentile(inside_distance, 82)) * 1.45)
    card_like = np.where(distance <= threshold, 255, 0).astype(np.uint8)
    search_mask = cv2.dilate(local_mask, np.ones((11, 11), np.uint8), iterations=1)
    card_like = cv2.bitwise_and(card_like, search_mask)

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (11, 11))
    card_like = cv2.morphologyEx(card_like, cv2.MORPH_CLOSE, kernel, iterations=2)
    card_like = cv2.morphologyEx(card_like, cv2.MORPH_OPEN, kernel, iterations=1)

    contours, _ = cv2.findContours(card_like, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < quad_area(local_manual) * 0.32:
        return None

    pts = cv2.boxPoints(cv2.minAreaRect(contour)).astype(np.float32)
    pts[:, 0] += x0
    pts[:, 1] += y0
    pts = order_quad_points(pts)
    if not validate_quad_px(pts):
        return None

    return QuadCandidate(
        points=normalize_points(pts, width, height),
        score=score_quad_candidate(image_bgr, pts, manual_quad_px, options),
        source="color",
        reason="LAB 色差分割 + 最小外接四边形",
    )


def propose_card_quad(
    image_bgr: np.ndarray,
    manual_quad: Quad,
    options: dict[str, Any],
) -> tuple[QuadCandidate, list[QuadCandidate]]:
    height, width = image_bgr.shape[:2]
    manual_px = denormalize_points(manual_quad, width, height)
    manual_px = order_quad_points(manual_px)
    manual_quad = normalize_points(manual_px, width, height)

    candidates = [
        QuadCandidate(
            points=manual_quad,
            score=0.55,
            source="manual",
            reason="用户手工标记结果",
        )
    ]
    candidates.extend(find_edge_candidates(image_bgr, manual_px, options))
    color_candidate = find_color_candidate(image_bgr, manual_px, options)
    if color_candidate:
        candidates.append(color_candidate)

    candidates = sorted(candidates, key=lambda candidate: candidate.score, reverse=True)
    return candidates[0], candidates


def rectify_card_image(
    image_bgr: np.ndarray,
    quad: Quad,
    output_size: tuple[int, int] = RECTIFIED_SIZE,
) -> np.ndarray:
    out_w, out_h = output_size
    height, width = image_bgr.shape[:2]
    src = order_quad_points(denormalize_points(quad, width, height))
    dst = np.array(
        [
            [0, 0],
            [out_w - 1, 0],
            [out_w - 1, out_h - 1],
            [0, out_h - 1],
        ],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(
        image_bgr,
        matrix,
        (out_w, out_h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def draw_quad(
    image_bgr: np.ndarray,
    quad: Quad,
    *,
    color: tuple[int, int, int],
    label: str,
) -> np.ndarray:
    output = image_bgr.copy()
    height, width = output.shape[:2]
    pts = denormalize_points(quad, width, height).astype(np.int32)
    cv2.polylines(output, [pts], True, color, 4, cv2.LINE_AA)
    for index, (x, y) in enumerate(pts):
        cv2.circle(output, (int(x), int(y)), 9, color, -1, cv2.LINE_AA)
        cv2.putText(
            output,
            str(index + 1),
            (int(x) + 10, int(y) - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
    cv2.putText(
        output,
        label,
        (24, 42),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        color,
        3,
        cv2.LINE_AA,
    )
    return output


def panel_label(image_bgr: np.ndarray, label: str) -> np.ndarray:
    output = image_bgr.copy()
    cv2.rectangle(output, (0, 0), (output.shape[1], 52), (20, 20, 20), -1)
    cv2.putText(
        output,
        label,
        (18, 35),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.85,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return output


def make_review_image(
    source_bgr: np.ndarray,
    manual: QuadCandidate,
    auto: QuadCandidate,
) -> np.ndarray:
    left = draw_quad(
        source_bgr,
        manual.points,
        color=(0, 220, 255),
        label=f"Manual score={manual.score:.2f}",
    )
    middle = draw_quad(
        source_bgr,
        auto.points,
        color=(80, 255, 80),
        label=f"Auto/{auto.source} score={auto.score:.2f}",
    )
    rectified = rectify_card_image(source_bgr, auto.points)

    panel_h = 430
    left_scale = panel_h / left.shape[0]
    mid_scale = panel_h / middle.shape[0]
    left = cv2.resize(left, (int(left.shape[1] * left_scale), panel_h))
    middle = cv2.resize(middle, (int(middle.shape[1] * mid_scale), panel_h))
    rectified = cv2.resize(rectified, (680, panel_h), interpolation=cv2.INTER_AREA)

    left = panel_label(left, "1: use manual")
    middle = panel_label(middle, f"2: use auto ({auto.reason})")
    rectified = panel_label(rectified, "preview: rectified clean card")
    return cv2.hconcat([left, middle, rectified])


class SourceCardMarkerApp:
    def __init__(
        self,
        image_path: Path,
        output_dir: Path,
        detection_options: dict[str, Any],
    ) -> None:
        import tkinter as tk

        self.tk = tk
        self.image_path = image_path
        self.output_dir = output_dir
        self.options = detection_options
        self.image = read_image_unicode(image_path)
        self.scale = scale_to_fit(
            self.image.shape[1],
            self.image.shape[0],
            MAX_DISPLAY_WIDTH,
            MAX_DISPLAY_HEIGHT,
        )
        self.points_display: list[tuple[int, int]] = []
        self.manual_candidate: QuadCandidate | None = None
        self.auto_candidate: QuadCandidate | None = None
        self.selected_candidate: QuadCandidate | None = None
        self.canvas_items: list[int] = []
        self.photo: Any = None
        self.mode = "mark"

        self.root = tk.Tk()
        self.root.title("身份证图片区域 demo - 粗标四角后自动找边")
        self.canvas = tk.Canvas(self.root, highlightthickness=0)
        self.canvas.pack()

        bar = tk.Frame(self.root)
        bar.pack(fill="x")
        self.status = tk.Label(
            bar,
            text="左键依次点击身份证四角：左上、右上、右下、左下。Enter 自动计算。",
            anchor="w",
        )
        self.status.pack(side="left", padx=8, pady=6)
        tk.Button(bar, text="自动计算 (Enter)", command=self.compute_candidates).pack(
            side="right", padx=6, pady=4
        )
        tk.Button(bar, text="重标 (R)", command=self.reset_marking).pack(
            side="right", pady=4
        )
        tk.Button(bar, text="保存 (S)", command=self.save_selected).pack(
            side="right", pady=4
        )

        self.canvas.bind("<ButtonPress-1>", self.on_left_click)
        self.canvas.bind("<ButtonPress-3>", self.on_right_click)
        self.root.bind("<Return>", lambda _event: self.compute_candidates())
        self.root.bind("1", lambda _event: self.choose_manual())
        self.root.bind("2", lambda _event: self.choose_auto())
        self.root.bind("r", lambda _event: self.reset_marking())
        self.root.bind("R", lambda _event: self.reset_marking())
        self.root.bind("s", lambda _event: self.save_selected())
        self.root.bind("S", lambda _event: self.save_selected())
        self.root.bind("<Escape>", lambda _event: self.root.destroy())
        self.show_marking_image()

    def run(self) -> None:
        self.root.mainloop()

    def set_canvas_image(self, image_bgr: np.ndarray) -> None:
        temp_path = self.output_dir / "_tk_preview.png"
        write_image_unicode(temp_path, image_bgr)
        self.photo = self.tk.PhotoImage(file=str(temp_path))
        self.canvas.config(width=image_bgr.shape[1], height=image_bgr.shape[0])
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")

    def show_marking_image(self) -> None:
        self.mode = "mark"
        self.set_canvas_image(display_image(self.image, self.scale))
        self.redraw_points()

    def redraw_points(self) -> None:
        for item in self.canvas_items:
            self.canvas.delete(item)
        self.canvas_items.clear()
        if self.mode != "mark":
            return
        for index in range(1, len(self.points_display)):
            x0, y0 = self.points_display[index - 1]
            x1, y1 = self.points_display[index]
            self.canvas_items.append(
                self.canvas.create_line(x0, y0, x1, y1, fill="#00ff80", width=3)
            )
        if len(self.points_display) == 4:
            x0, y0 = self.points_display[-1]
            x1, y1 = self.points_display[0]
            self.canvas_items.append(
                self.canvas.create_line(x0, y0, x1, y1, fill="#00ff80", width=3)
            )
        for index, (x, y) in enumerate(self.points_display):
            self.canvas_items.append(
                self.canvas.create_oval(
                    x - 6, y - 6, x + 6, y + 6, fill="#ffdd00", outline="white"
                )
            )
            self.canvas_items.append(
                self.canvas.create_text(
                    x + 14,
                    y - 12,
                    text=str(index + 1),
                    fill="white",
                    font=("Arial", 12, "bold"),
                )
            )

    def on_left_click(self, event: Any) -> None:
        if self.mode != "mark":
            return
        if len(self.points_display) >= 4:
            self.points_display.clear()
        self.points_display.append((event.x, event.y))
        self.status.config(text=f"已标 {len(self.points_display)}/4 个点。右键撤销，Enter 自动计算。")
        self.redraw_points()

    def on_right_click(self, _event: Any) -> None:
        if self.mode != "mark":
            return
        if self.points_display:
            self.points_display.pop()
        self.status.config(text=f"已标 {len(self.points_display)}/4 个点。")
        self.redraw_points()

    def current_manual_quad(self) -> Quad:
        height, width = self.image.shape[:2]
        pts = np.array(
            [[x / self.scale, y / self.scale] for x, y in self.points_display],
            dtype=np.float32,
        )
        pts = order_quad_points(pts)
        return normalize_points(pts, width, height)

    def compute_candidates(self) -> None:
        if len(self.points_display) != 4:
            self.status.config(text="需要刚好 4 个点：左上、右上、右下、左下。")
            return
        manual_quad = self.current_manual_quad()
        best, candidates = propose_card_quad(self.image, manual_quad, self.options)
        manual = next(candidate for candidate in candidates if candidate.source == "manual")
        self.manual_candidate = manual
        self.auto_candidate = best if best.source != "manual" else candidates[1] if len(candidates) > 1 else best
        manual_only = bool(self.options.get("manual_only", False))
        self.selected_candidate = self.manual_candidate if manual_only else None
        review = make_review_image(self.image, self.manual_candidate, self.auto_candidate)
        review_scale = scale_to_fit(
            review.shape[1], review.shape[0], MAX_DISPLAY_WIDTH, MAX_DISPLAY_HEIGHT
        )
        review = display_image(review, review_scale)
        write_image_unicode(self.output_dir / "source_card_candidates.jpg", review)
        self.mode = "review"
        self.set_canvas_image(review)
        if manual_only:
            self.status.config(text="人工确认模式：已锁定人工区域。S 保存，R 重标；算法区域只作参考。")
        else:
            self.status.config(
                text="对比完成：必须先按 1 用人工区域，或按 2 用算法区域；R 重新标记，S 保存。"
            )

    def choose_manual(self) -> None:
        if self.manual_candidate is None:
            return
        self.selected_candidate = self.manual_candidate
        self.status.config(text="已选择人工区域。按 S 保存，或 R 重标。")

    def choose_auto(self) -> None:
        if self.auto_candidate is None:
            return
        self.selected_candidate = self.auto_candidate
        self.status.config(text=f"已选择算法区域：{self.auto_candidate.source}。按 S 保存，或 R 重标。")

    def reset_marking(self) -> None:
        self.points_display.clear()
        self.manual_candidate = None
        self.auto_candidate = None
        self.selected_candidate = None
        self.status.config(text="已重置。左键依次点击身份证四角：左上、右上、右下、左下。")
        self.show_marking_image()

    def save_selected(self) -> None:
        if self.selected_candidate is None:
            self.status.config(text="还没有选择区域。先标 4 点并自动计算，再按 1 或 2 选择。")
            return
        clean_card = rectify_card_image(self.image, self.selected_candidate.points)
        clean_path = self.output_dir / "clean_id_card.jpg"
        json_path = self.output_dir / "selected_source_quad.json"
        write_image_unicode(clean_path, clean_card)
        payload = {
            "image": str(self.image_path),
            "selected": asdict(self.selected_candidate),
            "manual": asdict(self.manual_candidate) if self.manual_candidate else None,
            "auto": asdict(self.auto_candidate) if self.auto_candidate else None,
            "outputs": {
                "clean_card": str(clean_path),
                "comparison": str(self.output_dir / "source_card_candidates.jpg"),
            },
        }
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.status.config(text=f"已保存：{clean_path}；四角坐标：{json_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="身份证上传图片区域标注与自动修边 demo")
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--padding-ratio", type=float, default=0.16)
    parser.add_argument("--canny-low", type=int, default=35)
    parser.add_argument("--canny-high", type=int, default=125)
    parser.add_argument("--manual-only", action="store_true", help="只保存人工标记区域，自动区域仅作参考")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    options = {
        "padding_ratio": args.padding_ratio,
        "canny_low": args.canny_low,
        "canny_high": args.canny_high,
        "manual_only": args.manual_only,
        "expected_aspect_ratio": 1.585,
        "aspect_ratio_tolerance": 0.45,
        "min_area_ratio": 0.28,
    }
    app = SourceCardMarkerApp(args.image.resolve(), args.output_dir.resolve(), options)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
