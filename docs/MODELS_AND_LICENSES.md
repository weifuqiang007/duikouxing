# 模型、权重、依赖与许可证清单

## 1. dots.tts

| 项 | 值 |
|---|---|
| 官方仓库 | `https://github.com/studio-dots-ai/dots.tts` |
| Python 包 | `dots.tts==0.3.1` |
| 质量权重 | `dots-studio/dots.tts-soar` |
| 快速权重 | `dots-studio/dots.tts-mf` |
| 项目固定 Python | 3.11.9 |
| 许可证 | Apache-2.0 |

官方固定依赖摘要：

```text
torch==2.8.0
torchaudio==2.8.0
transformers==4.57.0
librosa==0.11.0
soundfile==0.13.1
numpy==2.2.6
pydantic==2.12.5
PyYAML==6.0.3
safetensors==0.8.0rc0
gradio==6.17.0
```

本项目只使用声音克隆，不使用随机声音采样。

本项目固定存储路径：

```text
G:\duikouxing\models\dots.tts-soar
G:\duikouxing\models\dots.tts-mf
G:\duikouxing\.cache\huggingface
```

## 2. MuseTalk 1.5

| 项 | 值 |
|---|---|
| 官方仓库 | `https://github.com/TMElyralab/MuseTalk` |
| 固定提交 | `0a89dec45a0192b824e3cf4daf96c239440c5ed8` |
| 主权重 | `TMElyralab/MuseTalk` 中 `musetalkV15/*` |
| 项目固定 Python | 3.10.14 |
| PyTorch | 2.0.1 + cu118 wheel |
| 代码许可 | MIT |
| 官方模型说明 | 允许商用，但依赖模型需分别遵守许可证 |

本项目固定仓库和权重路径为：

```text
G:\duikouxing\external\MuseTalk
G:\duikouxing\external\MuseTalk\models
```

官方 Python 依赖：

```text
diffusers==0.30.2
accelerate==0.28.0
numpy==1.23.5
tensorflow==2.12.0
tensorboard==2.12.0
opencv-python==4.9.0.80
soundfile==0.12.1
transformers==4.39.2
huggingface_hub==0.30.2
librosa==0.11.0
einops==0.8.1
gradio==5.24.0
gdown
requests
imageio[ffmpeg]
omegaconf
ffmpeg-python
moviepy
```

MMLab 依赖：

```text
mmengine
mmcv==2.0.1
mmdet==3.1.0
mmpose==1.1.0
```

## 3. MuseTalk 辅助权重

| 用途 | Hugging Face/来源 | 必需文件 |
|---|---|---|
| SD VAE | `stabilityai/sd-vae-ft-mse` | `config.json`, `diffusion_pytorch_model.bin` |
| Whisper | `openai/whisper-tiny` | `config.json`, `pytorch_model.bin`, `preprocessor_config.json` |
| DWPose | `yzd-v/DWPose` | `dw-ll_ucoco_384.pth` |
| SyncNet | `ByteDance/LatentSync` | `latentsync_syncnet.pt` |
| Face parser | `ManyOtherFunctions/face-parse-bisent`/官方脚本 | `79999_iter.pth`, `resnet18-5c106cde.pth` |

下载文件清单以 MuseTalk 固定提交中的 `download_weights.bat`/`.sh` 为唯一事实来源。本项目脚本在独立下载环境执行相同的 Hugging Face 仓库和文件清单，避免官方批处理升级 MuseTalk 运行环境依赖。其他大模型不得自行更换仓库或文件名。

## 4. FFmpeg

- 用途：解封装、转帧率、音频抽取、响度处理、循环、封装。
- 许可证取决于下载的构建选项。正式分发前应记录所用 FFmpeg 构建的 `ffmpeg -version` 和许可证配置。

## 5. 商用前必须复核

MuseTalk 主项目说明其代码和模型可商用，但同时明确要求遵守 Whisper、SD VAE、DWPose、Face Parse、SyncNet 等第三方依赖许可。正式商业交付前必须由项目负责人保留：

- 每个仓库的固定提交或模型 revision。
- 每个 LICENSE/NOTICE 副本。
- 权重下载日期与 SHA-256。
- 人物肖像、声音和证件处理授权。

本项目不引入 InsightFace 默认模型包，因为其开源模型权重存在非商业研究限制；如未来使用，必须单独取得商业许可。
