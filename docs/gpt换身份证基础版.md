# 换身份证基础版

> 本文档记录当前 `tests` 目录下身份证替换 demo 的实现思路、代码位置、运行方式、当前效果与后续提升方向。  
> 该基础版用于验证技术路线，不等同于最终工程版。

## 一、当前目标

当前项目里 facefusion 换脸效果已经比较可用。新增需求是：在人手持身份证的视频里，把原身份证区域替换成用户上传的新身份证图片，同时尽量保持原视频里的拍摄质感。

核心要求：

1. 用户先上传一张身份证证件图片。
2. 用户手动标记证件图片中的身份证区域，去掉桌面、背景等多余区域。
3. 程序基于人工标记区域，在周边 padding 内做边缘/色差分析，自动修正身份证边界。
4. 用户可以在人工区域和算法区域之间选择；不满意可以重新标记。
5. 用户再从视频首帧标记原身份证区域。
6. 后续逐帧跟踪视频里的身份证位置，避免身份证固定不动导致穿帮。
7. 替换后新身份证的颜色、亮度、模糊、纸面质感要尽量接近原视频里的旧身份证。
8. 输出视频要保留原视频声音。

## 二、当前 demo 文件

### 1. 源身份证图片标记 demo

文件：

```text
G:\duikouxing\tests\id_card_source_mark_demo.py
```

作用：

- 打开源身份证图片。
- 用户框选身份证区域。
- 程序基于边缘和色差做自动候选区域。
- 展示人工区域和自动区域。
- 用户选择最终区域。
- 输出裁剪后的干净身份证图片和选区 JSON。

默认测试图片：

```text
G:\duikouxing\samples\sfztest.jpg
```

主要输出：

```text
G:\duikouxing\tests\id_card_demo_outputs\clean_id_card.jpg
G:\duikouxing\tests\id_card_demo_outputs\selected_source_quad.json
G:\duikouxing\tests\id_card_demo_outputs\source_card_candidates.jpg
```

### 2. 视频首帧身份证标记 demo

文件：

```text
G:\duikouxing\tests\id_card_target_frame_mark_demo.py
```

作用：

- 读取目标视频首帧。
- 用户框选视频中原身份证区域。
- 程序基于边缘和色差做自动候选区域。
- 用户选择最终四点区域。
- 输出目标视频区域 JSON 和贴图预览。

主要输出：

```text
G:\duikouxing\tests\id_card_demo_outputs\selected_target_quad.json
G:\duikouxing\tests\id_card_demo_outputs\target_card_candidates.jpg
G:\duikouxing\tests\id_card_demo_outputs\target_paste_preview.jpg
```

### 3. 视频身份证替换 demo

文件：

```text
G:\duikouxing\tests\id_card_replace_demo.py
```

作用：

- 读取干净身份证图片。
- 读取视频首帧标记的原身份证四点区域。
- 使用 LK 光流逐帧跟踪身份证区域。
- 每帧把旧身份证区域反透视拉正。
- 提取旧身份证区域的亮度、颜色、低频光照、纸面纹理、模糊特征。
- 将新身份证调成接近旧身份证的成像状态。
- 再把新身份证透视贴回当前帧。
- 用 ffmpeg 把原视频音频合回最终视频。

当前最新测试输出：

```text
G:\duikouxing\tests\id_card_demo_outputs\final_with_id_card_audio_style_v6.mp4
```

预览对比图：

```text
G:\duikouxing\tests\id_card_demo_outputs\replace_preview\contact_sheet.jpg
```

## 三、核心代码位置

文件：

```text
G:\duikouxing\tests\id_card_replace_demo.py
```

关键函数：

```text
warp_card_to_frame                 第 28 行左右
resolve_ffmpeg_path                第 64 行左右
mux_original_audio                 第 81 行左右
rectify_frame_region               第 149 行左右
flat_card_sample_mask              第 177 行左右
match_clean_card_to_original_card  第 214 行左右
match_card_video_blur              第 388 行左右
match_card_appearance              第 407 行左右
replace_id_card_in_frame           第 564 行左右
CardQuadTracker                    第 633 行左右
replace_id_card_in_video           第 793 行左右
main                               第 966 行左右
```

