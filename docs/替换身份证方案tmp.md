# 身份证证件替换 — 交互式方案 v2

> 日期：2026-08-28
> 状态：**设计稿，待确认**

---

## 一、需求重新梳理

现有方案（v1）要求用户直接标出证件精确四角，但实际场景中：

1. **新证件图片**拍出来往往带桌面背景，需要先裁出纯证件区域
2. **视频中的证件**有手指遮挡，四角不一定是可见的
3. 人工标的不一定准，需要有算法辅助精修

所以用户想要的流程是：

```
阶段 A：证件图片裁切（从拍摄图中提取纯证件）
  用户粗标 → AI 自动寻找边缘 → 用户对比选择 → 不满意可反复

阶段 B：视频帧中标记证件位置（在哪里贴）
  用户粗标 → AI 自动寻找边缘 → 用户对比选择 → 不满意可反复

阶段 C：执行替换
  用 A 裁出的纯证件 + B 标出的视频位置 → 逐帧替换
```

---

## 二、整体架构

```
src/digital_human/
  id_card.py          ← 保留现有替换管线（replace_id_card_in_frame/video）
  id_card_workflow.py ← 【新增】交互式工作流编排
  id_card_edge.py     ← 【新增】边缘检测与四角精修算法
  annotate.py         ← 修改：新增矩形标注 + 对比展示窗口
  config.py           ← 修改：新增 ImageCardRegion 数据类
  cli.py              ← 修改：新增一个 workflow 子命令
```

---

## 三、阶段 A：证件图片裁切

### 3.1 交互流程

```
┌─────────────────────────────────────────────┐
│  Step 1: 弹窗显示新证件原图                   │
│  提示: "拖拽框选身份证区域"                      │
│  用户拖出一个矩形（可能包含部分桌面背景）          │
└──────────────────────┬──────────────────────┘
                       ↓
┌─────────────────────────────────────────────┐
│  Step 2: 算法自动运算                         │
│  在用户矩形外扩 padding 区域分析边缘            │
│  找到最可能的证件精确四角                       │
└──────────────────────┬──────────────────────┘
                       ↓
┌─────────────────────────────────────────────┐
│  Step 3: 对比展示窗口                         │
│  ┌──────────────┐  ┌──────────────┐          │
│  │  人工标注结果  │  │  AI 计算结果  │          │
│  │  (绿色框)     │  │  (蓝色框)     │          │
│  └──────────────┘  └──────────────┘          │
│                                              │
│  [采用人工] [采用AI] [重新标注] [完成]          │
└─────────────────────────────────────────────┘
```

点「采用人工」或「采用AI」→ 保存结果，进入阶段 B。
点「重新标注」→ 回到 Step 1，可以重新框选。

### 3.2 边缘检测算法

