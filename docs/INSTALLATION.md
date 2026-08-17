# 安装部署手册

项目根目录为本仓库所在目录（本机为 `E:\duikouxing`），不得位于 C 盘；配置中的相对路径均相对 `config/` 目录解析，换盘部署时无需修改。公司 RTX 3060 12GB 与家庭 RTX 4070 12GB 分别独立安装，不假设两台电脑能联网互访。

## 1. 固定版本

| 环境 | 路径 | Python |
|---|---|---|
| 编排 | `E:\duikouxing\.conda-envs\digital-human` | 3.11.9 |
| dots.tts | `E:\duikouxing\.conda-envs\dots-tts` | 3.11.9 |
| MuseTalk | `E:\duikouxing\.conda-envs\musetalk` | 3.10.14 |

不得将三个环境合并。

## 2. 项目内存储规则

安装和运行脚本强制设置：

```text
HF_HOME=E:\duikouxing\.cache\huggingface
HF_HUB_CACHE=E:\duikouxing\.cache\huggingface\hub
TORCH_HOME=E:\duikouxing\.cache\torch
PIP_CACHE_DIR=E:\duikouxing\.cache\pip
XDG_CACHE_HOME=E:\duikouxing\.cache
TEMP=E:\duikouxing\.tmp
TMP=E:\duikouxing\.tmp
```

dots.tts 权重放在：

```text
E:\duikouxing\models\dots.tts-soar
E:\duikouxing\models\dots.tts-mf
```

MuseTalk 为保持官方目录结构，放在：

```text
E:\duikouxing\external\MuseTalk\models
```

以上全部位于 `E:\duikouxing` 下，不使用 C 盘默认模型缓存。

## 3. 系统依赖

两台电脑分别安装：

- 当前稳定 NVIDIA 驱动。
- Miniconda/Anaconda。
- Git。
- FFmpeg，并确保 `ffmpeg`、`ffprobe` 在 PATH。
- G 盘建议至少保留 100GB，批量生产建议 200GB 以上。

检查：

```powershell
nvidia-smi
conda --version
git --version
ffmpeg -version
ffprobe -version
```

## 4. 一键建立 Conda 环境

```powershell
Set-Location E:\duikouxing
.\scripts\setup_conda.ps1
```

脚本执行内容：

1. 根据 `environments/*.yml` 创建三个前缀环境。
2. 在编排环境安装本项目与测试依赖。
3. 在 dots.tts 环境安装 `dots.tts==0.3.1`。
4. 将 MuseTalk 官方仓库克隆到 `external/MuseTalk`。
5. 固定 MuseTalk 提交 `0a89dec45a0192b824e3cf4daf96c239440c5ed8`。
6. 按官方要求安装 PyTorch 2.0.1、cu118 wheel 和 MMLab 组件。

dots.tts 环境按照 PyTorch 官方历史版本命令安装 `torch==2.8.0`、`torchaudio==2.8.0` 的 CUDA 12.8 wheel，并使用项目内的 `constraints/dots-tts-recommended.txt` 固定官方推荐依赖。两台电脑都需要较新的 NVIDIA 驱动以支持该 CUDA runtime。

安装后验证 Python 版本：

```powershell
conda run -p E:\duikouxing\.conda-envs\digital-human python --version
conda run -p E:\duikouxing\.conda-envs\dots-tts python --version
conda run -p E:\duikouxing\.conda-envs\musetalk python --version
```

预期分别为 3.11.9、3.11.9、3.10.14。

## 5. 下载模型

```powershell
Set-Location E:\duikouxing
.\scripts\download_models.ps1
```

脚本下载：

- `dots-studio/dots.tts-soar`。
- `dots-studio/dots.tts-mf`。
- MuseTalk 1.5 主权重。
- SD VAE。
- Whisper Tiny。
- DWPose。
- SyncNet。
- Face Parse BiSeNet 和 ResNet18。

MuseTalk 权重文件名和来源严格对应固定提交中的官方 `download_weights.bat`，但由独立下载环境逐项执行；不会让官方批处理在 MuseTalk 运行环境中升级 `huggingface_hub`，从而避免破坏固定依赖。

完成后生成：

```text
E:\duikouxing\model-checksums.json
```

该文件记录全部权重 SHA-256、文件大小和绝对路径。

## 6. 公司电脑配置

配置文件：[local.office.yaml](../config/local.office.yaml)

```yaml
runtime:
  expected_gpu: "RTX 3060"
  gpu_id: 0
  musetalk_batch_size: 2
  use_float16: true
  tts_profile: "quality"
```

检查：

```powershell
E:\duikouxing\.conda-envs\digital-human\python.exe `
  -m digital_human.cli doctor --profile office
```

如果稳定，可人工将 batch 调到 4；出现 OOM 则保持 2，不能通过升级/混装依赖解决。

## 7. 家庭电脑配置

配置文件：[local.home.yaml](../config/local.home.yaml)

```yaml
runtime:
  expected_gpu: "RTX 4070"
  gpu_id: 0
  musetalk_batch_size: 4
  use_float16: true
  tts_profile: "quality"
```

检查：

```powershell
E:\duikouxing\.conda-envs\digital-human\python.exe `
  -m digital_human.cli doctor --profile home
```

两张卡显存都是 12GB，4070 的主要优势是速度，不应因为型号较新就使用超过显存的模型。

## 8. 配置切换

推荐通过脚本显式指定：

```powershell
.\scripts\run_job.ps1 -Profile office -Job .\config\job.local.yaml
.\scripts\run_job.ps1 -Profile home -Job .\config\job.local.yaml
```

也可以设置当前终端默认值：

```powershell
$env:DIGITAL_HUMAN_PROFILE = "office"
# 或
$env:DIGITAL_HUMAN_PROFILE = "home"
```

任务输出分别存入 `jobs-office` 和 `jobs-home`，避免两台电脑交换文件时覆盖同名任务。

## 9. 首次验证

1. 使用 10 秒、不含真实证件的视频。
2. 运行 `doctor`。
3. 生成 ROI 预览并人工确认。
4. 运行完整任务。
5. 通过验收后再处理真实授权素材。

## 10. 禁止事项

- 禁止使用默认 `conda create -n` 将大环境建立在系统盘。
- 禁止将模型下载到 `%USERPROFILE%\.cache`。
- 禁止合并 dots.tts 与 MuseTalk 环境。
- 禁止擅自升级 MuseTalk 的 PyTorch/NumPy。
- 禁止使用来源不明的一键整合包。
- 禁止上传真实身份证到 Hugging Face Space、Colab 或云端 Demo。
