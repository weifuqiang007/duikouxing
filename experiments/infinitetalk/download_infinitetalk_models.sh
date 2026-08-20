#!/usr/bin/env bash
# 经 hf-mirror.com 选择性下载 InfiniteTalk 权重（服务器不通 huggingface.co）。
# fp8 quant 推理路径（multitalk.py:194）不需要 base DiT 7 分片(27GB) 与 bf16 T5(11GB)，
# 总下载量约 19GB。
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-}"
if [[ -z "$PROJECT_ROOT" ]]; then
  for d in "$HOME/duikouxing" /root/duikouxing /root/siton-tmp/duikouxing; do
    [[ -d "$d/external/InfiniteTalk" ]] && PROJECT_ROOT="$d" && break
  done
fi
IT_ROOT="$PROJECT_ROOT/external/InfiniteTalk"

# 复用 latentsync 环境里的 huggingface-cli（其 huggingface-hub 0.30.2 带 --include）。
HFDOWNLOAD="$PROJECT_ROOT/.conda-envs/latentsync/bin/huggingface-cli"
[[ -x "$HFDOWNLOAD" ]] || HFDOWNLOAD="$PROJECT_ROOT/.conda-envs/infinitetalk/bin/huggingface-cli"
command -v "$HFDOWNLOAD" >/dev/null || {
  echo "ERROR: 找不到 huggingface-cli，先跑 setup_cloud_infinitetalk.sh 或 setup_cloud_4090.sh" >&2
  exit 2
}

export HF_ENDPOINT="https://hf-mirror.com"
export HF_HOME="$PROJECT_ROOT/.cache/huggingface"
W="$IT_ROOT/weights"
mkdir -p "$W"

dl() {  # dl <repo> <local_dir> [extra args...]
  local repo="$1" dir="$2"; shift 2
  echo "== $repo =="
  "$HFDOWNLOAD" download "$repo" --local-dir "$dir" "$@"
}

# 1) Wan2.1-I2V-14B-480P：排除 base DiT 分片与 bf16 T5（fp8 路径不加载），
#    保留 config.json / VAE / CLIP / tokenizer。
dl Wan-AI/Wan2.1-I2V-14B-480P "$W/Wan2.1-I2V-14B-480P" \
  --exclude "diffusion_pytorch_model-*.safetensors" "models_t5_umt5-xxl-enc-bf16.pth"

# 2) InfiniteTalk 官方权重：fp8 quant DiT + fp8 T5（T5 从 quant_dir 同目录加载，
#    见 multitalk.py:176 与 t5.py:506）。
dl MeiGen-AI/InfiniteTalk "$W/InfiniteTalk" \
  --include "quant_models/infinitetalk_single_fp8.safetensors" \
            "quant_models/infinitetalk_single_fp8.json" \
            "quant_models/t5_fp8.safetensors" \
            "quant_models/t5_map_fp8.json"

# 3) 音频编码器：主仓 + fp16 权重（官方 README 指定 refs/pr/1）。
dl TencentGameMate/chinese-wav2vec2-base "$W/chinese-wav2vec2-base"
dl TencentGameMate/chinese-wav2vec2-base "$W/chinese-wav2vec2-base" \
  model.safetensors --revision refs/pr/1

echo "== 校验关键文件 =="
for f in \
  "$W/Wan2.1-I2V-14B-480P/config.json" \
  "$W/Wan2.1-I2V-14B-480P/Wan2.1_VAE.pth" \
  "$W/Wan2.1-I2V-14B-480P/google/umt5-xxl/tokenizer_config.json" \
  "$W/InfiniteTalk/quant_models/infinitetalk_single_fp8.safetensors" \
  "$W/InfiniteTalk/quant_models/t5_fp8.safetensors" \
  "$W/chinese-wav2vec2-base/model.safetensors" ; do
  [[ -s "$f" ]] && echo "  OK  $f" || { echo "  缺失 $f" ; MISSING=1; }
done
[[ "${MISSING:-0}" == 1 ]] && { echo "ERROR: 有文件缺失，检查上面的下载日志" >&2; exit 1; }
du -sh "$W"/*
echo "权重下载完成: $W"