```python
"""
id_card_edge.py — 核心算法：从用户粗标矩形出发，自动精修到证件精确边缘

思路：
  身份证卡面与背景（桌面）之间有明显的颜色跳变。
  在用户标记的矩形四周向外扩展一个 padding 带，
  在这个带内寻找最强的颜色梯度/边缘，
  这些边缘连成的四边形就是证件的真实边界。
"""

import cv2
import numpy as np
from typing import Optional


def refine_card_boundary(
    image: np.ndarray,           # BGR 原图
    user_rect: tuple[int, int, int, int],  # (x, y, w, h) 用户标注的矩形
    padding_pixels: int = 40,    # 向外扩展多少像素来搜索边缘
    min_edge_strength: float = 15.0,  # 边缘强度阈值
) -> Optional[list[tuple[int, int]]]:
    """
    输入：原图 + 用户粗标矩形
    输出：精修后的 4 个角点 [(x,y), ...] TL TR BR BL，或 None（找不到）
    """
    img_h, img_w = image.shape[:2]
    ux, uy, uw, uh = user_rect

    # ---- 1. 构建搜索区域：用户矩形向外扩展 padding ----
    sx = max(0, ux - padding_pixels)
    sy = max(0, uy - padding_pixels)
    ex = min(img_w, ux + uw + padding_pixels)
    ey = min(img_h, uy + uh + padding_pixels)
    search_region = image[sy:ey, sx:ex].copy()
    # 用户矩形在搜索区域中的相对坐标
    local_rect = (ux - sx, uy - sy, uw, uh)

    # ---- 2. 计算颜色梯度图 ----
    # 使用 LAB 空间的 a、b 通道梯度 + L 通道梯度的融合
    lab = cv2.cvtColor(search_region, cv2.COLOR_BGR2LAB).astype(np.float32)
    L, A, B = lab[:, :, 0], lab[:, :, 1], lab[:, :, 2]

    # 每个通道的 Sobel 梯度幅值
    def channel_grad(ch: np.ndarray) -> np.ndarray:
        gx = cv2.Sobel(ch, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(ch, cv2.CV_32F, 0, 1, ksize=3)
        return np.sqrt(gx ** 2 + gy ** 2)

    grad = np.maximum.reduce([channel_grad(L), channel_grad(A), channel_grad(B)])

    # ---- 3. 在用户矩形的四条边外侧，分别搜索最强边缘 ----
    # 策略：沿每条边的法线方向，取梯度投影的最大值位置
    lx, ly, lw, lh = local_rect
    cx, cy = lx + lw / 2, ly + lh / 2  # 矩形中心

    # 4 条边：上、下、左、右
    edges = {}  # 'top' -> (位置, 强度)

    # --- 上边：沿 y 轴向上（减小 y），在矩形上方的条带中搜索 ---
    band_top = grad[max(0, ly - padding_pixels):ly, lx:lx + lw]
    if band_top.size > 0:
        col_grad_y = np.mean(band_top, axis=1)  # 每行的平均梯度
        # 找从内向外（从下到上）梯度最大的行
        for i in range(len(col_grad_y) - 1, -1, -1):
            if col_grad_y[i] > min_edge_strength:
                edges['top'] = (sy + max(0, ly - padding_pixels) + i, col_grad_y[i])
                break

    # --- 下边 ---
    band_bottom = grad[ly + lh:min(ly + lh + padding_pixels, grad.shape[0]), lx:lx + lw]
    if band_bottom.size > 0:
        col_grad_y = np.mean(band_bottom, axis=1)
        for i in range(len(col_grad_y)):
            if col_grad_y[i] > min_edge_strength:
                edges['bottom'] = (sy + ly + lh + i, col_grad_y[i])
                break

    # --- 左边 ---
    band_left = grad[ly:ly + lh, max(0, lx - padding_pixels):lx]
    if band_left.size > 0:
        row_grad_x = np.mean(band_left, axis=0)
        for i in range(len(row_grad_x) - 1, -1, -1):
            if row_grad_x[i] > min_edge_strength:
                edges['left'] = (sx + max(0, lx - padding_pixels) + i, row_grad_x[i])
                break

    # --- 右边 ---
    band_right = grad[ly:ly + lh, lx + lw:min(lx + lw + padding_pixels, grad.shape[0]):]
    if band_right.size > 0 and band_right.shape[1] > 0:
        row_grad_x = np.mean(band_right, axis=0)
        for i in range(len(row_grad_x)):
            if row_grad_x[i] > min_edge_strength:
                edges['right'] = (sx + lx + lw + i, row_grad_x[i])
                break

    # ---- 4. 如果四条边都找到了，组装四角 ----
    if len(edges) < 3:  # 至少要找到 3 条边
        return None

    top_y = edges.get('top', (uy, 0))[0]
    bottom_y = edges.get('bottom', (uy + uh, 0))[0]
    left_x = edges.get('left', (ux, 0))[0]
    right_x = edges.get('right', (ux + uw, 0))[0]

    corners = [
        (left_x, top_y),    # TL
        (right_x, top_y),   # TR
        (right_x, bottom_y),# BR
        (left_x, bottom_y), # BL
    ]

    return corners


def refine_card_boundary_v2(
    image: np.ndarray,
    user_rect: tuple[int, int, int, int],
    padding_pixels: int = 50,
) -> Optional[list[tuple[int, int]]]:
    """
    备选方案（更鲁棒）：Canny + 轮廓检测
    
    适合：证件边缘对比度更强、背景更简单的场景
    """
    img_h, img_w = image.shape[:2]
    ux, uy, uw, uh = user_rect

    # 搜索区域
    sx = max(0, ux - padding_pixels)
    sy = max(0, uy - padding_pixels)
    ex = min(img_w, ux + uw + padding_pixels)
    ey = min(img_h, uy + uh + padding_pixels)
    search = image[sy:ey, sx:ex].copy()

    gray = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)

    # 膨胀让边缘连续
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    edges = cv2.dilate(edges, kernel, iterations=1)

    # 找轮廓
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # 筛选：面积接近用户矩形、近似四边形
    user_area = uw * uh
    best_contour = None
    best_score = float('inf')

    for cnt in contours:
        area = cv2.contourArea(cnt)
        # 面积在用户标记的 50%~200% 之间
        if area < user_area * 0.3 or area > user_area * 2.5:
            continue

        # 近似多边形
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)

        # 需要 4 个顶点
        if len(approx) != 4:
            continue

        # 与用户矩形的面积差异
        score = abs(area - user_area) / user_area
        if score < best_score:
            best_score = score
            best_contour = approx

    if best_contour is None:
        return None

    # 转回原图坐标，按 TL TR BR BL 排序
    pts = best_contour.reshape(4, 2).astype(int)
    pts[:, 0] += sx
    pts[:, 1] += sy

    # 按角度排序四角
    center = pts.mean(axis=0)
    angles = np.arctan2(pts[:, 1] - center[1], pts[:, 0] - center[0])
    order = np.argsort(angles)
    # 调整起始角到左上
    # 找 y 最小（最上面）的点作为起始
    top_two = order[:2]
    if pts[top_two[0], 0] > pts[top_two[1], 0]:
        top_two = top_two[::-1]
    bottom_two = order[2:]
    if pts[bottom_two[0], 0] > pts[bottom_two[1], 0]:
        bottom_two = bottom_two[::-1]
    ordered = np.array([pts[top_two[0]], pts[top_two[1]],
                          pts[bottom_two[1]], pts[bottom_two[0]]])

    return [tuple(p) for p in ordered]
```

