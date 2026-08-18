#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: bash scripts/run_job.sh <job.yaml> [--force]" >&2
  exit 2
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ORCHESTRATOR_ENV="${PROJECT_ROOT}/.conda-envs/digital-human"
JOB_PATH="$(realpath "$1")"
FORCE_ARG="${2:-}"

export DIGITAL_HUMAN_PROFILE="cloud"
export HF_HOME="${PROJECT_ROOT}/.cache/huggingface"
export HF_HUB_CACHE="${HF_HOME}/hub"
export TORCH_HOME="${PROJECT_ROOT}/.cache/torch"
export XDG_CACHE_HOME="${PROJECT_ROOT}/.cache"
export PIP_CACHE_DIR="${PROJECT_ROOT}/.cache/pip"
export TMPDIR="${PROJECT_ROOT}/.tmp"
export HF_HUB_OFFLINE=1

ARGS=(run --profile cloud --job "${JOB_PATH}")
if [[ "${FORCE_ARG}" == "--force" ]]; then
  ARGS+=(--force)
fi

conda run -p "${ORCHESTRATOR_ENV}" python -m digital_human.cli "${ARGS[@]}"
