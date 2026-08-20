#!/usr/bin/env bash
# 大文件并行分段下载器：Clash 晚高峰单连接只有 ~300KB/s 时，
# 用 N 条并发 Range 请求聚合带宽（实测可近线性放大）。
# 前置：先停掉 download_models_local.sh（避免同文件竞争），本脚本独立补齐
# 两个 fp8 大文件 + wav2vec；小文件仍走 huggingface-cli。
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
W="external/InfiniteTalk/weights"
LOG="external/InfiniteTalk/weights_download.log"
PX="${HTTPS_PROXY:-http://127.0.0.1:7890}"
BASE="${HF_ENDPOINT:-https://hf-mirror.com}"
NCHUNK="${NCHUNK:-10}"

fetch_big() {  # fetch_big <url> <out>
  local url="$1" out="$2"
  mkdir -p "$(dirname "$out")"
  local total
  total=$(curl -x "$PX" -sIL -H "Range: bytes=0-0" "$url" | tr -d '\r' \
    | awk 'tolower($0) ~ /^content-range:/ {n=split($0,a,"/"); print a[n]}' | tail -1)
  [[ -n "$total" ]] || { echo "无法获取文件大小: $url" >&2; return 1; }
  if [[ -s "$out" && "$(stat -c%s "$out")" == "$total" ]]; then
    echo "已完整，跳过: $out"; return 0
  fi
  local chunk=$(( total / NCHUNK ))
  echo "[$(date '+%T')] 并行拉取 $(basename "$out") 共 $((total/1024/1024))MB × ${NCHUNK} 段"
  local pids=() i
  for i in $(seq 0 $((NCHUNK - 1))); do
    (
      local s=$(( i * chunk )) e
      if [[ $i == $((NCHUNK - 1)) ]]; then e=$(( total - 1 )); else e=$(( (i + 1) * chunk - 1 )); fi
      local want=$(( e - s + 1 )) try actual
      for try in 1 2 3 4 5 6 7 8; do
        curl -x "$PX" -sL --max-time 14400 --retry 3 -r "$s-$e" -o "$(printf "%s.part%02d" "$out" "$i")" "$url" && break
        sleep 10
      done
      actual=$(stat -c%s "$(printf "%s.part%02d" "$out" "$i")" 2>/dev/null || echo 0)
      [[ "$actual" == "$want" ]]
    ) &
    pids+=($!)
  done
  local fail=0 p
  for p in "${pids[@]}"; do wait "$p" || { fail=1; echo "分段失败: $out (pid $p)"; }; done
  [[ $fail == 0 ]] || return 1
  cat "$out".part* > "$out"   # part00..part09 补零后字典序即拼接序
  rm -f "$out".part*
  if [[ "$(stat -c%s "$out")" != "$total" ]]; then
    echo "合并后大小不符: $out" >&2; return 1
  fi
  echo "[$(date '+%T')] 完成: $out ($((total/1024/1024))MB)"
}

{
  echo "== 并行下载会话开始 $(date '+%F %T')（${NCHUNK} 段并发, 经 $PX -> $BASE）=="

  fetch_big "$BASE/MeiGen-AI/InfiniteTalk/resolve/main/quant_models/infinitetalk_single_fp8.safetensors" \
    "$W/InfiniteTalk/quant_models/infinitetalk_single_fp8.safetensors"
  fetch_big "$BASE/MeiGen-AI/InfiniteTalk/resolve/main/quant_models/t5_fp8.safetensors" \
    "$W/InfiniteTalk/quant_models/t5_fp8.safetensors"
} >> "$LOG" 2>&1

# 小文件：映射表 json 走 huggingface-cli（KB 级）
export https_proxy="$PX" http_proxy="$PX" HF_HUB_ENABLE_HF_TRANSFER=0 HF_ENDPOINT="$BASE"
huggingface-cli download MeiGen-AI/InfiniteTalk \
  --local-dir "$W/InfiniteTalk" \
  --include "quant_models/infinitetalk_single_fp8.json" "quant_models/t5_map_fp8.json" \
  >> "$LOG" 2>&1

# wav2vec：小文件整体拉取 + 大文件并行
huggingface-cli download TencentGameMate/chinese-wav2vec2-base \
  --local-dir "$W/chinese-wav2vec2-base" \
  --exclude "chinese-wav2vec2-base-fairseq-ckpt.pt" \
  >> "$LOG" 2>&1
fetch_big "$BASE/TencentGameMate/chinese-wav2vec2-base/resolve/main/chinese-wav2vec2-base-fairseq-ckpt.pt" \
  "$W/chinese-wav2vec2-base/chinese-wav2vec2-base-fairseq-ckpt.pt" >> "$LOG" 2>&1
huggingface-cli download TencentGameMate/chinese-wav2vec2-base \
  model.safetensors --revision refs/pr/1 \
  --local-dir "$W/chinese-wav2vec2-base" >> "$LOG" 2>&1

echo "== 校验关键文件 $(date '+%T') ==" | tee -a "$LOG"
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
  [[ -s "$f" ]] && echo "  OK  $f" | tee -a "$LOG" || { echo "  缺失 $f" | tee -a "$LOG"; MISSING=1; }
done
if [[ "$MISSING" == 1 ]]; then
  echo "DOWNLOAD_INCOMPLETE $(date '+%F %T')" >> "$LOG"
  echo "ERROR: 有文件缺失，查看 $LOG" >&2; exit 1
fi
echo "DOWNLOAD_ALL_DONE $(date '+%F %T')" >> "$LOG"
echo "全部下载完成: $W"