### 3.3 两版算法的适用场景

| | v1: 梯度投影法 | v2: Canny+轮廓法 |
|---|---|---|
| 适合 | 背景简单、证件边缘清晰的桌面拍摄 | 证件与背景反差大、边缘连续的场景 |
| 不适合 | 桌面纹理复杂/有图案 | 桌面上有其他物体干扰轮廓 |
| 速度 | 快（纯矩阵运算） | 稍慢（轮廓检测） |
| 输出 | 轴对齐矩形四角 | 任意四边形四角 |

**建议：默认先跑 v1，如果 v1 找到的四角面积与用户标记差异 >30%，自动回退 v2。两版都返回时取面积更接近用户标记的。**

---

## 四、阶段 B：视频帧中标记证件位置

### 4.1 交互流程

与阶段 A **完全一致**，区别只是：
- 图片来源是视频的某一帧（默认首帧 `at_seconds=0`）
- 用户标注的是「证件在视频中的位置」，不是裁切
- 标注结果直接作为 `replace_id_card_in_video` 的 `corners` 参数

```
┌─────────────────────────────────────────────┐
│  Step 1: 弹窗显示视频首帧                      │
│  提示: "拖拽框选视频中身份证区域"                  │
│  用户拖出一个矩形                              │
└──────────────────────┬──────────────────────┘
                       ↓
┌─────────────────────────────────────────────┐
│  Step 2: 算法自动运算（同阶段 A）                │
└──────────────────────┬──────────────────────┘
                       ↓
┌─────────────────────────────────────────────┐
│  Step 3: 对比展示 + 选择                       │
└──────────────────────┬──────────────────────┘
                       ↓
              保存 corners → 进入阶段 C
```

