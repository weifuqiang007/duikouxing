# GPT 书写：替换身份证方案

> 日期：2026-08-28  
> 目标读者：后续实现代码的 GLM5 / 工程实现者  
> 说明：本文只响应用户当前需求。`身份证证件替换—最终方案.md` 可作为背景参考，但其中“已实现”“测试通过”等描述不能当作当前代码事实直接相信，必须以仓库实际代码为准。

## 1. 用户这次真正想要的流程

项目运行阶段，程序已经拿到了：

- 一张用户上传的身份证/证件图片；
- 一个待处理视频，通常是 FaceFusion 换脸后的结果视频；
- 用户希望通过弹窗交互完成证件区域定位和替换。

关键点不是“用户一次性精确标四角就结束”，而是：

1. 用户先在上传图片里粗略标出身份证区域，因为图片可能拍到了桌面、背景、边缘杂物。
2. 程序根据用户粗标区域，在周围一定 padding 范围内自动寻找更准确的证件边缘。
3. 程序同时展示“人工标记区域”和“算法计算区域”。
4. 用户可以选择：
   - 用人工区域；
   - 用算法区域；
   - 都不满意，重新标记。
5. 直到用户满意，得到一张已经去掉桌面背景、被透视矫正后的“干净证件图”。
6. 然后用户在视频某一帧，默认首帧，标出视频中的身份证区域。
7. 视频帧中的区域同样走“人工粗标 -> 算法找边 -> 双结果展示 -> 用户确认/重标”的闭环。
8. 最后把干净证件图贴到视频中的身份证位置，并做亮度、颜色、曝光匹配。

这一版的核心思想是：**人负责给大致范围，代码负责算边缘，最后由人确认。**  
这样比完全手工更准，也比完全自动更稳。

## 2. 为什么这个方向合理

身份证/证件区域有几个非常有利的特点：

- 它是一个近似平面，适合用透视变换 `warpPerspective`。
- 它和桌面、衣服、手指、背景通常有明显色差。
- 它的边缘通常是直线，适合用 Canny、轮廓、Hough 或四边形拟合。
- 它在视频里通常比较稳定，不像嘴和脸那样有复杂形变。
- 手指遮挡部分不需要生成或替换，直接保留视频原像素反而更真实。

因此不需要生成式模型，也不应该让 AI 重画身份证。最稳的是传统图像处理：

```text
人工粗选 + 边缘检测 + 色差分割 + 四边形拟合 + 用户确认 + 透视贴图 + 局部色彩匹配
```

## 3. 最终产品流程

```text
启动证件替换任务
  ↓
读取上传的证件图片
  ↓
弹窗 A：用户粗略圈出图片里的身份证
  ↓
代码在粗选区域周围 padding 内自动找证件边缘
  ↓
弹窗 B：展示 人工区域 vs 算法区域 vs 矫正预览
  ↓
用户选择：人工 / 算法 / 重新标记
  ↓
得到干净、矫正后的证件图
  ↓
读取视频首帧或指定帧
  ↓
弹窗 C：用户粗略圈出视频中的身份证
  ↓
代码在视频帧粗选区域周围 padding 内自动找证件边缘
  ↓
弹窗 D：展示 人工区域 vs 算法区域 vs 贴回预览
  ↓
用户选择：人工 / 算法 / 重新标记
  ↓
可选：用户标注手指保护区
  ↓
逐帧替换视频中的身份证
  ↓
输出最终视频
```

## 4. 建议新增文件

建议不要直接把逻辑塞进 FaceFusion 目录。FaceFusion 继续负责换脸，证件替换作为后处理。

新增：

```text
src/digital_human/id_card_replace.py
src/digital_human/id_card_annotate.py
tests/test_id_card_replace.py
docs/gpt书写替换身份证方案.md
```

也可以先只建一个 `src/digital_human/id_card_replace.py`，等逻辑稳定后再拆。

## 5. 配置结构建议

