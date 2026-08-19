#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOTS_ENV="${PROJECT_ROOT}/.conda-envs/dots-tts"

export HF_HOME="${PROJECT_ROOT}/.cache/huggingface"
export HF_HUB_CACHE="${HF_HOME}/hub"
export TORCH_HOME="${PROJECT_ROOT}/.cache/torch"
export XDG_CACHE_HOME="${PROJECT_ROOT}/.cache"
export PIP_CACHE_DIR="${PROJECT_ROOT}/.cache/pip"
export TMPDIR="${PROJECT_ROOT}/.tmp"

bash "${PROJECT_ROOT}/scripts/download_latentsync_models.sh"

[[ -x "${DOTS_ENV}/bin/python" ]] || {
  echo "ERROR: missing dots.tts environment; run setup_cloud_4090.sh first" >&2
  exit 2
}

mkdir -p "${PROJECT_ROOT}/models"
conda run -p "${DOTS_ENV}" huggingface-cli download dots-studio/dots.tts-soar \
  --local-dir "${PROJECT_ROOT}/models/dots.tts-soar"
conda run -p "${DOTS_ENV}" huggingface-cli download dots-studio/dots.tts-mf \
  --local-dir "${PROJECT_ROOT}/models/dots.tts-mf"

echo "All cloud pipeline models downloaded under ${PROJECT_ROOT}"
