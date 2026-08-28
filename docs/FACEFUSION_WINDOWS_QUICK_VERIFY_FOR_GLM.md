# FaceFusion Windows 快速安装与验证手册

本文档给 GLM 执行使用。目标是在当前 Windows 电脑上快速跑通 FaceFusion，验证：

1. UI 能否启动。
2. RTX 3060 GPU 能否用于推理。
3. 能否用 5-10 秒短视频完成一次换脸。
4. 能否用 5-10 秒短视频和音频完成一次对口型。

## 当前机器条件

已确认电脑配置：

- 系统：Windows 11 专业版 64 位
- CPU：Intel i7-12700KF
- 内存：32GB
- GPU：NVIDIA GeForce RTX 3060 12GB
- NVIDIA 驱动：591.86
- CUDA 驱动显示：13.1
- Python：3.12.7
- FFmpeg：已安装
- Conda：已安装
- 项目目录：`G:\duikouxing`
- FaceFusion 源码目录：`G:\duikouxing\facefusion`

结论：硬件可以跑 FaceFusion，建议优先使用 NVIDIA GPU。

## 重要边界

FaceFusion 不是完整数字人系统。它主要适合：

- 换脸：把 A 视频中的脸换成 B 的脸。
- 对口型：让已有视频中人物的嘴型跟指定音频同步。
- 增强：脸部增强、画质增强、背景处理等。

它不等于“输入一张照片和一段音频，自动生成完整高保真人物视频”。如果要做公司员工复用形象念稿，最佳路线是：已有稳定视频底片 + 新音频 + lip sync。

涉及员工脸、声音、身份证画面时，必须确认授权。建议不要把手持身份证的视频作为长期模板素材。

## 官方资料

- GitHub 仓库：<https://github.com/facefusion/facefusion>
- 官方文档：<https://docs.facefusion.io>
- Windows 安装说明：<https://docs.facefusion.io/installation/accelerator/windows>

注意：官方 README 里的 Windows Installer 入口可能是付费安装器。若不使用付费安装器，按下面源码方式安装。

## 安装步骤

### 1. 确认源码目录

打开 PowerShell，执行：

```powershell
cd /d G:\duikouxing\facefusion
dir
```

如果目录不存在，重新下载：

```powershell
cd /d G:\duikouxing
git clone https://github.com/facefusion/facefusion.git facefusion
cd /d G:\duikouxing\facefusion
```

### 2. 创建独立 Conda 环境

```powershell
conda create -n facefusion python=3.12 pip -y
conda activate facefusion
```

确认环境：

```powershell
python --version
pip --version
```

预期 Python 为 3.12.x。

### 3. 安装 FaceFusion 依赖

当前机器 NVIDIA 驱动显示 CUDA 13.1，FaceFusion 安装脚本支持 `cuda@13`，优先尝试：

```powershell
cd /d G:\duikouxing\facefusion
python install.py cuda@13
```

如果 `cuda@13` 安装失败，再尝试 `cuda@12`：

```powershell
python install.py cuda@12 --force-reinstall
```

如果 CUDA 版本仍失败，最后才用 CPU/默认版本验证 UI：

```powershell
python install.py default --force-reinstall
```

CPU/默认版本能启动，但视频处理会慢很多，不建议作为最终方案。

## 启动 UI

```powershell
conda activate facefusion
cd /d G:\duikouxing\facefusion
python facefusion.py run
```

启动后观察终端输出中的本地地址，通常类似：

```text
http://127.0.0.1:7860
```

在浏览器打开该地址。

## 快速验证一：GPU 是否可用

启动 UI 后，在 FaceFusion 的执行设置里查看 `execution providers`，优先选择类似：

- CUDA
- CUDAExecutionProvider

如果只能看到 CPU，也可以先跑通 UI，但需要记录问题。

另外开一个 PowerShell 窗口执行：

```powershell
nvidia-smi
```

在 FaceFusion 正在处理视频时，观察 GPU 显存和利用率是否上升。

成功标准：

- UI 能打开。
- 处理视频时 `nvidia-smi` 里显存占用明显增加。
- GPU 利用率有波动。

## 快速验证二：换脸测试

准备素材：

- Source：职员 B 的清晰正脸照片，建议 1-3 张。
- Target：你用手机拍的一段 5-10 秒视频，正脸、光线稳定、少动作。