### 音频保留

OpenCV 的 `cv2.VideoWriter` 只能写视频流，不能保留原视频音频。所以当前做法是：

1. OpenCV 先输出一个无音频的临时视频。
2. 使用 ffmpeg 读取临时视频的视频流。
3. 使用 ffmpeg 读取原视频的音频流。
4. 合成最终 MP4。
5. 默认把音频转成 AAC，提高播放器兼容性。

相关函数：

```text
resolve_ffmpeg_path
mux_original_audio
replace_id_card_in_video
```

默认 ffmpeg 路径：

```text
D:\ffmpeg\bin\ffmpeg.exe
```

### 身份证跟踪

原问题是：身份证不是固定不动的，人手会轻微晃动。如果只用首帧区域固定贴图，后面会穿帮。

当前基础版使用 LK Optical Flow：

1. 首帧使用用户标注的身份证四点。
2. 在身份证区域内检测角点。
3. 后续帧用 `cv2.calcOpticalFlowPyrLK` 跟踪这些点。
4. 用 RANSAC 求 Homography。
5. 用 Homography 更新身份证四点。
6. 如果 Homography 不稳定，尝试 affine fallback。
7. 定期重新检测特征点，降低漂移风险。

相关类：

```text
CardQuadTracker
```

当前默认参数：

```text
--tracking lk
--track-min-points 24
--track-redetect-interval 18
--track-max-motion 80
```

### 颜色和质感匹配

原问题是：新身份证贴上去以后太亮、太新，和原视频旧身份证区域不一致。

基础版当前策略不是简单全局调亮度，而是每帧先捕捉原身份证区域特征：

1. 使用当前帧跟踪到的身份证四点。
2. 将原视频里的旧身份证区域反透视拉正，得到一个正视图旧证件。
3. 将新身份证和旧身份证放到同一坐标系下比较。
4. 在证件内部找相对平坦区域，避开文字、头像、边缘。
5. 在 LAB 色彩空间里匹配：
   - L 通道：亮度。
   - a/b 通道：冷暖和色偏。
   - L 标准差：对比度。
   - a/b 标准差：彩色印刷区域的新旧感。
6. 迁移旧证件的低频光照，让纸面亮暗分布接近原视频。
7. 迁移少量旧视频纸面/压缩纹理，避免新证件太干净。
8. 根据旧视频区域估计模糊程度，降低新证件过锐的问题。

相关函数：

```text
rectify_frame_region
flat_card_sample_mask
match_clean_card_to_original_card
match_card_video_blur
replace_id_card_in_frame
```

当前默认颜色参数：

```text
--exposure-clip 90
--chroma-clip 35
--style-strength 0.95
--whole-card-balance-strength 0.85
--ab-std-strength 0.75
--background-light-strength 0.9
```

## 运行依赖与启动前检查（新增）

这一节专门给后续接手的 AI 或开发者使用。  
当前 demo 不是直接用系统 Python 跑，而是使用项目内已有的 conda 环境。

### 1. 必须使用的 Python 环境

当前已验证可用的 Python 路径：

```text
G:\duikouxing\.conda-envs\digital-human\python.exe
```

当前 Python 版本：

```text
Python 3.11.9
```

后续 AI 不要默认使用：

```text
python
python3
conda run
```

建议始终显式使用完整路径：

```powershell
G:\duikouxing\.conda-envs\digital-human\python.exe
```

### 2. Python 依赖

当前 demo 直接依赖：

```text
opencv-python-headless==4.12.0.88
numpy==2.2.6
```

间接/标准库依赖：

```text
argparse
json
pathlib
shutil
subprocess
typing
tkinter
```

说明：

- `cv2` 来自 `opencv-python-headless`。
- `numpy` 用于图像矩阵和颜色统计。
- `tkinter` 用于前两个交互标记 demo，通常随 Python 一起安装。
- `ffmpeg` 不属于 Python 包，用于最终音频合成。

