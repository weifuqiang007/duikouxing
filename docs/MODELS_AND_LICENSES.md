# 模型、权重、依赖与许可证

以下是后续大模型和开发者必须遵守的唯一版本清单，不得猜测替换。

## dots.tts声音克隆

| 项 | 固定值 |
|---|---|
| Python包 | `dots.tts==0.3.1` |
| 质量权重 | Hugging Face `dots-studio/dots.tts-soar` |
| 本地目录 | `G:\duikouxing2\models\dots.tts-soar` |
| Python | 3.11.9 |
| PyTorch | 2.8.0 CUDA 12.8 wheel |
| 用途 | 从客户参考音频生成配置中的新话术 |

下载入口是 `scripts/download_voice_model.ps1`。本方案不依赖 `dots.tts-mf`；只有把机器配置改为fast时才需要另行下载该官方权重。

## LivePortrait动作迁移

| 项 | 固定值 |
|---|---|
| 官方仓库 | `https://github.com/KlingAIResearch/LivePortrait.git` |
| 固定提交 | `9b294b3d0536135442ea73cb01e6cb3ca7029dd3` |
| 权重仓库 | Hugging Face `KlingTeam/LivePortrait` |
| 代码目录 | `G:\duikouxing2\external\LivePortrait` |
| 权重目录 | `G:\duikouxing2\external\LivePortrait\pretrained_weights` |
| Python | 3.10.13 |
| PyTorch | 2.3.0 + cu121 |
| torchvision/torchaudio | 0.18.0 / 2.3.0 |

官方依赖由固定提交的 `requirements.txt` 和 `requirements_base.txt` 安装。关键固定包包括：

```text
numpy==1.26.4
opencv-python==4.10.0.84
scipy==1.13.1
imageio==2.34.2
scikit-image==0.24.0
albumentations==1.4.10
onnx==1.16.1
onnxruntime-gpu==1.18.0
transformers==4.38.0
tyro==0.8.5
pykalman==0.9.7
```

下载后必须存在：

```text
pretrained_weights/liveportrait/base_models/appearance_feature_extractor.pth
pretrained_weights/liveportrait/base_models/motion_extractor.pth
pretrained_weights/liveportrait/base_models/spade_generator.pth
pretrained_weights/liveportrait/base_models/warping_module.pth
pretrained_weights/liveportrait/retargeting_models/stitching_retargeting_module.pth
pretrained_weights/liveportrait/landmark.onnx
pretrained_weights/insightface/models/buffalo_l/det_10g.onnx
```

脚本会下载完整官方权重包，动物权重即使存在也不会被本项目调用。人类模式不需要编译X-Pose的 `MultiScaleDeformableAttention`。

## 固定调用参数

本项目通过官方 `inference.py` 调用以下能力：source video、driving video、`flag_relative_motion`、`animation_region=exp/lip`、`flag_stitching`、`flag_pasteback`、驱动自动裁剪。禁止启用 `flag_lip_retargeting`；禁止使用 `pose/all`。

## FFmpeg

用于音轨提取、响度归一、帧率/时长归一和最终封装。实际许可证取决于安装的构建参数，正式分发前记录 `ffmpeg -version`。

## 许可证结论

- LivePortrait仓库代码为MIT许可证。
- 官方LivePortrait许可证明确说明其使用的InsightFace模型只限非商业研究用途。
- 因此本分支可用于当前自有测试素材的Demo验证；不能把默认InsightFace权重直接视为已获商业授权。
- 正式商业交付前必须替换为具备明确商业授权的人脸检测/关键点组件，或另行取得所需许可，并做完整回归测试。
- dots.tts及其权重、FFmpeg构建、所有间接依赖也要保存下载日期、版本、LICENSE/NOTICE和SHA-256记录。

安全风险和许可证是两个不同问题。即使测试阶段不做脱敏，也不能删除这份依赖和许可证记录。
