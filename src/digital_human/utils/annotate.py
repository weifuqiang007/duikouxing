from __future__ import annotations

from pathlib import Path

from digital_human.composite import box_to_roi, grab_frame
from digital_human.config import MouthROI

_MAX_DISPLAY = 900  # 显示窗口最长边（像素），避免超出屏幕


def select_mouth_roi(
    video: Path, current: MouthROI, at_seconds: float = 0.0
) -> MouthROI | None:
    """弹出 tkinter 窗口拖拽矩形标注嘴部区域，返回换算后的 ROI；取消返回 None。"""
    import tkinter as tk

    import cv2  # headless 即可：只做缩放和存 PNG

    frame = grab_frame(video, at_seconds)
    frame_h, frame_w = frame.shape[:2]
    scale = min(_MAX_DISPLAY / frame_h, _MAX_DISPLAY / frame_w, 1.0)
    disp_w, disp_h = int(frame_w * scale), int(frame_h * scale)
    display = cv2.resize(frame, (disp_w, disp_h), interpolation=cv2.INTER_AREA)
    image_path = Path(".tmp") / "annotate_frame.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(image_path), display)

    result: dict[str, MouthROI | None] = {"roi": None}
    state: dict[str, object] = {"start": None, "box": None, "rect": None, "oval": None}

    root = tk.Tk()
    root.title("嘴部 ROI 标注")
    photo = tk.PhotoImage(file=str(image_path))  # tk 8.6 原生支持 PNG
    canvas = tk.Canvas(root, width=disp_w, height=disp_h, highlightthickness=0)
    canvas.pack()
    canvas.create_image(0, 0, image=photo, anchor="nw")

    def clear_shapes() -> None:
        for key in ("rect", "oval"):
            if state[key]:
                canvas.delete(state[key])
                state[key] = None
        state["box"] = None

    def on_press(event: "tk.Event[tk.Canvas]") -> None:
        clear_shapes()
        state["start"] = (event.x, event.y)

    def on_drag(event: "tk.Event[tk.Canvas]") -> None:
        if not state["start"]:
            return
        clear_shapes()
        left, right = sorted((state["start"][0], event.x))
        top, bottom = sorted((state["start"][1], event.y))
        state["rect"] = canvas.create_rectangle(
            left, top, right, bottom, outline="#00c800", width=1
        )
        state["oval"] = canvas.create_oval(
            left, top, right, bottom, outline="#ffdd00", width=3
        )
        state["box"] = (left, top, right, bottom)

    def on_save() -> None:
        if state["box"]:
            left, top, right, bottom = state["box"]
            result["roi"] = box_to_roi(
                int(left / scale),
                int(top / scale),
                int(round((right - left) / scale)),
                int(round((bottom - top) / scale)),
                frame_w,
                frame_h,
                current.feather_pixels,
            )
        root.destroy()

    def on_cancel() -> None:
        result["roi"] = None
        root.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    root.bind("<Return>", lambda _event: on_save())
    root.bind("<Escape>", lambda _event: on_cancel())

    bar = tk.Frame(root)
    bar.pack(fill="x")
    tk.Label(
        bar,
        text="拖一个矩形：上到鼻底、下过下巴、左右到脸颊（椭圆内切于矩形，略大更稳）",
    ).pack(side="left", padx=6, pady=4)
    tk.Button(bar, text="保存 (Enter)", command=on_save).pack(side="right", padx=6, pady=4)
    tk.Button(bar, text="重拖", command=clear_shapes).pack(side="right", pady=4)
    tk.Button(bar, text="取消 (Esc)", command=on_cancel).pack(side="right", pady=4)

    root.mainloop()
    return result["roi"]

def select_polygon_points(
    video: Path,
    at_seconds: float = 0.0,
    *,
    title: str = "多边形标注",
    expected_points: int | None = None,
) -> list[tuple[float, float]] | None:
    """弹出 tkinter 窗口逐点标注多边形，返回归一化坐标列表；取消返回 None。

    左键增加点，右键撤销上一个点，Enter 保存，Esc 取消。
    """
    import tkinter as tk

    frame = grab_frame(video, at_seconds)
    frame_h, frame_w = frame.shape[:2]
    scale = min(_MAX_DISPLAY / frame_h, _MAX_DISPLAY / frame_w, 1.0)
    disp_w, disp_h = int(frame_w * scale), int(frame_h * scale)
    display = cv2.resize(frame, (disp_w, disp_h), interpolation=cv2.INTER_AREA)
    image_path = Path(".tmp") / "annotate_frame.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(image_path), display)

    result: dict[str, list[tuple[float, float]] | None] = {"points": None}
    points: list[tuple[int, int]] = []
    canvas_items: list[int] = []

    root = tk.Tk()
    root.title(title)
    photo = tk.PhotoImage(file=str(image_path))
    canvas = tk.Canvas(root, width=disp_w, height=disp_h, highlightthickness=0)
    canvas.pack()
    canvas.create_image(0, 0, image=photo, anchor="nw")

    def redraw() -> None:
        for item_id in canvas_items:
            canvas.delete(item_id)
        canvas_items.clear()
        # Lines connecting consecutive points.
        for i in range(len(points)):
            x1, y1 = points[i]
            x2, y2 = points[(i + 1) % len(points)] if len(points) > 1 else (x1, y1)
            item = canvas.create_line(x1, y1, x2, y2, fill="#00c800", width=2)
            canvas_items.append(item)
        # Close polygon if 3+ points.
        if len(points) >= 3:
            item = canvas.create_line(
                points[-1][0], points[-1][1],
                points[0][0], points[0][1],
                fill="#00c800", width=2, dash=(4, 4),
            )
            canvas_items.append(item)
        # Point circles and numbers.
        for i, (x, y) in enumerate(points):
            r = 4
            item = canvas.create_oval(x - r, y - r, x + r, y + r, fill="#ffdd00", outline="white")
            canvas_items.append(item)
            item = canvas.create_text(x + 8, y - 8, text=str(i + 1), fill="white", font=("Arial", 9, "bold"))
            canvas_items.append(item)

    def on_left_click(event: "tk.Event[tk.Canvas]") -> None:
        points.append((event.x, event.y))
        redraw()

    def on_right_click(event: "tk.Event[tk.Canvas]") -> None:
        if points:
            points.pop()
            redraw()

    def on_save() -> None:
        if expected_points is not None and len(points) != expected_points:
            return  # Don't save if wrong count.
        if not points:
            result["points"] = None
        else:
            result["points"] = [(x / scale / frame_w, y / scale / frame_h) for x, y in points]
        root.destroy()

    def on_cancel() -> None:
        result["points"] = None
        root.destroy()

    canvas.bind("<ButtonPress-1>", on_left_click)
    canvas.bind("<ButtonPress-3>", on_right_click)
    root.bind("<Return>", lambda _e: on_save())
    root.bind("<Escape>", lambda _e: on_cancel())

    bar = tk.Frame(root)
    bar.pack(fill="x")
    hint = f"左键加点 ({len(points)}/"
    if expected_points:
        hint += f"{expected_points})"
    else:
        hint += "N)"
    hint += "; 右键撤销; Enter 保存; Esc 取消"
    tk.Label(bar, text=hint).pack(side="left", padx=6, pady=4)
    tk.Button(bar, text="保存 (Enter)", command=on_save).pack(side="right", padx=6, pady=4)
    tk.Button(bar, text="取消 (Esc)", command=on_cancel).pack(side="right", pady=4)

    root.mainloop()
    return result["points"]