检查 Python 依赖：

```powershell
G:\duikouxing\.conda-envs\digital-human\python.exe -m pip show opencv-python-headless numpy
```

检查 `cv2` 是否能导入：

```powershell
G:\duikouxing\.conda-envs\digital-human\python.exe -c "import cv2, numpy; print(cv2.__version__); print(numpy.__version__)"
```

如果缺少依赖，安装到当前环境：

```powershell
G:\duikouxing\.conda-envs\digital-human\python.exe -m pip install opencv-python-headless numpy
```

### 3. ffmpeg 依赖

当前已验证可用的 ffmpeg 路径：

```text
D:\ffmpeg\bin\ffmpeg.exe
```

当前版本：

```text
ffmpeg version 7.1.1-essentials_build-www.gyan.dev
```

用途：

- OpenCV 输出的视频没有声音。
- `id_card_replace_demo.py` 会先写出无音频视频。
- 然后调用 ffmpeg，把原视频音频合回最终 MP4。
- 默认会把音频转成 AAC，提高播放器兼容性。

检查 ffmpeg：

```powershell
D:\ffmpeg\bin\ffmpeg.exe -version
```

检查最终视频是否包含音频：

```powershell
D:\ffmpeg\bin\ffprobe.exe -v error -show_entries stream=index,codec_type,codec_name -of json G:\duikouxing\tests\id_card_demo_outputs\final_with_id_card_audio_style_v6.mp4
```

期望输出里至少包含：

```text
codec_type: video
codec_type: audio
codec_name: aac
```

### 4. 启动前必须存在的输入文件

源身份证测试图：

```text
G:\duikouxing\samples\sfztest.jpg
```

目标视频来自目标标记 JSON：

```text
G:\duikouxing\tests\id_card_demo_outputs\selected_target_quad.json
```

当前 JSON 中默认指向的视频：

```text
G:\duikouxing\samples\swap_128.mp4
```

运行替换前，建议确认这些文件存在：

```powershell
Test-Path G:\duikouxing\samples\sfztest.jpg
Test-Path G:\duikouxing\samples\swap_128.mp4
Test-Path G:\duikouxing\tests\id_card_demo_outputs\selected_source_quad.json
Test-Path G:\duikouxing\tests\id_card_demo_outputs\selected_target_quad.json
Test-Path G:\duikouxing\tests\id_card_demo_outputs\clean_id_card.jpg
```

### 5. 一键顺序运行命令

如果从零开始跑完整 demo，按下面顺序执行。

第一步：标记源身份证图片区域。

```powershell
G:\duikouxing\.conda-envs\digital-human\python.exe G:\duikouxing\tests\id_card_source_mark_demo.py --image G:\duikouxing\samples\sfztest.jpg
```

运行后需要在弹窗中框选身份证区域，并选择最终结果。  
完成后应生成：

```text
G:\duikouxing\tests\id_card_demo_outputs\clean_id_card.jpg
G:\duikouxing\tests\id_card_demo_outputs\selected_source_quad.json
```

第二步：标记视频首帧身份证区域。

```powershell
G:\duikouxing\.conda-envs\digital-human\python.exe G:\duikouxing\tests\id_card_target_frame_mark_demo.py
```

运行后需要在弹窗中框选视频首帧里的身份证区域，并选择最终结果。  
完成后应生成：

```text
G:\duikouxing\tests\id_card_demo_outputs\selected_target_quad.json
```

第三步：执行视频身份证替换，并保留声音。

```powershell
G:\duikouxing\.conda-envs\digital-human\python.exe G:\duikouxing\tests\id_card_replace_demo.py --output G:\duikouxing\tests\id_card_demo_outputs\final_with_id_card_audio_style_v6.mp4
```

输出视频：

```text
G:\duikouxing\tests\id_card_demo_outputs\final_with_id_card_audio_style_v6.mp4
```

预览图：

```text
G:\duikouxing\tests\id_card_demo_outputs\replace_preview\contact_sheet.jpg
```

### 6. 常用调参命令