```yaml
id_card_replacement:
  enabled: true

  # 用户上传的证件图片，可能包含桌面背景。
  source_image: "samples/uploaded_id_card_photo.jpg"

  # FaceFusion 换脸之后的视频。
  input_video: "jobs/facefusion_result.mp4"

  # 最终输出。
  output_video: "jobs/final_with_replaced_id_card.mp4"

  # 图片内证件区域，由交互确认后写入。
  source_card_quad:
    mode: "auto"       # manual 或 auto
    points:
      - [0.1200, 0.1800]
      - [0.8800, 0.1600]
      - [0.9000, 0.7400]
      - [0.1000, 0.7600]

  # 视频中证件区域，由交互确认后写入。
  target_card_quad:
    frame_index: 0
    mode: "auto"       # manual 或 auto
    points:
      - [0.2800, 0.5600]
      - [0.7600, 0.5700]
      - [0.7700, 0.8500]
      - [0.2700, 0.8400]

  # 可选。手指遮挡区域保留视频原像素。
  protect_polygons:
    - name: "finger_left"
      points:
        - [0.2900, 0.6100]
        - [0.3500, 0.6100]
        - [0.3600, 0.7300]
        - [0.3000, 0.7350]

  detection:
    padding_ratio: 0.12
    expected_aspect_ratio: 1.585
    aspect_ratio_tolerance: 0.35
    canny_low: 40
    canny_high: 120
    min_area_ratio: 0.20

  blend:
    feather_pixels: 2
    color_mode: "lab_local"
    exposure_clip: 25
    chroma_clip: 10
    shadow_transfer: true
```

说明：

- `source_card_quad` 是上传图片里的证件四边形，用于把证件从桌面背景中裁出来。
- `target_card_quad` 是视频帧里的证件四边形，用于贴回视频。
- `points` 都用归一化坐标 `[0, 1]`，避免分辨率变化后失效。
- 中国居民身份证真实宽高比约为 `85.6 / 54 = 1.585`，可作为弱约束，不要作为绝对强约束，因为拍摄有透视。

## 6. 交互设计

### 6.1 图片证件区域确认

弹窗显示上传图片。

用户操作：

- 左键依次点四个角，顺序为：左上、右上、右下、左下。
- 或者拖一个粗略矩形，程序把矩形转成四角。
- 右键撤销上一个点。
- Enter 进入算法修正。
- Esc 取消。

算法修正后展示三块内容：

```text
左：原图 + 人工标记四边形
中：原图 + 算法计算四边形
右：算法区域透视矫正后的干净证件图
```

用户选择：

- `1` 使用人工区域；
- `2` 使用算法区域；
- `R` 重新标记；
- `Esc` 取消。

### 6.2 视频证件区域确认

弹窗显示视频第 0 帧，后续允许 `--at-seconds` 指定时间。

流程和图片一致：

```text
人工粗标视频证件区域
  -> 算法在 padding 范围内自动找边
  -> 展示人工/算法结果
  -> 用户确认或重标
```

视频中还要额外展示“贴回预览”：

```text
左：视频帧 + 人工四边形
中：视频帧 + 算法四边形
右：用干净证件图贴回后的预览帧
```

### 6.3 手指保护区

首版建议保留手动标注：

- 用户在视频帧上圈出手指遮挡区域。
- 替换时这些区域永远用视频原像素。

后续可以加自动辅助：

- 在目标证件区域内做肤色/色差检测，得到疑似手指 mask。
- 展示给用户确认。
- 用户可选择采用、忽略或手动补充。

首版不要强依赖自动手指检测，因为光照、肤色、证件底纹和压缩噪声都可能误判。

## 7. 自动寻找证件边缘的思路

输入：

- 原图或视频帧 `image`
- 用户粗标四边形 `manual_quad`
- 参数 `padding_ratio`

输出：

- 算法修正四边形 `auto_quad`
- 置信分数 `score`
- 调试图 `debug_image`

核心步骤：

1. 根据人工区域计算 bounding box。
2. 在 bounding box 外扩 padding，得到局部搜索区域。
3. 在搜索区域内转 LAB / 灰度。
4. 做边缘检测：
   - Canny；
   - 形态学闭运算连接断边；
   - 找外轮廓；
   - `approxPolyDP` 拟合四边形。
5. 做色差分割：
   - 取人工区域中心部分作为“证件底色”参考；
   - LAB 距离明显不同的区域视为背景或手指；
   - 找最大稳定卡片区域。
6. 合并候选：
   - 边缘候选四边形；
   - 色差候选四边形；
   - 人工四边形。
7. 按评分选最佳四边形。

评分建议：

```text
score =
  0.35 * 和人工区域的 IoU
+ 0.25 * 四条边的 Canny 边缘强度
+ 0.20 * 面积合理性
+ 0.10 * 宽高比接近身份证比例
+ 0.10 * 四边形凸性/角度合理性
```