### 4.2 视频中的特殊考虑

视频帧中的证件可能有手指遮挡，所以边缘检测需要额外处理：

```python
def refine_card_boundary_for_video(
    frame: np.ndarray,
    user_rect: tuple[int, int, int, int],
    padding_pixels: int = 50,
) -> Optional[list[tuple[int, int]]]:
    """
    视频帧版本的边缘精修。
    
    与图片版的区别：
    - 手指遮挡会导致某些边检测不到 → 允许部分边回退到用户标注
    - 证件可能有透视变形 → 用 Canny+轮廓法更合适
    """
    result = refine_card_boundary_v2(frame, user_rect, padding_pixels)

    if result is None:
        # 视频中检测失败时，回退：用用户矩形但各边独立精修
        result = refine_card_boundary(frame, user_rect, padding_pixels)

    if result is None:
        # 最终回退：直接把用户矩形转成四角
        ux, uy, uw, uh = user_rect
        result = [
            (ux, uy),
            (ux + uw, uy),
            (ux + uw, uy + uh),
            (ux, uy + uh),
        ]

    return result
```

---

## 五、对比展示窗口

这是用户最核心的交互环节。复用现有 tkinter 模式，新增一个对比窗口函数：

```python
"""
annotate.py 新增函数
"""

def compare_and_select(
    image: np.ndarray,                    # 要展示的图（证件原图或视频帧）
    manual_corners: list[tuple[int, int]], # 人工标注的四角
    ai_corners: list[tuple[int, int]],     # AI 计算的四角
    *,
    title: str = "选择标注结果",
) -> tuple[str, list[tuple[int, int]]] | None:
    """
    并排展示人工标注和 AI 结果，让用户选择。
    
    返回：
      ("manual", corners) 或 ("ai", corners) 或 None（用户取消/重新标注）
    
    窗口布局：
    ┌──────────────────────────────────────────────┐
    │  [人工标注 (绿色)]  vs  [AI 检测 (蓝色)]        │
    │  ┌────────────┐    ┌────────────┐            │
    │  │            │    │            │            │
    │  │   左图      │    │   右图      │            │
    │  │            │    │            │            │
    │  └────────────┘    └────────────┘            │
    │                                              │
    │  [采用人工标注]  [采用AI结果]  [重新标注]  [取消] │
    └──────────────────────────────────────────────┘
    
    左右图都在对应位置画上四角连线 + 角点圆圈。
    图下方显示四角的像素坐标（方便用户判断精度）。
    """
```

实现要点：
- 左右两张图各自用 `cv2.polylines` 画框 + `cv2.circle` 画角点
- 缩放到 `_MAX_DISPLAY` 范围内（复用现有 `min(900/h, 900/w, 1.0)` 逻辑）
- 写两个临时 PNG，用两个 `tk.Canvas` 并排显示
- 按钮回调直接 `root.destroy()` + 设置 `result` 闭包变量

---

## 六、矩形标注器

现有 `select_polygon_points` 是点选模式，用户需要的是**拖拽矩形**模式。
现有 `select_mouth_roi` 已经实现了拖拽矩形，但它是椭圆 ROI。

```python
"""
annotate.py 新增函数 — 复用 select_mouth_roi 的拖拽模式，但返回矩形四角
"""

def select_rectangle_corners(
    image_path: Path | np.ndarray,
    *,
    title: str = "框选证件区域",
    max_display: int = 900,
) -> tuple[int, int, int, int] | None:
    """
    弹出窗口，用户拖拽画一个矩形。
    
    交互方式（复用现有 mouth ROI 的拖拽模式）：
    - 鼠标左键按下 → 记录起点
    - 拖拽 → 实时显示矩形预览（绿色虚线）
    - 松开 → 矩形确定
    - Enter → 确认
    - Esc → 取消
    
    返回：(x, y, w, h) 原图像素坐标，或 None（取消）
    """
```