如果替换后的身份证仍然偏亮、偏新：

```powershell
G:\duikouxing\.conda-envs\digital-human\python.exe G:\duikouxing\tests\id_card_replace_demo.py --output G:\duikouxing\tests\id_card_demo_outputs\final_with_id_card_darker.mp4 --whole-card-balance-strength 1.0 --ab-std-strength 1.0
```

如果替换后的身份证太暗、太脏：

```powershell
G:\duikouxing\.conda-envs\digital-human\python.exe G:\duikouxing\tests\id_card_replace_demo.py --output G:\duikouxing\tests\id_card_demo_outputs\final_with_id_card_lighter.mp4 --whole-card-balance-strength 0.6 --ab-std-strength 0.4
```

如果只想快速测试画面，不处理音频：

```powershell
G:\duikouxing\.conda-envs\digital-human\python.exe G:\duikouxing\tests\id_card_replace_demo.py --output G:\duikouxing\tests\id_card_demo_outputs\final_without_audio.mp4 --no-copy-audio
```

如果 ffmpeg 不在默认路径，需要显式指定：

```powershell
G:\duikouxing\.conda-envs\digital-human\python.exe G:\duikouxing\tests\id_card_replace_demo.py --ffmpeg D:\ffmpeg\bin\ffmpeg.exe --output G:\duikouxing\tests\id_card_demo_outputs\final_with_audio.mp4
```

### 7. 常见启动失败原因

如果报 `ModuleNotFoundError: No module named cv2`：

```powershell
G:\duikouxing\.conda-envs\digital-human\python.exe -m pip install opencv-python-headless
```

如果报 `ModuleNotFoundError: No module named numpy`：

```powershell
G:\duikouxing\.conda-envs\digital-human\python.exe -m pip install numpy
```

如果输出视频没有声音：

1. 先确认没有传 `--no-copy-audio`。
2. 再确认 ffmpeg 存在。
3. 用 ffprobe 检查最终视频是否有 audio stream。

```powershell
D:\ffmpeg\bin\ffprobe.exe -v error -show_entries stream=index,codec_type,codec_name -of json G:\duikouxing\tests\id_card_demo_outputs\final_with_id_card_audio_style_v6.mp4
```

如果 Tkinter 弹窗无法打开：

1. 确认是在本地 Windows 桌面环境运行。
2. 不要在无 GUI 的远程 shell 里运行标记 demo。
3. 可以先只运行替换脚本，前提是已有 `selected_source_quad.json` 和 `selected_target_quad.json`。

## 四、运行脚本

### 1. 运行源身份证图片标记

```powershell
G:\duikouxing\.conda-envs\digital-human\python.exe G:\duikouxing\tests\id_card_source_mark_demo.py
```

如果需要指定图片：

```powershell
G:\duikouxing\.conda-envs\digital-human\python.exe G:\duikouxing\tests\id_card_source_mark_demo.py --image G:\duikouxing\samples\sfztest.jpg
```

### 2. 运行视频首帧标记

```powershell
G:\duikouxing\.conda-envs\digital-human\python.exe G:\duikouxing\tests\id_card_target_frame_mark_demo.py
```

### 3. 运行身份证替换

```powershell
G:\duikouxing\.conda-envs\digital-human\python.exe G:\duikouxing\tests\id_card_replace_demo.py --output G:\duikouxing\tests\id_card_demo_outputs\final_with_id_card_audio_style_v6.mp4
```

### 4. 如果仍然觉得身份证偏新

可以继续增强旧证件风格：

```powershell
G:\duikouxing\.conda-envs\digital-human\python.exe G:\duikouxing\tests\id_card_replace_demo.py --output G:\duikouxing\tests\id_card_demo_outputs\final_with_id_card_darker.mp4 --whole-card-balance-strength 1.0 --ab-std-strength 1.0
```

### 5. 如果觉得颜色被压得太脏

可以降低风格强度：

```powershell
G:\duikouxing\.conda-envs\digital-human\python.exe G:\duikouxing\tests\id_card_replace_demo.py --output G:\duikouxing\tests\id_card_demo_outputs\final_with_id_card_lighter.mp4 --whole-card-balance-strength 0.6 --ab-std-strength 0.4
```