注意：算法只是辅助，不要替用户做最终决定。

## 8. 核心代码草案

下面是思路级核心代码，不要求原样复制；实现时应按当前项目风格拆分、加测试、处理异常。

### 8.1 数据结构

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np


Point = tuple[float, float]
Quad = list[Point]


@dataclass(frozen=True)
class QuadCandidate:
    points: Quad
    score: float
    source: str  # manual / edge / color


@dataclass(frozen=True)
class IdCardReplaceConfig:
    source_image: Path
    input_video: Path
    output_video: Path
    source_card_quad: Quad
    target_card_quad: Quad
    protect_polygons: list[list[Point]]
    detection: dict[str, Any]
    blend: dict[str, Any]
```

### 8.2 归一化坐标转换

```python
def denormalize_points(points: Quad, width: int, height: int) -> np.ndarray:
    return np.array(
        [[x * (width - 1), y * (height - 1)] for x, y in points],
        dtype=np.float32,
    )


def normalize_points(points: np.ndarray, width: int, height: int) -> Quad:
    return [
        (round(float(x) / max(width - 1, 1), 6), round(float(y) / max(height - 1, 1), 6))
        for x, y in points
    ]
```

### 8.3 点排序

```python
def order_quad_points(points: np.ndarray) -> np.ndarray:
    """返回顺序：左上、右上、右下、左下。"""
    pts = np.asarray(points, dtype=np.float32).reshape(4, 2)
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).reshape(-1)

    top_left = pts[np.argmin(s)]
    bottom_right = pts[np.argmax(s)]
    top_right = pts[np.argmin(diff)]
    bottom_left = pts[np.argmax(diff)]
    return np.array([top_left, top_right, bottom_right, bottom_left], dtype=np.float32)
```

### 8.4 从粗标区域生成搜索 ROI

```python
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
```

### 8.5 边缘候选

```python
def find_edge_quad_candidates(
    image_bgr: np.ndarray,
    manual_quad_px: np.ndarray,
    options: dict[str, Any],
) -> list[QuadCandidate]:
    h, w = image_bgr.shape[:2]
    x0, y0, x1, y1 = padded_roi_from_quad(
        manual_quad_px,
        w,
        h,
        float(options.get("padding_ratio", 0.12)),
    )
    roi = image_bgr[y0:y1, x0:x1]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)

    edges = cv2.Canny(
        gray,
        int(options.get("canny_low", 40)),
        int(options.get("canny_high", 120)),
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[QuadCandidate] = []

    image_area = float(w * h)
    min_area_ratio = float(options.get("min_area_ratio", 0.20))
    manual_area = abs(cv2.contourArea(manual_quad_px.astype(np.float32)))

    for contour in contours:
        area = cv2.contourArea(contour)
        if area < manual_area * min_area_ratio:
            continue

        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.03 * peri, True)
        if len(approx) != 4:
            rect = cv2.minAreaRect(contour)
            approx = cv2.boxPoints(rect).reshape(-1, 1, 2)

        if len(approx) != 4:
            continue

        pts = approx.reshape(4, 2).astype(np.float32)
        pts[:, 0] += x0
        pts[:, 1] += y0
        pts = order_quad_points(pts)

        score = score_quad_candidate(image_bgr, pts, manual_quad_px, options)
        candidates.append(
            QuadCandidate(
                points=normalize_points(pts, w, h),
                score=score,
                source="edge",
            )
        )

    return sorted(candidates, key=lambda item: item.score, reverse=True)
