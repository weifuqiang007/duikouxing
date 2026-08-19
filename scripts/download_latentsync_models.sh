#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LATENTSYNC_ROOT="${PROJECT_ROOT}/external/LatentSync"
LATENTSYNC_ENV="${PROJECT_ROOT}/.conda-envs/latentsync"
CACHE_ROOT="${PROJECT_ROOT}/.cache"

export HF_HOME="${CACHE_ROOT}/huggingface"
export HF_HUB_CACHE="${HF_HOME}/hub"
export TORCH_HOME="${CACHE_ROOT}/torch"
export XDG_CACHE_HOME="${CACHE_ROOT}"
export PIP_CACHE_DIR="${CACHE_ROOT}/pip"
export TMPDIR="${PROJECT_ROOT}/.tmp"

[[ -x "${LATENTSYNC_ENV}/bin/python" ]] || {
  echo "ERROR: run scripts/setup_cloud_4090.sh first" >&2
  exit 2
}
[[ -d "${LATENTSYNC_ROOT}/.git" ]] || {
  echo "ERROR: missing official LatentSync repository" >&2
  exit 2
}

mkdir -p "${LATENTSYNC_ROOT}/checkpoints" "${HF_HOME}" "${TMPDIR}"

# 完整官方权重约 9.64GB：UNet 5.07GB、SyncNet 1.61GB、Whisper 与人脸检测辅助权重。
# 注意：官方 requirements 固定 huggingface-hub==0.30.2，该版本只有 huggingface-cli 子命令。
conda run -p "${LATENTSYNC_ENV}" huggingface-cli download ByteDance/LatentSync-1.6 \
  --local-dir "${LATENTSYNC_ROOT}/checkpoints"

# 官方推理以 Hugging Face ID 加载 VAE；提前写入项目内缓存，避免任务运行时下载。
conda run -p "${LATENTSYNC_ENV}" huggingface-cli download stabilityai/sd-vae-ft-mse

# InsightFace FaceAnalysis 默认会在首次推理时隐式下载 buffalo_l。
# 在安装阶段显式下载官方 v0.7 model pack，保证任务阶段离线。
INSIGHTFACE_DIR="${LATENTSYNC_ROOT}/checkpoints/auxiliary/models/buffalo_l"
if [[ ! -s "${INSIGHTFACE_DIR}/det_10g.onnx" || ! -s "${INSIGHTFACE_DIR}/2d106det.onnx" ]]; then
  mkdir -p "${INSIGHTFACE_DIR}"
  curl --fail --location --retry 5 \
    "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip" \
    --output "${TMPDIR}/buffalo_l.zip"
  unzip -q -o "${TMPDIR}/buffalo_l.zip" -d "${INSIGHTFACE_DIR}"
fi

test -s "${LATENTSYNC_ROOT}/checkpoints/latentsync_unet.pt"
test -s "${LATENTSYNC_ROOT}/checkpoints/whisper/tiny.pt"
test -s "${INSIGHTFACE_DIR}/det_10g.onnx"
test -s "${INSIGHTFACE_DIR}/2d106det.onnx"

echo "LatentSync 1.6 models downloaded under ${LATENTSYNC_ROOT}/checkpoints"
