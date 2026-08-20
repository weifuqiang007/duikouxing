#!/usr/bin/env bash
# 本地经 Clash 代理下载 InfiniteTalk 权重（服务器不通 huggingface.co）。
# 产物落在 external/InfiniteTalk/weights/，与服务器目录结构完全一致 ——
# 下载完成后整目录打包上传服务器即可（见 README「运行」段）。
# 总量约 33GB：fp8 DiT 19.5G + t5_fp8 6.7G + Wan VAE/CLIP 5.3G + wav2vec 1.5G。
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
W="external/InfiniteTalk/weights"
LOG="external/InfiniteTalk/weights_download.log"

export https_proxy="${HTTPS_PROXY:-http://127.0.0.1:7890}"
export http_proxy="${HTTP_PROXY:-http://127.0.0.1:7890}"
export HF_HUB_ENABLE_HF_TRANSFER=0   # hf_transfer 不吃代理变量，关掉走 requests
# NO_PROXY=1：本机可直连 hf-mirror 时去掉代理（默认保留，经 Clash 访问）。
if [[ "${NO_PROXY:-0}" == 1 ]]; then
  unset https_proxy http_proxy all_proxy
fi

mkdir -p "$W"
{
  echo "== 本地代理下载开始 $(date '+%F %T') =="
  huggingface-cli download Wan-AI/Wan2.1-I2V-14B-480P \
    --local-dir "$W/Wan2.1-I2V-14B-480P" \
    --exclude "diffusion_pytorch_model-*.safetensors" "models_t5_umt5-xxl-enc-bf16.pth"
  echo "== [1/3] Wan2.1 base 完成 $(date '+%T') =="

  huggingface-cli download MeiGen-AI/InfiniteTalk \
    --local-dir "$W/InfiniteTalk" \
    --include "quant_models/infinitetalk_single_fp8.safetensors" \
              "quant_models/infinitetalk_single_fp8.json" \
              "quant_models/t5_fp8.safetensors" \
              "quant_models/t5_map_fp8.json"
  echo "== [2/3] InfiniteTalk fp8 完成 $(date '+%T') =="

  huggingface-cli download TencentGameMate/chinese-wav2vec2-base \
    --local-dir "$W/chinese-wav2vec2-base"
  huggingface-cli download TencentGameMate/chinese-wav2vec2-base \
    model.safetensors --revision refs/pr/1 \
    --local-dir "$W/chinese-wav2vec2-base"
  echo "== [3/3] wav2vec 完成 $(date '+%T') =="
} >> "$LOG" 2>&1

echo "== 校验关键文件 =="
MISSING=0
for f in \
  "$W/Wan2.1-I2V-14B-480P/config.json" \
  "$W/Wan2.1-I2V-14B-480P/Wan2.1_VAE.pth" \
  "$W/Wan2.1-I2V-14B-480P/google/umt5-xxl/tokenizer_config.json" \
  "$W/InfiniteTalk/quant_models/infinitetalk_single_fp8.safetensors" \
  "$W/InfiniteTalk/quant_models/infinitetalk_single_fp8.json" \
  "$W/InfiniteTalk/quant_models/t5_fp8.safetensors" \
  "$W/InfiniteTalk/quant_models/t5_map_fp8.json" \
  "$W/chinese-wav2vec2-base/model.safetensors" ; do
  [[ -s "$f" ]] && echo "  OK  $f" || { echo "  缺失 $f"; MISSING=1; }
done
du -sh "$W"/*
if [[ "$MISSING" == 1 ]]; then
  echo "DOWNLOAD_INCOMPLETE $(date '+%T')" >> "$LOG"
  echo "ERROR: 有文件缺失，查看 $LOG" >&2
  exit 1
fi
echo "DOWNLOAD_ALL_DONE $(date '+%F %T')" >> "$LOG"
echo "全部下载完成: $W"