```

### 8.6 色差候选

```python
def find_color_quad_candidate(
    image_bgr: np.ndarray,
    manual_quad_px: np.ndarray,
    options: dict[str, Any],
) -> QuadCandidate | None:
    h, w = image_bgr.shape[:2]
    x0, y0, x1, y1 = padded_roi_from_quad(
        manual_quad_px,
        w,
        h,
        float(options.get("padding_ratio", 0.12)),
    )
    roi = image_bgr[y0:y1, x0:x1]
    lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB).astype(np.float32)

    local_manual = manual_quad_px.copy().astype(np.float32)
    local_manual[:, 0] -= x0
    local_manual[:, 1] -= y0

    mask = np.zeros(roi.shape[:2], dtype=np.uint8)
    cv2.fillPoly(mask, [local_manual.astype(np.int32)], 255)

    mx, my, mw, mh = cv2.boundingRect(local_manual.astype(np.int32))
    cx0 = max(0, mx + int(mw * 0.35))
    cy0 = max(0, my + int(mh * 0.35))
    cx1 = min(roi.shape[1], mx + int(mw * 0.65))
    cy1 = min(roi.shape[0], my + int(mh * 0.65))

    center = lab[cy0:cy1, cx0:cx1]
    if center.size == 0:
        return None

    ref = np.median(center.reshape(-1, 3), axis=0)
    dist = np.linalg.norm(lab - ref[None, None, :], axis=2)

    inside_dist = dist[mask > 0]
    if inside_dist.size < 100:
        return None

    threshold = max(18.0, float(np.percentile(inside_dist, 80)) * 1.6)

    # 证件底色区域：距离参考色较近。这里会避开桌面、深色背景和部分手指。
    card_like = np.where(dist <= threshold, 255, 0).astype(np.uint8)
    card_like = cv2.bitwise_and(card_like, cv2.dilate(mask, np.ones((7, 7), np.uint8)))

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    card_like = cv2.morphologyEx(card_like, cv2.MORPH_CLOSE, kernel, iterations=2)
    card_like = cv2.morphologyEx(card_like, cv2.MORPH_OPEN, kernel, iterations=1)

    contours, _ = cv2.findContours(card_like, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < abs(cv2.contourArea(local_manual)) * 0.35:
        return None

    rect = cv2.minAreaRect(contour)
    pts = cv2.boxPoints(rect).astype(np.float32)
    pts[:, 0] += x0
    pts[:, 1] += y0
    pts = order_quad_points(pts)

    return QuadCandidate(
        points=normalize_points(pts, w, h),
        score=score_quad_candidate(image_bgr, pts, manual_quad_px, options),
        source="color",
    )
```

### 8.7 四边形评分

```python
def score_quad_candidate(
    image_bgr: np.ndarray,
    quad_px: np.ndarray,
    manual_quad_px: np.ndarray,
    options: dict[str, Any],
) -> float:
    iou = polygon_iou(quad_px, manual_quad_px, image_bgr.shape[1], image_bgr.shape[0])
    edge_strength = average_edge_strength_on_quad(image_bgr, quad_px)
    aspect_score = id_card_aspect_score(
        quad_px,
        float(options.get("expected_aspect_ratio", 1.585)),
        float(options.get("aspect_ratio_tolerance", 0.35)),
    )
    convex_score = 1.0 if cv2.isContourConvex(quad_px.astype(np.float32)) else 0.0

    return (
        0.40 * iou
        + 0.30 * edge_strength
        + 0.20 * aspect_score
        + 0.10 * convex_score
    )
```

辅助函数：

```python
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
    edges = cv2.Canny(gray, 40, 120)
    samples = []
    pts = quad_px.astype(np.float32)
    for i in range(4):
        p0 = pts[i]
        p1 = pts[(i + 1) % 4]
        for t in np.linspace(0, 1, 80):
            p = p0 * (1 - t) + p1 * t
            x = int(round(p[0]))
            y = int(round(p[1]))
            if 0 <= x < edges.shape[1] and 0 <= y < edges.shape[0]:
                samples.append(edges[y, x] / 255.0)
    return float(np.mean(samples)) if samples else 0.0
```

### 8.8 人工 + 算法候选统一入口

```python
def propose_card_quad(
    image_bgr: np.ndarray,
    manual_quad: Quad,
    options: dict[str, Any],
) -> tuple[QuadCandidate, list[QuadCandidate]]:
    h, w = image_bgr.shape[:2]
    manual_px = denormalize_points(manual_quad, w, h)
    manual_candidate = QuadCandidate(points=manual_quad, score=0.60, source="manual")

    candidates = [manual_candidate]
    candidates.extend(find_edge_quad_candidates(image_bgr, manual_px, options))

    color_candidate = find_color_quad_candidate(image_bgr, manual_px, options)
    if color_candidate:
        candidates.append(color_candidate)

    candidates = sorted(candidates, key=lambda item: item.score, reverse=True)
    return candidates[0], candidates
```

### 8.9 裁出干净证件图

```python
def rectify_card_image(
    image_bgr: np.ndarray,
    quad: Quad,
    *,
    output_width: int = 856,
    output_height: int = 540,
) -> np.ndarray:
    h, w = image_bgr.shape[:2]
    src = denormalize_points(quad, w, h)
    src = order_quad_points(src)
    dst = np.array(
        [
            [0, 0],
            [output_width - 1, 0],
            [output_width - 1, output_height - 1],
            [0, output_height - 1],
        ],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(
        image_bgr,
        matrix,
        (output_width, output_height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
```

### 8.10 把干净证件图贴到视频帧

```python
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
    dst = denormalize_points(target_quad, frame_w, frame_h)
    dst = order_quad_points(dst)

    matrix = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(card_bgr, matrix, (frame_w, frame_h), flags=cv2.INTER_CUBIC)

    src_mask = np.full((card_h, card_w), 255, dtype=np.uint8)
    mask = cv2.warpPerspective(src_mask, matrix, (frame_w, frame_h), flags=cv2.INTER_NEAREST)
    return warped, mask
```

### 8.11 手指保护 mask

```python
def build_protect_mask(
    frame_shape: tuple[int, int, int],
    protect_polygons: list[list[Point]],
) -> np.ndarray:
    h, w = frame_shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    for polygon in protect_polygons:
        if len(polygon) < 3:
            continue
        pts = denormalize_points(polygon, w, h).astype(np.int32)
        cv2.fillPoly(mask, [pts], 255)
    return mask
```

### 8.12 LAB 局部颜色匹配

```python
def match_card_color_lab(
    frame_bgr: np.ndarray,
    warped_card_bgr: np.ndarray,
    replace_mask: np.ndarray,
    options: dict[str, Any],
) -> np.ndarray:
    exposure_clip = float(options.get("exposure_clip", 25))
    chroma_clip = float(options.get("chroma_clip", 10))
    erode_px = int(options.get("sample_erode_pixels", 4))

    sample = replace_mask.copy()
    if erode_px > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (erode_px * 2 + 1, erode_px * 2 + 1))
        sample = cv2.erode(sample, kernel)

    if int((sample > 0).sum()) < 500:
        return warped_card_bgr

    src_lab = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    card_lab = cv2.cvtColor(warped_card_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)

    idx = sample > 0
    src_mean = src_lab[idx].mean(axis=0)
    card_mean = card_lab[idx].mean(axis=0)
    delta = src_mean - card_mean
    delta[0] = np.clip(delta[0], -exposure_clip, exposure_clip)
    delta[1] = np.clip(delta[1], -chroma_clip, chroma_clip)
    delta[2] = np.clip(delta[2], -chroma_clip, chroma_clip)

    card_lab += delta[None, None, :]

    if bool(options.get("shadow_transfer", True)):
        low_src = cv2.GaussianBlur(src_lab, (0, 0), 25)
        low_card = cv2.GaussianBlur(card_lab, (0, 0), 25)
        low_delta = np.clip(low_src - low_card, -12, 12)
        card_lab += low_delta * (replace_mask[..., None].astype(np.float32) / 255.0)

    return cv2.cvtColor(np.clip(card_lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
```

### 8.13 单帧替换

```python
def replace_id_card_in_frame(
    frame_bgr: np.ndarray,
    clean_card_bgr: np.ndarray,
    *,
    target_quad: Quad,
    protect_polygons: list[list[Point]],
    blend_options: dict[str, Any],
) -> np.ndarray:
    warped_card, card_mask = warp_card_to_frame(clean_card_bgr, frame_bgr.shape, target_quad)
    protect_mask = build_protect_mask(frame_bgr.shape, protect_polygons)

    replace_mask = cv2.bitwise_and(card_mask, cv2.bitwise_not(protect_mask))
    corrected_card = match_card_color_lab(frame_bgr, warped_card, replace_mask, blend_options)

    feather = int(blend_options.get("feather_pixels", 2))
    alpha = replace_mask.astype(np.float32) / 255.0
    if feather > 0:
        kernel_size = feather * 2 + 1
        alpha = cv2.GaussianBlur(alpha, (kernel_size, kernel_size), 0)

    alpha3 = alpha[..., None]
    result = frame_bgr.astype(np.float32) * (1.0 - alpha3) + corrected_card.astype(np.float32) * alpha3
    return np.clip(result, 0, 255).astype(np.uint8)
```

### 8.14 视频替换

```python
def replace_id_card_in_video(config: IdCardReplaceConfig) -> Path:
    uploaded = read_image_unicode(config.source_image)
    clean_card = rectify_card_image(uploaded, config.source_card_quad)

    cap = cv2.VideoCapture(str(config.input_video))
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频: {config.input_video}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    config.output_video.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(config.output_video),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        cap.release()
        raise RuntimeError(f"无法创建输出视频: {config.output_video}")

    written = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            out = replace_id_card_in_frame(
                frame,
                clean_card,
                target_quad=config.target_card_quad,
                protect_polygons=config.protect_polygons,
                blend_options=config.blend,
            )
            writer.write(out)
            written += 1
    finally:
        cap.release()
        writer.release()

    if written == 0:
        raise RuntimeError("证件替换没有产生任何帧")
    if total > 0 and abs(written - total) > 1:
        raise RuntimeError(f"输出帧数异常: written={written}, expected={total}")

    return config.output_video
```

### 8.15 支持中文路径的图片读取

```python
def read_image_unicode(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"无法读取图片: {path}")
    return image
```

## 9. CLI 命令建议

第一阶段可以先做三个命令：

```powershell
# 1. 标注并确认上传图片里的身份证区域
python -m digital_human.cli annotate-source-id-card `
  --image samples/uploaded_id_card_photo.jpg `
  --job config/job.local.yaml

# 2. 标注并确认视频帧里的身份证区域
python -m digital_human.cli annotate-target-id-card `
  --video jobs/facefusion_result.mp4 `
  --job config/job.local.yaml `
  --at-seconds 0

# 3. 执行替换
python -m digital_human.cli replace-id-card `
  --job config/job.local.yaml
```

第二阶段再加：

```powershell
# 标注手指保护区
python -m digital_human.cli annotate-id-card-protect `
  --video jobs/facefusion_result.mp4 `
  --job config/job.local.yaml `
  --at-seconds 0 `
  --name left_finger
```

## 10. 预览图要求

为了让用户确认，至少输出这些预览：

```text
source_card_candidates.jpg
  左：上传原图 + 人工框
  中：上传原图 + 算法框
  右：矫正后的干净证件图

target_card_candidates.jpg
  左：视频帧 + 人工框
  中：视频帧 + 算法框
  右：贴回预览

final_contact_sheet.jpg
  取第 1 帧、中间帧、末帧和变化较大的帧，展示替换效果
```

## 11. 测试要求

先用合成图片测试，不要用真实身份证。

必须覆盖：

1. 人工粗框周围 padding 内能找到更准确的四边形。
2. 算法找不到边缘时能回退人工框。
3. `rectify_card_image` 能去掉图片中桌面背景。
4. `warp_card_to_frame` 能把干净证件图贴到目标四边形。
5. `protect_polygons` 内像素保持视频原像素。
6. 替换区域外像素保持视频原像素。
7. LAB 匹配能把过亮证件压到接近原视频亮度。
8. `exposure_clip` 能限制亮度修正幅度。
9. 非法四边形、点数不足、退化区域会报错。
10. 输出视频帧数、FPS、尺寸与输入一致。

## 12. 我的建议实现顺序

1. 先实现 `rectify_card_image`：解决上传图片里带桌面背景的问题。
2. 再实现 `propose_card_quad`：人工框和算法框双候选。
3. 再做弹窗确认闭环：人工 / 算法 / 重标。
4. 再实现视频帧目标区域确认。
5. 再实现单帧替换。
6. 再实现视频逐帧替换。
7. 最后加手指保护区标注和颜色匹配优化。

这个顺序的好处是每一步都能单独看效果，避免一口气写完整条链路后不知道问题出在标注、裁卡、贴图还是调色。

## 13. 和上一版方案的主要区别

上一版方案更偏向：

```text
用户已经有干净证件图 -> 标视频四角 -> 贴回
```

这次用户真实想法是：

```text
上传的证件图片也可能不干净
  -> 先对上传图片做交互裁卡和算法修边
  -> 再对视频帧做交互定位和算法修边
  -> 两边都确认满意后再替换
```

所以这版要把“图片端证件区域提取”作为一等功能，而不是假设上传图片已经是干净证件。

## 14. 隐私和安全边界

- 不做 OCR。
- 不读取、打印、保存身份证号码文本。
- 不把真实身份证图片提交到 Git。
- 日志只记录文件名、尺寸、帧数、四边形坐标、算法分数，不记录证件内容。
- 本功能只用于授权视频编辑和内容制作，不能用于身份核验、开户、贷款、签约或冒充本人。

