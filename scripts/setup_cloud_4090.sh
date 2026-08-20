#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_ROOT="${PROJECT_ROOT}/.conda-envs"
CACHE_ROOT="${PROJECT_ROOT}/.cache"
EXTERNAL_ROOT="${PROJECT_ROOT}/external"
LATENTSYNC_ROOT="${EXTERNAL_ROOT}/LatentSync"
LATENTSYNC_COMMIT="a229c3948406bc2cf6eaf4873e662e70c6a04746"

export HF_HOME="${CACHE_ROOT}/huggingface"
export HF_HUB_CACHE="${HF_HOME}/hub"
export TORCH_HOME="${CACHE_ROOT}/torch"
export XDG_CACHE_HOME="${CACHE_ROOT}"
export PIP_CACHE_DIR="${CACHE_ROOT}/pip"
export TMPDIR="${PROJECT_ROOT}/.tmp"

mkdir -p "${ENV_ROOT}" "${CACHE_ROOT}" "${EXTERNAL_ROOT}" "${TMPDIR}"

command -v conda >/dev/null || { echo "ERROR: conda not found" >&2; exit 2; }
command -v git >/dev/null || { echo "ERROR: git not found" >&2; exit 2; }
command -v ffmpeg >/dev/null || { echo "ERROR: ffmpeg not found" >&2; exit 2; }

ORCHESTRATOR_ENV="${ENV_ROOT}/digital-human"
DOTS_ENV="${ENV_ROOT}/dots-tts"
LATENTSYNC_ENV="${ENV_ROOT}/latentsync"

if [[ ! -x "${ORCHESTRATOR_ENV}/bin/python" ]]; then
  conda create -y -p "${ORCHESTRATOR_ENV}" python=3.11.9 pip=24.2
fi
conda run -p "${ORCHESTRATOR_ENV}" python -m pip install -e "${PROJECT_ROOT}[dev]"

# 声音链路与口型链路严格分离，不得把 torch/transformers 依赖混装。
if [[ ! -x "${DOTS_ENV}/bin/python" ]]; then
  conda create -y -p "${DOTS_ENV}" python=3.11.9 pip=24.2
fi
conda run -p "${DOTS_ENV}" python -m pip install \
  torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
conda install -y -p "${DOTS_ENV}" -c conda-forge pynini
conda run -p "${DOTS_ENV}" python -m pip install "dots.tts==0.3.1" --no-deps
conda run -p "${DOTS_ENV}" python -m pip install \
  transformers huggingface-hub loguru "langcodes[data]" einops librosa soundfile \
  numpy pydantic PyYAML safetensors torchdiffeq tqdm lingua-language-detector \
  WeTextProcessing accelerate tensorboard \
  -c "${PROJECT_ROOT}/constraints/dots-tts-recommended.txt"

if [[ ! -d "${LATENTSYNC_ROOT}/.git" ]]; then
  git clone https://github.com/bytedance/LatentSync.git "${LATENTSYNC_ROOT}"
fi
REMOTE_URL="$(git -C "${LATENTSYNC_ROOT}" remote get-url origin)"
[[ "${REMOTE_URL}" == *"bytedance/LatentSync"* ]] || {
  echo "ERROR: external/LatentSync is not the official repository: ${REMOTE_URL}" >&2
  exit 2
}
git -C "${LATENTSYNC_ROOT}" fetch origin
git -C "${LATENTSYNC_ROOT}" checkout "${LATENTSYNC_COMMIT}"

if ! grep -q -- "-c:v copy" \
  "${LATENTSYNC_ROOT}/latentsync/pipelines/lipsync_pipeline.py"; then
  git -C "${LATENTSYNC_ROOT}" apply \
    "${PROJECT_ROOT}/patches/latentsync-1.6-quality-mux.patch"
fi

# 口型幅度旋钮：job yaml 的 lipsync.audio_amp 依赖本补丁（默认 1.0 = 官方原行为）。
if ! grep -q "LATENTSYNC_AUDIO_AMP" \
  "${LATENTSYNC_ROOT}/latentsync/whisper/audio2feature.py"; then
  git -C "${LATENTSYNC_ROOT}" apply \
    "${PROJECT_ROOT}/patches/latentsync-audio-amplitude.patch"
fi

if [[ ! -x "${LATENTSYNC_ENV}/bin/python" ]]; then
  conda create -y -p "${LATENTSYNC_ENV}" python=3.10.13 pip=24.3.1
fi
conda run -p "${LATENTSYNC_ENV}" python -m pip install \
  -r "${LATENTSYNC_ROOT}/requirements.txt"

echo "Cloud environments ready under ${ENV_ROOT}"
echo "Next: bash scripts/download_cloud_models.sh"