这个函数和现有 `select_mouth_roi` 的拖拽逻辑几乎一样，区别是：
- 不转换为 `MouthROI`，直接返回像素矩形
- 可以接受 `np.ndarray`（不限于视频路径，因为证件图是图片不是视频）
- 画的是矩形框而非椭圆

---

## 七、工作流编排

```python
"""
id_card_workflow.py — 新增模块

整体编排三个阶段，串联调用 annotate + edge detection + replacement
"""

from pathlib import Path
import numpy as np
from dataclasses import dataclass


@dataclass
class WorkflowResult:
    """工作流的最终产出"""
    # 阶段 A 的结果：裁切后的纯证件图
    cropped_card: np.ndarray
    cropped_corners: list[tuple[float, float]]  # 归一化，裁切后的四角（用于透视变换源）
    
    # 阶段 B 的结果：视频中的目标位置
    video_corners: list[tuple[float, float]]  # 归一化，TL TR BR BL
    
    # 原始配置
    source_image: Path
    input_video: Path
    output_video: Path


def run_id_card_workflow(
    source_image: Path,
    input_video: Path,
    output_video: Path,
    at_seconds: float = 0.0,
) -> WorkflowResult:
    """
    交互式证件替换工作流入口。
    
    流程：
    1. 读取证件图片 → 用户框选 → AI 精修 → 对比选择 → 裁切
    2. 读取视频首帧 → 用户框选 → AI 精修 → 对比选择
    3. 执行逐帧替换
    """
    import cv2
    from .annotate import select_rectangle_corners, compare_and_select
    from .id_card_edge import refine_card_boundary, refine_card_boundary_v2
    from .id_card import replace_id_card_in_video
    from .config import IdCardConfig

    # ========== 阶段 A：证件图片裁切 ==========
    print("=" * 50)
    print("阶段 1/3：标记证件图片中的身份证区域")
    print("请在弹出窗口中框选身份证（可以包含少量背景，AI 会自动修正边缘）")
    print("=" * 50)

    card_image = _read_image(source_image)

    while True:  # 循环直到用户满意
        # Step 1: 用户框选
        user_rect = select_rectangle_corners(card_image, title="框选身份证区域")
        if user_rect is None:
            raise RuntimeError("用户取消标注")

        # Step 2: AI 自动精修
        ai_corners_px = _try_refine(card_image, user_rect)

        # 用户矩形的四角
        ux, uy, uw, uh = user_rect
        manual_corners_px = [(ux, uy), (ux+uw, uy), (ux+uw, uy+uh), (ux, uy+uh)]

        if ai_corners_px is not None:
            # Step 3: 对比展示
            choice = compare_and_select(
                card_image,
                manual_corners_px,
                ai_corners_px,
                title="证件裁切：选择标注结果",
            )
            if choice is None:
                continue  # 重新标注
            _, chosen_corners_px = choice
        else:
            print("  ⚠ AI 未检测到明显边缘，使用人工标注")
            chosen_corners_px = manual_corners_px
            # 仍然展示给用户确认
            confirm = compare_and_select(
                card_image,
                manual_corners_px,
                manual_corners_px,  # 两者相同
                title="AI 未检测到边缘，确认人工标注？",
            )
            if confirm is None:
                continue

        # 用户确认了，跳出循环
        break

    # 裁切证件图片
    cropped_card, card_corners_norm = _crop_card(card_image, chosen_corners_px)

    # ========== 阶段 B：视频帧中标记证件位置 ==========
    print("=" * 50)
    print("阶段 2/3：标记视频中身份证的位置")
    print("请在弹出窗口中框选视频帧中的身份证区域")
    print("=" * 50)

    frame = _grab_frame(input_video, at_seconds)
    frame_h, frame_w = frame.shape[:2]

    while True:
        user_rect = select_rectangle_corners(frame, title="框选视频中身份证区域")
        if user_rect is None:
            raise RuntimeError("用户取消标注")

        ai_corners_px = _try_refine_video(frame, user_rect)

        ux, uy, uw, uh = user_rect
        manual_corners_px = [(ux, uy), (ux+uw, uy), (ux+uw, uy+uh), (ux, uy+uh)]

        if ai_corners_px is not None:
            choice = compare_and_select(
                frame,
                manual_corners_px,
                ai_corners_px,
                title="视频标记：选择标注结果",
            )
            if choice is None:
                continue
            _, chosen_corners_px = choice
        else:
            print("  ⚠ AI 未检测到明显边缘，使用人工标注")
            chosen_corners_px = manual_corners_px

        break

    # 归一化视频四角
    video_corners_norm = [(x / frame_w, y / frame_h) for x, y in chosen_corners_px]

    # ========== 阶段 C：执行替换 ==========
    print("=" * 50)
    print("阶段 3/3：执行视频中证件替换...")
    print("=" * 50)

    config = IdCardConfig(
        source_image=source_image,
        input_video=input_video,
        output_video=output_video,
        corners=video_corners_norm,
        protect_polygons=[],
        auto_detect_fingers=True,
        color_match={"mode": "lab_local"},
    )

    # 这里复用现有的 replace_id_card_in_video
    # 但需要注意：现有函数直接对 source_image 做透视变换
    # 如果阶段 A 裁切了证件，应该用 cropped_card 替代
    # → 需要小改 replace_id_card_in_frame 接受 card_image 参数
    #    （其实它已经接受了，只是 IdCardConfig 里的 source_image 是路径）
    
    result_path = _replace_with_cropped_card(cropped_card, config)
    
    print(f"✅ 替换完成: {result_path}")

    return WorkflowResult(
        cropped_card=cropped_card,
        cropped_corners=card_corners_norm,
        video_corners=video_corners_norm,
        source_image=source_image,
        input_video=input_video,
        output_video=output_video,
    )


def _try_refine(image, user_rect, padding=50):
    """尝试两种算法，取更好的结果"""
    v1 = refine_card_boundary(image, user_rect, padding)
    v2 = refine_card_boundary_v2(image, user_rect, padding)

    ux, uy, uw, uh = user_rect
    user_area = uw * uh

    def corner_area(corners):
        if corners is None:
            return 0
        xs = [c[0] for c in corners]
        ys = [c[1] for c in corners]
        return (max(xs) - min(xs)) * (max(ys) - min(ys))

    # 取面积更接近用户标记的
    candidates = []
    for corners, label in [(v1, "v1"), (v2, "v2")]:
        if corners is not None:
            area = corner_area(corners)
            diff = abs(area - user_area) / max(user_area, 1)
            if diff < 0.5:  # 面积差异不超过 50%
                candidates.append((diff, corners))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]  # 面积最接近的


def _crop_card(image, corners_px):
    """
    从原图中裁切出证件区域，透视变换为正矩形。
    
    返回：(裁切后的证件图 np.ndarray, 归一化四角)
    """
    import cv2
    
    pts = np.array(corners_px, dtype=np.float32)
    # 按周长排序：找最长边作为宽度参考
    # 计算 target 尺寸
    widths = [
        np.linalg.norm(pts[1] - pts[0]),  # top
        np.linalg.norm(pts[2] - pts[3]),  # bottom
    ]
    heights = [
        np.linalg.norm(pts[3] - pts[0]),  # left
        np.linalg.norm(pts[2] - pts[1]),  # right
    ]
    w = int(max(widths))
    h = int(max(heights))

    dst = np.array([[0, 0], [w-1, 0], [w-1, h-1], [0, h-1]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(pts, dst)
    warped = cv2.warpPerspective(image, M, (w, h))

    # 归一化四角（相对于裁切后图片）
    corners_norm = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]

    return warped, corners_norm
```

