# 真人表演驱动数字人 Demo（LivePortrait 分支）

当前分支：`codex/liveportrait-performance-drive`。工作目录固定为 `G:\duikouxing2`，不会访问或修改正在执行 LatentSync 的 `G:\duikouxing`。

## 这个分支实现什么

客户提供一段人物手持广告牌、证书或身份证的视频。系统保留客户原视频的背景、身体、手部、物品、头部姿态和镜头，只做以下处理：

1. 从客户原视频提取参考声音，并按 `script` 生成客户音色的新话术音频。
2. 让操作者用 iPhone 跟随该音频录制一段正脸口播视频。
3. LivePortrait 从操作者视频提取口唇、脸颊和下颌表演，迁移到客户原视频。
4. 使用官方 stitching + paste-back 将生成脸部回贴到客户每一帧；不使用旧的椭圆嘴部 ROI。
5. 丢弃操作者视频声音，把客户音色的新话术音频封装为成片音轨。

现有 `main` 代码不支持 LivePortrait；本分支已经新增适配器、两阶段运行流程、配置、安装脚本、校验和测试。核心实现已写好，但必须先安装官方仓库与权重，才能进行真实 GPU 验证。

## 目录约定

```text
G:\duikouxing2\
├── samples\source.mp4                 # 客户测试视频
├── samples\driving.mov                # 你用 iPhone 跟录的视频（第二阶段放入）
├── config\job.local.yaml              # 本次任务配置
├── .conda-envs\                       # 全部 Conda 环境
├── .cache\ / .tmp\                   # 全部缓存和临时文件
├── models\dots.tts-soar\             # 客户音色模型
├── external\LivePortrait\
│   └── pretrained_weights\            # LivePortrait 全部权重
└── jobs-office\<job_id>\              # 公司电脑任务产物
    ├── input\recording_guide.wav       # 你要跟随的导读音频
    ├── input\recording_script.txt      # 你要说的话和录制要求
    ├── work\liveportrait_pasteback.mp4
    ├── logs\liveportrait.log
    └── output\final.mp4
```

## 第一次安装

要求：Windows 10/11、NVIDIA驱动、Conda、Git、FFmpeg/FFprobe。RTX 3060 12GB、RTX 4070 12GB均可运行；32GB内存足够。Python版本固定：编排和声音环境为 `3.11.9`，LivePortrait为 `3.10.13`。

```powershell
Set-Location G:\duikouxing2

# 安装编排、dots.tts和原项目兼容环境，全部prefix位于本项目。
.\scripts\setup_conda.ps1

# 只下载本方案需要的dots.tts-soar，不必下载MuseTalk权重。
.\scripts\download_voice_model.ps1

# 克隆固定提交并安装LivePortrait独立环境。
.\scripts\setup_liveportrait.ps1

# 下载权重到external\LivePortrait\pretrained_weights。
.\scripts\download_liveportrait_models.ps1
```

固定版本和下载源见[模型与许可证](docs/MODELS_AND_LICENSES.md)。不要手工把权重放到C盘缓存。

## 第一步：放入客户视频并生成导读

```powershell
New-Item -ItemType Directory -Force .\samples
Copy-Item <客户视频路径> .\samples\source.mp4
Copy-Item .\config\job.example.yaml .\config\job.local.yaml
```

编辑 `config\job.local.yaml`：

- `job_id`：每次素材或参数改变都换一个新值。
- `consent_confirmed: true`。
- `source_video: "../samples/source.mp4"`。
- `driving_video: null`，第一阶段必须先留空。
- `reference_audio: null`，表示从客户视频截取声音。
- `reference_start_seconds` / `reference_duration_seconds`：选择客户清晰、单人说话的10～20秒。
- `reference_text`：必须逐字填写上述参考片段真实说出的内容。
- `script`：填写最终替换话术；这也是你稍后必须跟读的话。

生成客户音色导读：

```powershell
# 公司RTX 3060
.\scripts\prepare_driving.ps1 -Profile office -Job .\config\job.local.yaml

# 家里RTX 4070时改成：-Profile home
```

成功后得到 `jobs-office\<job_id>\input\recording_guide.wav` 和 `recording_script.txt`。先完整试听导读；有错字、吞字或音色不对时不要录视频，先修正参考文字/片段或话术，并使用新 `job_id`。

## 第二步：你应该怎么录iPhone视频

你要说的内容就是 `recording_script.txt` 中的本次话术。不要凭自己的速度自由朗读；戴单边耳机播放 `recording_guide.wav`，逐字同步跟读或对口型。

- 推荐1080p、30fps，关闭电影模式、美颜、滤镜和HDR。
- 镜头固定，正面均匀补光；完整拍到头部和下颌，嘴部无遮挡。
- 第一帧正脸、闭嘴、自然表情；录制前后各留约0.5秒。
- 尽量不转头、不点头、不晃肩；自然带动嘴唇、脸颊和下颌。
- 录入声音不会进入成片，音质不重要，动作节奏非常重要。
- 总时长与导读相差超过12%会被拒绝；少量差异会自动整体微调。

```powershell
Copy-Item <iPhone视频路径> .\samples\driving.mov
```

然后把任务配置改为：

```yaml
driving_video: "../samples/driving.mov"
```

## 第三步：生成成片

```powershell
$Python = 'G:\duikouxing2\.conda-envs\digital-human\python.exe'
& $Python -m digital_human.cli doctor --profile office
.\scripts\run_job.ps1 -Profile office -Job .\config\job.local.yaml
```

成片是 `jobs-office\<job_id>\output\final.mp4`。家里使用 `home`，输出在 `jobs-home`。

首轮建议用5～10秒素材做 `exp@0.75`、`exp@0.85`、`exp@1.0`，再做 `lip@0.85` 对照。每组必须换 `job_id`。`exp` 会迁移自然的脸颊/下颌微动；`lip` 只作保守对照。禁止 `all`，代码会拒绝它，因为它可能迁移操作者头部姿态。

## 文档入口

- [项目架构书](docs/PROJECT_ARCHITECTURE.md)
- [开发清单](docs/DEVELOPMENT_CHECKLIST.md)
- [验收标准](docs/ACCEPTANCE.md)
- [安装细节](docs/INSTALLATION.md)
- [模型、权重和许可证](docs/MODELS_AND_LICENSES.md)
- [真人录制规范](docs/RECORDING_GUIDE.md)

## 当前边界

- 这是“视频表演驱动”，不是音频自动口型模型；没有真人驱动视频不能执行本后端。
- 全局时长修正不能修复逐字错位，因此必须跟着最终导读录制。
- 牙齿、舌头、极端张嘴、遮挡、快速转头仍可能失败。
- 本分支保留MuseTalk兼容后端，但LivePortrait路径不会调用旧ROI/composite。
- 测试素材可按当前约定本地处理；正式商用前仍需处理第三方模型许可证。