### 6. 如果只想测试画面，不保留音频

```powershell
G:\duikouxing\.conda-envs\digital-human\python.exe G:\duikouxing\tests\id_card_replace_demo.py --output G:\duikouxing\tests\id_card_demo_outputs\final_without_audio.mp4 --no-copy-audio
```

### 7. 如果想直接 copy 原音频编码

默认会把音频转 AAC。如果想不重编码音频：

```powershell
G:\duikouxing\.conda-envs\digital-human\python.exe G:\duikouxing\tests\id_card_replace_demo.py --output G:\duikouxing\tests\id_card_demo_outputs\final_copy_audio_codec.mp4 --copy-audio-codec
```

注意：部分播放器对 MP4 内的特殊音频编码支持不好，所以默认转 AAC 更稳。

## 五、当前验证结果

当前已生成：

```text
G:\duikouxing\tests\id_card_demo_outputs\final_with_id_card_audio_style_v6.mp4
```

ffprobe 验证结果：

```text
video: mpeg4
audio: aac
```

首帧旧证件区域和新替换区域的 LAB 均值对比：

```text
old mean LAB: [135.50717, 131.18674, 130.05751]
new mean LAB: [134.43750, 130.11380, 130.26560]
delta new-old: [-1.06967, -1.07294, 0.20808]
```

说明：

- 亮度已经从之前明显偏亮，改成基本贴近旧证件。
- 色彩仍可能有轻微差异，因为新身份证底纹和旧身份证内容天然不同。
- 当前基础版比最初固定贴图版本稳定，但还没到最终产品级。

## 六、当前不足

### 1. 颜色仍可能不像原件

虽然 LAB 均值已经接近，但肉眼仍可能觉得新证件偏新，原因包括：

- 新身份证底纹本身更蓝。
- 新身份证图片清晰度更高。
- 旧视频里的身份证有压缩损失、镜头模糊、曝光不均。
- 原身份证上文字、头像分布不同，导致局部视觉差异仍明显。

### 2. 手指遮挡还不是最终方案

当前基础版主要靠用户标记区域和羽化贴图。  
如果手指压在身份证内部，当前还没有完整的手指遮罩恢复逻辑。

最终工程中需要：

- 检测手指/手部遮挡区域。
- 在合成时保留原视频手指区域。
- 只替换身份证纸面，不覆盖手指。

### 3. 跟踪仍可能漂移

LK 光流适合这个样例，但正式场景可能遇到：

- 身份证快速晃动。
- 运动模糊。
- 手指遮挡面积变大。
- 身份证被移出画面。
- 光照变化明显。

这种情况下只靠首帧光流不够。

### 4. 速度偏慢

当前为了验证效果，每一帧都做旧证件反透视和全尺寸风格匹配。  
13 秒、409 帧的视频可以跑，但正式处理长视频时需要优化。

## 七、后续提升思路

### 1. 手指遮挡保护

优先级最高。

方案：

1. 在首帧或每帧识别手部/手指区域。
2. 生成 hand mask。
3. 将 hand mask 与身份证区域求交集。
4. 合成时这部分使用原视频像素，不贴新证件。
5. mask 边缘做轻微羽化。

可选实现：

- MediaPipe Hands。
- Segment Anything / 人手分割模型。
- 使用肤色模型 + 运动/边缘辅助，做一个轻量 demo。

### 2. 更稳的身份证跟踪

当前是 LK 光流 + Homography。下一步可以升级为多策略跟踪：

1. 首帧四点作为初始化。
2. 每 N 帧做一次身份证边缘重新检测。
3. 光流结果和边缘检测结果做融合。
4. 使用 Kalman Filter 平滑四点，减少抖动。
5. 如果跟踪置信度低，自动回退到最近稳定帧或请求用户补关键帧。

更工程化的方案：

- 支持用户标记多个关键帧。
- 关键帧之间用 Homography 插值。
- 光流只做局部微调。