---

## 八、与现有模块的关系

### 复用不动的部分

| 模块 | 函数 | 用途 |
|------|------|------|
| `composite.py` | `grab_frame()` | 从视频取帧 |
| `id_card.py` | `replace_id_card_in_frame()` | 单帧替换（透视+保护+颜色匹配+合成） |
| `id_card.py` | `_auto_detect_non_card()` | 自动手指检测 |
| `id_card.py` | `_lab_local_color_match()` | LAB 颜色匹配 |
| `annotate.py` | `_MAX_DISPLAY` 常量 | 显示缩放上限 |
| `annotate.py` | tkinter 显示模式 | 写临时 PNG → PhotoImage → Canvas |

### 需要小改的部分

| 模块 | 改动 | 原因 |
|------|------|------|
| `id_card.py` | `replace_id_card_in_video` 新增可选参数 `card_image: np.ndarray | None` | 当阶段 A 裁切了证件时，直接传入裁切后的图，跳过从文件读取 |
| `annotate.py` | 新增 `select_rectangle_corners` | 图片标注需要接受 np.ndarray 而非仅 Path |
| `annotate.py` | 新增 `compare_and_select` | 对比展示窗口 |
| `config.py` | `IdCardConfig.source_image` 改为 `Path | None` | 允许直接传图片数据 |

