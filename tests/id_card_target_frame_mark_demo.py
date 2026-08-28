from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from id_card_source_mark_demo import (
    DEFAULT_OUTPUT_DIR,
    MAX_DISPLAY_HEIGHT,
    MAX_DISPLAY_WIDTH,
    Quad,
    QuadCandidate,
    denormalize_points,
    display_image,
    draw_quad,
    order_quad_points,
    panel_label,
    propose_card_quad,
    read_image_unicode,
    rectify_card_image,
    scale_to_fit,
    write_image_unicode,
)


DEFAULT_VIDEO = Path(r"G:\duikouxing\samples\swap_128.mp4")
DEFAULT_CLEAN_CARD = DEFAULT_OUTPUT_DIR / "clean_id_card.jpg"


def grab_video_frame(video_path: Path, at_seconds: float = 0.0) -> tuple[np.ndarray, dict[str, Any]]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频: {video_path}")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    frame_index = 0
    if at_seconds > 0 and fps > 0:
        frame_index = max(0, min(total_frames - 1, int(round(at_seconds * fps))))
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)

    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        raise RuntimeError(f"无法读取视频帧: {video_path} @ {at_seconds}s")

    metadata = {
        "video": str(video_path),
        "width": width,
        "height": height,
        "fps": fps,
        "frame_count": total_frames,
        "frame_index": frame_index,
        "at_seconds": frame_index / fps if fps > 0 else at_seconds,
    }
    return frame, metadata


