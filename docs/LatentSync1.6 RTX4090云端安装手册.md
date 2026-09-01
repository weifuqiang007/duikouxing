# LatentSync 1.6 / RTX 4090 云端安装手册

本手册仅适用于 `codex/latentsync-1.6-cloud` 分支的 Ubuntu 22.04 云服务器。

## 1. 实例规格

```text
RTX 4090 24GB
64GB RAM
8～16 vCPU
100～150GB 可用磁盘
Ubuntu 22.04
NVIDIA 驱动可运行 CUDA 12.1 wheel
```

## 2. 系统前置

```bash
sudo apt-get update
sudo apt-get install -y git git-lfs ffmpeg libgl1 build-essential curl unzip
git lfs install
nvidia-smi
ffmpeg -version
```

云镜像必须已安装 Conda/Miniconda，并且 `conda` 在 `PATH` 中。

## 3. 获取项目

```bash
git clone <your-repository-url> duikouxing
cd duikouxing
git switch codex/latentsync-1.6-cloud
git rev-parse --abbrev-ref HEAD
```

项目路径不应包含空格。

## 4. 创建环境

```bash
bash scripts/setup_cloud_4090.sh
```

该脚本会：

1. 创建 Python 3.11.9 编排环境。
2. 创建 Python 3.11.9 dots.tts 环境。
3. 创建 Python 3.10.13 LatentSync 环境。
4. 克隆官方 `bytedance/LatentSync`。
5. 固定到 `a229c3948406bc2cf6eaf4873e662e70c6a04746`。
6. 安装官方 `requirements.txt`。
7. 应用 `patches/latentsync-1.6-quality-mux.patch`。

## 5. 下载 LatentSync 权重

```bash
bash scripts/download_cloud_models.sh
```

下载完成后至少存在：

```text
external/LatentSync/checkpoints/latentsync_unet.pt
external/LatentSync/checkpoints/stable_syncnet.pt
external/LatentSync/checkpoints/whisper/tiny.pt
external/LatentSync/checkpoints/auxiliary/
external/LatentSync/checkpoints/auxiliary/models/buffalo_l/det_10g.onnx
external/LatentSync/checkpoints/auxiliary/models/buffalo_l/2d106det.onnx
.cache/huggingface/
```

不要手工改权重文件名。

## 6. dots.tts 权重

上一步 `download_cloud_models.sh` 已同时下载：

```text
models/dots.tts-soar/
models/dots.tts-mf/
```

如果仅上传本地已生成的 `base_duration_matched.mp4` 和 `target_normalized.wav`，
可以在后续增加 lipsync-only 任务入口；当前完整 CLI 仍需要 dots.tts 权重。

## 7. 环境检查

```bash
conda run -p .conda-envs/digital-human \
  python -m digital_human.cli doctor --profile cloud
```

必须确认：

- GPU 名包含 `RTX 4090`。
- LatentSync Torch 可见 CUDA。
- 官方仓库提交完全匹配。
- UNet 和 Whisper 权重存在。
- dots.tts 环境可见 CUDA。

## 8. 任务配置

```bash
cp config/job.example.yaml config/job.cloud.yaml
```

必须修改：

```yaml
job_id: "customer-test-001"
consent_confirmed: true
local_only: false
source_video: "../samples/source.mp4"
reference_audio: "../samples/reference.wav"
reference_text: "与参考音频逐字一致"
script: "新话术"
```

保持：

```yaml
lipsync:
  engine: "latentsync_1_6"
  inference_steps: 30
  guidance_scale: 1.3
  seed: 1247
  enable_deepcache: false

composite:
  mode: "native"
```

## 9. 运行

```bash
bash scripts/run_job.sh config/job.cloud.yaml
```

配置变更但复用同一 `job_id` 时：

```bash
bash scripts/run_job.sh config/job.cloud.yaml --force
```

产物：

```text
jobs-cloud/<job_id>/output/final.mp4
jobs-cloud/<job_id>/manifest.json
jobs-cloud/<job_id>/logs/latentsync.log
```

## 10. 首轮 A/B

不要直接跑 30 秒完整视频。先准备同一 3～5 秒素材，依次测试：

```yaml
# A
inference_steps: 20
guidance_scale: 1.3

# B
inference_steps: 30
guidance_scale: 1.3

# C
inference_steps: 30
guidance_scale: 1.2

# D
inference_steps: 40
guidance_scale: 1.3
```

每次使用新 `job_id`，避免缓存产物混淆。

## 11. 常见问题

### 缺少 VAE/尝试连网

重跑：

```bash
bash scripts/download_cloud_models.sh
```

### 高画质 mux 补丁未应用

重跑：

```bash
bash scripts/setup_cloud_4090.sh
```

不要手工删除 Adapter 中的补丁校验。

### 嘴部闪烁

1. `guidance_scale: 1.3` 降到 `1.2`。
2. 保持 seed 不变重跑。
3. 检查源视频是否有快速转头/遮挡。

### OOM

1. 确认没有其他 GPU 进程。
2. 确认任务使用 FP16 且没有其他 GPU 进程。
3. 仍 OOM 则使用 32/48GB 实例，不降到 256 分辨率。