### 新增模块

| 模块 | 职责 |
|------|------|
| `id_card_edge.py` | 边缘检测算法（v1 梯度投影 + v2 Canny 轮廓） |
| `id_card_workflow.py` | 三阶段工作流编排 + 裁切逻辑 |

---

## 九、用户操作全流程总结

```
$ python -m digital_human.cli id-card-workflow \
    --source-image samples/new_id_card.png \
    --input-video jobs-cloud/wlh-test/output/facefusion_result.mp4 \
    --output-video jobs-cloud/wlh-test/output/final_with_id_card.mp4

┌─────────────────────────────────────────────────┐
│  阶段 1/3：标记证件图片中的身份证区域              │
│                                                  │
│  [弹窗显示证件原图]                                │
│  用户拖拽框选 → 松开                               │
│                                                  │
│  AI 正在分析边缘... ✓ 检测到                      │
│                                                  │
│  [弹窗对比展示]                                   │
│  ┌──────────┐  ┌──────────┐                      │
│  │ 人工(绿)  │  │ AI(蓝)   │                      │
│  └──────────┘  └──────────┘                      │
│  [采用人工] [采用AI] [重新标注] [取消]              │
│                                                  │
│  → 用户点 [采用AI]                               │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│  阶段 2/3：标记视频中身份证的位置                   │
│                                                  │
│  [弹窗显示视频首帧]                                │
│  用户拖拽框选 → 松开                               │
│                                                  │
│  AI 正在分析边缘... ✓ 检测到                      │
│                                                  │
│  [弹窗对比展示]                                   │
│  [采用人工] [采用AI] [重新标注] [取消]              │
│                                                  │
│  → 用户点 [采用AI]                               │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│  阶段 3/3：执行视频中证件替换...                    │
│  处理帧: 150/150                                  │
│  ✅ 替换完成: final_with_id_card.mp4              │
└─────────────────────────────────────────────────┘
```

---

## 十、后续可扩展

1. **实时预览**：在对比窗口中直接显示替换效果（调用 `replace_id_card_in_frame` 渲染一帧）
2. **多点拖拽微调**：对比窗口中允许拖动四角点进行微调，而不只是二选一
3. **关键帧追踪**：阶段 B 标完首帧后，自动 KLT 追踪后续帧的四角
4. **手指保护区标注**：替换结果预览后，如果手指区域处理不好，可以补标保护区

---

## 十一、与现有方案的取舍

| 现有方案（v1） | 新方案（v2 交互式） |
|---|---|
| 用户直接标 4 个精确角点 | 用户拖矩形粗标，AI 辅助精修 |
| 无裁切步骤，source_image 整张贴 | 先裁切证件，只贴纯卡面区域 |
| CLI 分三步执行 | CLI 一步 workflow 完成 |
| 标注即最终结果 | 标注 + AI 对比 + 反复迭代 |

**v2 不替代 v1**。v1 的 CLI 分步模式仍然保留，适合自动化/批量场景。v2 是面向人工操作的新入口。