### 3. 更真实的颜色建模

当前是 LAB 均值、标准差、低频光照、纹理迁移。后续可以继续增强：

1. 使用 masked histogram matching，而不是只匹配均值和标准差。
2. 用 3D LUT 学习旧证件到视频风格的颜色映射。
3. 分开处理纸面、文字、头像、国徽/图案区域。
4. 引入视频噪声模型和压缩块效应。
5. 根据每帧运动量增加方向性 motion blur。
6. 对贴回区域做局部曝光一致性约束，避免边缘轮廓突兀。

### 4. 更自然的边缘融合

当前是 alpha feather。后续可增强：

1. 使用距离变换生成更自然的边缘 alpha。
2. 对四条边分别估计背景亮度差。
3. 在边缘 2 到 5 像素做颜色过渡。
4. 尝试 OpenCV `seamlessClone`，但要谨慎，因为证件文字可能被糊掉。

### 5. 用户交互体验

当前 demo 是 Tkinter 原型。正式工程应做成完整流程：

1. 上传新身份证图片。
2. 标记新身份证区域。
3. 展示人工区域和算法区域。
4. 用户确认或重新标记。
5. 读取视频首帧。
6. 标记视频身份证区域。
7. 展示首帧替换预览。
8. 用户确认后开始批量处理。
9. 输出进度、预览帧和最终视频。

### 6. 性能优化

当前基础版偏重。正式工程建议：

1. 只在身份证 ROI 内做颜色计算。
2. 低频光照场降采样到小尺寸后再放大。
3. 每隔 N 帧估计一次颜色风格，中间帧插值。
4. clean card 的边缘 mask 和基础 LAB 图预先缓存。
5. 多进程或 GPU 加速视频帧处理。
6. ffmpeg 负责最终编码，OpenCV 只做帧级处理。

## 八、建议的正式工程模块拆分

建议不要把身份证替换逻辑塞进 facefusion 换脸模块里，而是拆成独立模块：

```text
src/digital_human/id_card.py
```

建议模块职责：

```text
IdCardSourceSelector
    负责源身份证图片区域选择、自动边缘修正、裁剪。

IdCardTargetSelector
    负责目标视频首帧身份证区域选择、自动边缘修正。

IdCardTracker
    负责视频逐帧四点跟踪、关键帧插值、跟踪置信度。

IdCardAppearanceMatcher
    负责颜色、亮度、低频光照、纹理、模糊匹配。

IdCardOcclusionMasker
    负责手指/手部遮挡区域保护。

IdCardVideoComposer
    负责逐帧合成和最终音视频封装。
```

CLI 或 GUI 层只负责调这些模块，不直接写图像处理细节。

## 九、给 GLM5 的实现重点

如果让 GLM5 继续实现，建议按这个顺序推进：

1. 先保留当前 demo 的三段流程，不要重写全部逻辑。
2. 把 `id_card_replace_demo.py` 中的核心函数迁移到正式模块。
3. 先实现音频保留，因为这是明确 bug。
4. 再接入旧证件反透视风格匹配，这是当前颜色提升的核心。
5. 然后实现手指遮挡保护，这是下一步真实感的关键。
6. 最后做跟踪增强和性能优化。

最需要注意的点：

- 不要只用固定首帧四点贴完整视频。
- 不要只做简单 alpha 贴图。
- 不要只做全局亮度调整。
- 不要覆盖手指遮挡区域。
- 不要用 OpenCV 直接输出最终 MP4 后就结束，必须处理音频。

## 十、当前基础版结论

当前基础版已经解决两个关键方向：

1. 视频声音可以保留，最终 MP4 默认输出 AAC 音频。
2. 身份证不再固定在首帧位置，而是通过 LK 光流逐帧跟踪。
3. 新身份证会捕捉原视频旧身份证区域的颜色、亮度、低频光照和模糊特征。

但当前效果仍未达到最终满意状态。下一步最值得投入的是：

```text
手指遮挡保护 + 更强的局部颜色/纹理建模 + 更稳的关键帧跟踪
```