def warp_card_to_frame(
    card_bgr: np.ndarray,
    frame_shape: tuple[int, int, int],
    target_quad: Quad,
) -> tuple[np.ndarray, np.ndarray]:
    frame_h, frame_w = frame_shape[:2]
    card_h, card_w = card_bgr.shape[:2]
    src = np.array(
        [
            [0, 0],
            [card_w - 1, 0],
            [card_w - 1, card_h - 1],
            [0, card_h - 1],
        ],
        dtype=np.float32,
    )
    dst = order_quad_points(denormalize_points(target_quad, frame_w, frame_h))
    matrix = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(
        card_bgr,
        matrix,
        (frame_w, frame_h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    source_mask = np.full((card_h, card_w), 255, dtype=np.uint8)
    mask = cv2.warpPerspective(
        source_mask,
        matrix,
        (frame_w, frame_h),
        flags=cv2.INTER_NEAREST,
    )
    return warped, mask


def preview_card_paste(
    frame_bgr: np.ndarray,
    clean_card_bgr: np.ndarray,
    target_quad: Quad,
    feather_pixels: int = 2,
) -> np.ndarray:
    warped, mask = warp_card_to_frame(clean_card_bgr, frame_bgr.shape, target_quad)
    alpha = mask.astype(np.float32) / 255.0
    if feather_pixels > 0:
        kernel_size = feather_pixels * 2 + 1
        alpha = cv2.GaussianBlur(alpha, (kernel_size, kernel_size), 0)
    alpha3 = alpha[..., None]
    result = frame_bgr.astype(np.float32) * (1.0 - alpha3) + warped.astype(np.float32) * alpha3
    return np.clip(result, 0, 255).astype(np.uint8)


def make_target_review_image(
    frame_bgr: np.ndarray,
    manual: QuadCandidate,
    auto: QuadCandidate,
    clean_card_bgr: np.ndarray | None,
) -> np.ndarray:
    left = draw_quad(
        frame_bgr,
        manual.points,
        color=(0, 220, 255),
        label=f"Manual score={manual.score:.2f}",
    )
    middle = draw_quad(
        frame_bgr,
        auto.points,
        color=(80, 255, 80),
        label=f"Auto/{auto.source} score={auto.score:.2f}",
    )
    if clean_card_bgr is None:
        right = rectify_card_image(frame_bgr, auto.points)
        right_label = "preview: rectified frame crop"
    else:
        right = preview_card_paste(frame_bgr, clean_card_bgr, auto.points)
        right_label = "preview: clean card pasted into frame"

    panel_h = 430
    left = cv2.resize(left, (int(left.shape[1] * panel_h / left.shape[0]), panel_h))
    middle = cv2.resize(middle, (int(middle.shape[1] * panel_h / middle.shape[0]), panel_h))
    right = cv2.resize(right, (int(right.shape[1] * panel_h / right.shape[0]), panel_h))

    left = panel_label(left, "1: use manual target")
    middle = panel_label(middle, f"2: use auto target ({auto.reason})")
    right = panel_label(right, right_label)
    return cv2.hconcat([left, middle, right])


class TargetFrameMarkerApp:
    def __init__(
        self,
        video_path: Path,
        at_seconds: float,
        output_dir: Path,
        detection_options: dict[str, Any],
        clean_card_path: Path | None,
    ) -> None:
        import tkinter as tk

        self.tk = tk
        self.video_path = video_path
        self.frame, self.metadata = grab_video_frame(video_path, at_seconds)
        self.output_dir = output_dir
        self.options = detection_options
        self.clean_card_path = clean_card_path if clean_card_path and clean_card_path.is_file() else None
        self.clean_card = read_image_unicode(self.clean_card_path) if self.clean_card_path else None
        self.scale = scale_to_fit(
            self.frame.shape[1],
            self.frame.shape[0],
            MAX_DISPLAY_WIDTH,
            MAX_DISPLAY_HEIGHT,
        )
        self.points_display: list[tuple[int, int]] = []
        self.canvas_items: list[int] = []
        self.manual_candidate: QuadCandidate | None = None
        self.auto_candidate: QuadCandidate | None = None
        self.selected_candidate: QuadCandidate | None = None
        self.photo: Any = None
        self.mode = "mark"

        self.root = tk.Tk()
        self.root.title("视频帧身份证区域 demo - 粗标四角后自动找边")
        self.canvas = tk.Canvas(self.root, highlightthickness=0)
        self.canvas.pack()

        bar = tk.Frame(self.root)
        bar.pack(fill="x")
        self.status = tk.Label(
            bar,
            text=(
                "视频帧已读取。左键依次点击身份证四角：左上、右上、右下、左下。"
                "Enter 自动计算。"
            ),
            anchor="w",
        )
        self.status.pack(side="left", padx=8, pady=6)
        tk.Button(bar, text="自动计算 (Enter)", command=self.compute_candidates).pack(
            side="right", padx=6, pady=4
        )
        tk.Button(bar, text="重标 (R)", command=self.reset_marking).pack(side="right", pady=4)
        tk.Button(bar, text="保存 (S)", command=self.save_selected).pack(side="right", pady=4)

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
        temp_path = self.output_dir / "_tk_target_preview.png"
        write_image_unicode(temp_path, image_bgr)
        self.photo = self.tk.PhotoImage(file=str(temp_path))
        self.canvas.config(width=image_bgr.shape[1], height=image_bgr.shape[0])
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")

    def show_marking_image(self) -> None:
        self.mode = "mark"
        self.set_canvas_image(display_image(self.frame, self.scale))
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
        height, width = self.frame.shape[:2]
        pts = np.array(
            [[x / self.scale, y / self.scale] for x, y in self.points_display],
            dtype=np.float32,
        )
        pts = order_quad_points(pts)
        return [
            (
                round(float(x) / max(width - 1, 1), 6),
                round(float(y) / max(height - 1, 1), 6),
            )
            for x, y in pts
        ]

    def compute_candidates(self) -> None:
        if len(self.points_display) != 4:
            self.status.config(text="需要刚好 4 个点：左上、右上、右下、左下。")
            return
        manual_quad = self.current_manual_quad()
        best, candidates = propose_card_quad(self.frame, manual_quad, self.options)
        manual = next(candidate for candidate in candidates if candidate.source == "manual")
        self.manual_candidate = manual
        self.auto_candidate = best if best.source != "manual" else candidates[1] if len(candidates) > 1 else best
        manual_only = bool(self.options.get("manual_only", False))
        self.selected_candidate = self.manual_candidate if manual_only else None

        review = make_target_review_image(
            self.frame,
            self.manual_candidate,
            self.auto_candidate,
            self.clean_card,
        )
        review_scale = scale_to_fit(
            review.shape[1],
            review.shape[0],
            MAX_DISPLAY_WIDTH,
            MAX_DISPLAY_HEIGHT,
        )
        review = display_image(review, review_scale)
        write_image_unicode(self.output_dir / "target_card_candidates.jpg", review)
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
        self.status.config(text="已选择人工视频帧区域。按 S 保存，或 R 重标。")

    def choose_auto(self) -> None:
        if self.auto_candidate is None:
            return
        self.selected_candidate = self.auto_candidate
        self.status.config(text=f"已选择算法视频帧区域：{self.auto_candidate.source}。按 S 保存，或 R 重标。")

    def reset_marking(self) -> None:
        self.points_display.clear()
        self.manual_candidate = None
        self.auto_candidate = None
        self.selected_candidate = None
        self.status.config(text="已重置。左键依次点击视频帧中的身份证四角。")
        self.show_marking_image()

    def save_selected(self) -> None:
        if self.selected_candidate is None:
            self.status.config(text="还没有选择区域。先标 4 点并自动计算，再按 1 或 2 选择。")
            return
        json_path = self.output_dir / "selected_target_quad.json"
        payload = {
            "video_metadata": self.metadata,
            "clean_card": str(self.clean_card_path) if self.clean_card_path else None,
            "selected": asdict(self.selected_candidate),
            "manual": asdict(self.manual_candidate) if self.manual_candidate else None,
            "auto": asdict(self.auto_candidate) if self.auto_candidate else None,
            "outputs": {
                "comparison": str(self.output_dir / "target_card_candidates.jpg"),
            },
        }
        if self.clean_card is not None:
            preview = preview_card_paste(self.frame, self.clean_card, self.selected_candidate.points)
            preview_path = self.output_dir / "target_paste_preview.jpg"
            write_image_unicode(preview_path, preview)
            payload["outputs"]["paste_preview"] = str(preview_path)
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.status.config(text=f"已保存视频帧四角坐标：{json_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="视频帧身份证区域标注与自动修边 demo")
    parser.add_argument("--video", type=Path, default=DEFAULT_VIDEO)
    parser.add_argument("--at-seconds", type=float, default=0.0)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--clean-card",
        type=Path,
        default=DEFAULT_CLEAN_CARD,
        help="可选：图片端 demo 产出的 clean_id_card.jpg，用于展示贴回预览。",
    )
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
    app = TargetFrameMarkerApp(
        args.video.resolve(),
        args.at_seconds,
        args.output_dir.resolve(),
        options,
        args.clean_card.resolve() if args.clean_card else None,
    )
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
