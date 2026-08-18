# 安装部署手册

项目根目录是 `G:\duikouxing2`。公司RTX 3060 12GB和家庭RTX 4070 12GB分别独立安装；两台机器不需要联网互访。所有相对路径随仓库移动，不允许把环境、权重或缓存放到C盘。

## 固定环境

| 环境 | 项目内路径 | Python | 主要用途 |
|---|---|---:|---|
| 编排 | `.conda-envs/digital-human` | 3.11.9 | CLI、配置、FFmpeg编排 |
| dots.tts | `.conda-envs/dots-tts` | 3.11.9 | 客户声音克隆 |
| LivePortrait | `.conda-envs/liveportrait` | 3.10.13 | 真人表演驱动和paste-back |
| MuseTalk兼容 | `.conda-envs/musetalk` | 3.10.14 | 旧后端，不参与本方案 |

环境不能合并。LivePortrait固定使用PyTorch 2.3.0、torchvision 0.18.0、torchaudio 2.3.0的CUDA 12.1 wheel。

## 系统依赖

- Windows 10/11、当前稳定NVIDIA驱动。
- Miniconda或Anaconda、Git。
- FFmpeg和FFprobe在PATH中。
- 建议项目盘预留至少60GB；保留旧MuseTalk全套权重时建议100GB。

```powershell
nvidia-smi
conda --version
git --version
ffmpeg -version
ffprobe -version
```

## 安装顺序

```powershell
Set-Location G:\duikouxing2
.\scripts\setup_conda.ps1
.\scripts\download_voice_model.ps1
.\scripts\setup_liveportrait.ps1
.\scripts\download_liveportrait_models.ps1
```

`setup_conda.ps1` 建立编排和声音环境，并安装当前项目为editable包；它也保留旧MuseTalk环境以兼容原功能。`setup_liveportrait.ps1` 克隆官方仓库、校验remote、固定提交并安装独立依赖。两个下载脚本只把文件放入本仓库。

如果代码更新后编排环境已经存在，执行：

```powershell
conda run -p .\.conda-envs\digital-human pip install -e ".[dev]"
```

## 项目内存储

运行脚本覆盖以下变量：

```text
HF_HOME=<项目>/.cache/huggingface
HF_HUB_CACHE=<项目>/.cache/huggingface/hub
TORCH_HOME=<项目>/.cache/torch
PIP_CACHE_DIR=<项目>/.cache/pip
XDG_CACHE_HOME=<项目>/.cache
TEMP=<项目>/.tmp
TMP=<项目>/.tmp
```

权重位置：

```text
models/dots.tts-soar
external/LivePortrait/pretrained_weights/liveportrait
external/LivePortrait/pretrained_weights/insightface
```

## 验证

```powershell
conda run -p .\.conda-envs\digital-human python --version
conda run -p .\.conda-envs\dots-tts python --version
conda run -p .\.conda-envs\liveportrait python --version

.\.conda-envs\digital-human\python.exe `
  -m digital_human.cli doctor --profile office --backend liveportrait
```

家庭电脑把 `office` 换成 `home`。doctor检查FFmpeg、FFprobe、dots.tts CUDA、LivePortrait CUDA、当前GPU型号、选中的SOAR权重、全部关键LivePortrait权重和官方提交。

## 3060、4070与4090

- 3060/4070均设置 `use_half_precision: true`、`source_max_dim: 1280`，一次只跑一个任务。
- 出现黑块而非OOM时，先设置 `use_half_precision: false`复测。
- 12GB显存出现OOM时先将 `source_max_dim`降至960；不要混装或升级依赖。
- 4090 24GB可以使用相同代码和环境；新增机器配置时复制local yaml，修改GPU字符串和任务目录即可。

## 网络失败处理

脚本默认使用 `https://hf-mirror.com`，也允许运行前设置自己的 `HF_ENDPOINT`。权重脚本下载结束后逐文件检查；失败时可重跑，Hugging Face下载器会续传。不要下载来源不明的一键整合包。

## 禁止事项

- 不在正在执行任务的 `G:\duikouxing` 中运行本分支脚本。
- 不使用 `conda create -n` 把大型环境建立到默认系统目录。
- 不手工改变LivePortrait仓库提交。
- 不把`pretrained_weights`移动到其他目录后用软链接指向C盘。
- 不用微信压缩iPhone驱动视频。