建议先用 720p 或更低分辨率短视频，不要一开始就上长视频。

FaceFusion 操作：

1. Source 选择 B 的照片。
2. Target 选择你的短视频。
3. Processor 选择 `face_swapper`。
4. 可选再加 `face_enhancer`。
5. 输出到 `G:\duikouxing\samples\facefusion_outputs`。

建议优先测试的模型：

- `inswapper_128`：速度快，先验证流程。
- `simswap_256` 或 `simswap_unofficial_512`：看高保真效果。
- `ghost_*`：可作为备选比较。

成功标准：

- 输出视频能正常播放。
- 脸部身份接近 B。
- 脸部边缘不明显闪烁。
- 嘴部、牙齿、眼睛没有严重变形。

## 快速验证三：对口型测试

准备素材：

- Target：员工 A 的稳定自拍视频，5-10 秒即可。
- Source audio：一段新稿子的音频，长度尽量和目标视频接近。

FaceFusion 操作：

1. Target 选择员工 A 的视频。
2. Source 选择新音频，或者选择带新音频的视频。
3. Processor 选择 `lip_syncer`。
4. 模型先试 `wav2lip_gan_96`，再试 `edtalk_256`。
5. 输出到 `G:\duikouxing\samples\facefusion_outputs`。

成功标准：

- 输出视频声音正常。
- 嘴型跟音频基本同步。
- 下巴和嘴唇区域没有明显糊、抖、撕裂。
- 面部身份仍像原员工 A。

## 建议的样片素材规范

为了更容易得到高保真结果，素材尽量满足：

- 正脸或轻微侧脸。
- 人脸占画面高度 20%-45%。
- 光线均匀，脸上不要有强阴影。
- 不要戴口罩，不要频繁遮挡嘴。
- 背景干净。
- 头部不要大幅转动。
- 视频帧率 25 或 30 fps。
- 先用 720p、5-10 秒测试，成功后再上 1080p 和长视频。

## 常见问题处理

### UI 启动失败

先确认环境：

```powershell
conda activate facefusion
cd /d G:\duikouxing\facefusion
python --version
pip list | findstr onnxruntime
python facefusion.py run
```

把完整报错保存下来。

### CUDA 依赖失败

优先重装对应 onnxruntime：

```powershell
conda activate facefusion
cd /d G:\duikouxing\facefusion
python install.py cuda@13 --force-reinstall
```

仍失败则改：

```powershell
python install.py cuda@12 --force-reinstall
```

### 显存不够

降低参数：

- 输入视频先降到 720p。
- 先只开一个 processor。
- 不要同时开 frame enhancer 和 face enhancer。
- 关闭其他占显存的软件。

当前 RTX 3060 12GB 正常应足够跑短视频验证。

### 输出脸不像

调整方向：

- 换更清晰的 source 照片。
- source 和 target 的角度、光线尽量接近。
- 多试几个 face swapper 模型。
- 增加 face enhancer，但不要过度增强。

### 对口型不自然

调整方向：

- 音频要干净，少噪声。
- 视频中原人物嘴部不要被遮挡。
- 音频长度尽量匹配目标视频长度。
- 分别测试 `wav2lip_gan_96` 和 `edtalk_256`。

## 卸载与清理

如果不好用，删除 Conda 环境：

```powershell
conda deactivate
conda env remove -n facefusion -y
```

删除源码目录：

```powershell
Remove-Item -LiteralPath G:\duikouxing\facefusion -Recurse -Force
```

如果只想保留源码、删除输出文件，可只清理：

```powershell
Remove-Item -LiteralPath G:\duikouxing\samples\facefusion_outputs -Recurse -Force
```

执行删除前请确认目录路径无误。

## 给 GLM 的最终交付要求

请完成后反馈以下内容：

1. 是否成功启动 FaceFusion UI。
2. UI 地址是多少。
3. 是否识别到 CUDA/GPU。
4. `nvidia-smi` 中是否看到 FaceFusion 处理时占用 GPU。
5. 换脸 5-10 秒样片是否成功。
6. 对口型 5-10 秒样片是否成功。
7. 哪个模型效果最好。
8. 遇到的完整报错和解决步骤。

建议最终输出两个样片：

- `G:\duikouxing\samples\facefusion_outputs\swap_test.mp4`
- `G:\duikouxing\samples\facefusion_outputs\lipsync_test.mp4`
