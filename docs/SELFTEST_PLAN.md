# 本人素材自测流程（LivePortrait 表演驱动）

目的：用操作者本人录制的源视频和驱动视频，端到端验证本分支的真人驱动口型流水线，不涉及任何第三方素材。公司（RTX 3060）和家里（RTX 4070）流程完全相同，只有 `-Profile` 参数不同。

## 0. 环境准备（新机器一次性）

Git 只包含代码和配置；conda 环境、LivePortrait 仓库、全部权重都在 gitignore 中，新机器必须重跑安装脚本：

```powershell
Set-Location G:\duikouxing2
git pull

.\scripts\setup_conda.ps1                  # 编排 + dots.tts 环境（Python 3.11.9）
.\scripts\download_voice_model.ps1         # dots.tts-soar 声音权重
.\scripts\setup_liveportrait.ps1           # LivePortrait 仓库 + 环境（Python 3.10.13）
.\scripts\download_liveportrait_models.ps1 # LivePortrait 全部权重
```

要求：NVIDIA 驱动、Conda、Git、FFmpeg/FFprobe 已安装。跑完做体检：

```powershell
$Python = 'G:\duikouxing2\.conda-envs\digital-human\python.exe'
& $Python -m digital_human.cli doctor --profile home   # 公司用 office
```

## 1. 启用自测配置

`job.local.yaml` 被 gitignore（本地任务配置不入库），从被跟踪的自测模板复制：

```powershell
Copy-Item .\config\job.selftest.yaml .\config\job.local.yaml
```

自测配置中两个固定句子**不要改动**（`reference_text` 必须与源视频逐字一致）：

- 参考句（录进源视频）：`我是魏富强，今天是八月十八号。这段视频用来做声音参考，测试真人驱动口型流程。画面里的背景和动作都不重要，重要的是吐字清晰。`
- 新话术（跟导读录制）：`我是魏富强，这是一段测试话术，用来验证口型迁移的效果，说完这句就结束了。`

## 2. 录源视频（自己扮演"客户"）

**说参考句**，一个字不能差。手机固定、正脸、均匀光、1080p，总时长约 18 秒（开头闭嘴 0.5 秒 → 从容说完约 14 秒 → 结尾自然停 2 秒）。存为：

```powershell
Copy-Item <源视频路径> .\samples\source.mp4
```

## 3. 生成导读

```powershell
.\scripts\prepare_driving.ps1 -Profile home -Job .\config\job.local.yaml   # 公司用 office
```

得到 `jobs-home\selftest-001\input\recording_guide.wav`（公司为 `jobs-office\...`）。**必须先完整试听**：音色不像、有错字吞字时不要录驱动视频。

## 4. 录 iPhone 驱动视频

**说的是新话术那句**，不是参考句。戴单边耳机循环播放导读，逐字跟随导读的每个字和停顿动嘴：

- 轻声跟读或无声对口型均可——iPhone 音轨会被丢弃，只有嘴部动作节奏重要。
- 总时长与导读相差 12% 以内会被自动整体校正；抢拍、拖拍、漏字无法修复，感觉错了整段重录。
- 开头闭嘴正脸 0.5 秒，结束闭嘴 0.5 秒；不转头不点头；1080p/30fps，关美颜滤镜 HDR。
- 详细规范见 [RECORDING_GUIDE.md](RECORDING_GUIDE.md)。

保留 iPhone 原始 MOV（不要微信转发压缩），复制为：

```powershell
Copy-Item <iPhone视频路径> .\samples\driving.mov
```

## 5. 生成成片

把 `config\job.local.yaml` 中的：

```yaml
driving_video: "../samples/driving.mov"
```

然后：

```powershell
$Python = 'G:\duikouxing2\.conda-envs\digital-human\python.exe'
& $Python -m digital_human.cli doctor --profile home
.\scripts\run_job.ps1 -Profile home -Job .\config\job.local.yaml
```

成片：`jobs-home\selftest-001\output\final.mp4`（公司为 `jobs-office\...`）。

## 6. 判断靠不靠谱

对照 [ACCEPTANCE.md](ACCEPTANCE.md)，连续 10 秒以上片段在 1 倍速和 0.25 倍速各检查一遍：

- 背景、身体、手持物未被重绘；头部姿态沿用源视频，不跟随操作者转头。
- 身份稳定（脸型/眼眉没有变成操作者）；无贴片感、矩形边界、颜色断层、磨皮块。
- 嘴唇、脸颊、下颌连续；无连续 2 帧以上人脸丢失、黑块、嘴部冻结。
- 牙齿和口腔无熔化、重复牙齿、漂浮边缘。
- 口型：爆破音、闭口音、大开口元音与声音主观一致；持续提前/滞后不超过约 80ms；停顿时嘴回到闭口。

### A/B 参数对照（可选，验证通过后做）

复制 `job.selftest.yaml` 为新配置，每组换 `job_id`，只改 `performance_drive` 两项：

| 组名 | job_id | animation_region | driving_multiplier |
|---|---|---|---|
| 保守 | selftest-exp075 | exp | 0.75 |
| 默认 | selftest-001 | exp | 0.85 |
| 全幅 | selftest-exp100 | exp | 1.0 |
| 对照 | selftest-lip085 | lip | 0.85 |

### 已知边界（失败不代表流程不通）

牙齿/舌头细节、极端张大嘴、嘴部遮挡、快速转头仍可能失败，见 [readme](../readme.md) 当前边界一节。性能预期：RTX 3060 / RTX 4070 12GB FP16 处理 10 秒 1280 素材不 OOM。

## 7. 排障

- 任何一步失败，先看对应 job 目录下 `logs\`（如 `jobs-home\selftest-001\logs\liveportrait.log`）。
- 定位顺序见 [ACCEPTANCE.md](ACCEPTANCE.md) 末节：先确认是否真正跟随导读，再查驱动首帧是否正脸闭嘴。
