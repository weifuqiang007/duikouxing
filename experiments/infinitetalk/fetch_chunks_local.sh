#!/usr/bin/env bash
# 小分块并行下载器 v2：Clash 晚高峰长连接活不过 ~65MB，改 12MB 小块 +
# 8 工人轮转，每块独立短连接+重试。适用于所有 >8MB 的文件。
# 小文件（json/config）不走本脚本。
set -uo pipefail   # 不用 -e：worker 内部自己管错误，主流程靠校验兜底

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
W="external/InfiniteTalk/weights"
LOG="external/InfiniteTalk/weights_download.log"
PX="${HTTPS_PROXY:-http://127.0.0.1:7890}"
BASE="${HF_ENDPOINT:-https://hf-mirror.com}"
CHUNK_MB="${CHUNK_MB:-12}"
WORKERS="${WORKERS:-8}"

CHUNK=$(( CHUNK_MB * 1024 * 1024 ))

file_size() {  # <url> -> 字节数
  curl -x "$PX" -sIL -H "Range: bytes=0-0" "$1" | tr -d '\r' \
    | awk 'tolower($0) ~ /^content-range:/ {n=split($0,a,"/"); print a[n]}' | tail -1
}

fetch_chunked() {  # <url> <dest>
  local url="$1" dest="$2"
  mkdir -p "$(dirname "$dest")"
  local total
  total=$(file_size "$url")
  if [[ -z "$total" || "$total" -le 0 ]]; then echo "[FAIL] 拿不到大小: $url"; return 1; fi
  if [[ -s "$dest" && "$(stat -c%s "$dest")" == "$total" ]]; then
    echo "[SKIP] 已完整: $(basename "$dest")"; return 0
  fi
  local nchunk=$(( (total + CHUNK - 1) / CHUNK ))
  local tmpd="${dest}.chunks"
  mkdir -p "$tmpd"
  echo "[$(date '+%T')] 分块拉取 $(basename "$dest")：$((total/1024/1024))MB ÷ ${CHUNK_MB}MB = ${nchunk} 块 × ${WORKERS} 工人"
  local w
  for w in $(seq 0 $((WORKERS - 1))); do
    (
      local idx=$w want s e try ok done_count=0
      while (( idx < nchunk )); do
        s=$(( idx * CHUNK ))
        e=$(( s + CHUNK - 1 )); (( e >= total )) && e=$(( total - 1 ))
        want=$(( e - s + 1 ))
        # 块级续传：上次已完成的块直接跳过
        if [[ "$(stat -c%s "$tmpd/$(printf '%06d' "$idx")" 2>/dev/null || echo 0)" == "$want" ]]; then
          idx=$(( idx + WORKERS )); continue
        fi
        ok=0
        for try in 1 2 3 4 5 6 7 8 9 10 11 12; do
          curl -x "$PX" -sL --connect-timeout 15 --max-time 300 -r "$s-$e" \
            -o "$tmpd/$(printf '%06d' "$idx")" "$url"
          [[ "$(stat -c%s "$tmpd/$(printf '%06d' "$idx")" 2>/dev/null || echo 0)" == "$want" ]] && { ok=1; break; }
          sleep 3
        done
        (( ok == 1 )) || { echo "[FAIL] 块 $idx 重试耗尽: $(basename "$dest")"; exit 1; }
        done_count=$((done_count+1))
        (( done_count % 25 == 0 )) && echo "  [worker$w] $done_count 块完成"
        idx=$(( idx + WORKERS ))
      done
    ) &
  done
  local fail=0
  wait || fail=1
  # 全量校验块数与尺寸
  local i bad=0
  for i in $(seq 0 $((nchunk - 1))); do
    f="$tmpd/$(printf '%06d' "$i")"
    s=$(( i * CHUNK )); e=$(( s + CHUNK - 1 )); (( e >= total )) && e=$(( total - 1 ))
    [[ "$(stat -c%s "$f" 2>/dev/null || echo 0)" == "$(( e - s + 1 ))" ]] || { bad=$((bad+1)); echo "[MISS] 块 $i"; }
  done
  (( bad == 0 )) || { echo "[FAIL] $(basename "$dest") 缺 $bad 块"; return 1; }
  cat "$tmpd"/[0-9]* > "$dest"
  rm -rf "$tmpd"
  [[ "$(stat -c%s "$dest")" == "$total" ]] || { echo "[FAIL] 合并大小不符: $dest"; return 1; }
  echo "[$(date '+%T')] 完成: $(basename "$dest") ($((total/1024/1024))MB)"
}

{
  echo "== 小分块下载会话 $(date '+%F %T')（${CHUNK_MB}MB×${WORKERS}工人）=="
  fetch_chunked "$BASE/MeiGen-AI/InfiniteTalk/resolve/main/quant_models/infinitetalk_single_fp8.safetensors" \
    "$W/InfiniteTalk/quant_models/infinitetalk_single_fp8.safetensors"
  fetch_chunked "$BASE/MeiGen-AI/InfiniteTalk/resolve/main/quant_models/t5_fp8.safetensors" \
    "$W/InfiniteTalk/quant_models/t5_fp8.safetensors"
  fetch_chunked "$BASE/TencentGameMate/chinese-wav2vec2-base/resolve/main/pytorch_model.bin" \
    "$W/chinese-wav2vec2-base/pytorch_model.bin"
  fetch_chunked "$BASE/TencentGameMate/chinese-wav2vec2-base/resolve/main/chinese-wav2vec2-base-fairseq-ckpt.pt" \
    "$W/chinese-wav2vec2-base/chinese-wav2vec2-base-fairseq-ckpt.pt"
  fetch_chunked "$BASE/TencentGameMate/chinese-wav2vec2-base/resolve/refs/pr/1/model.safetensors" \
    "$W/chinese-wav2vec2-base/model.safetensors"
} >> "$LOG" 2>&1

# 小文件：wav2vec 配置 + 映射表 json（重试几轮，晚高峰 requests 偶发失败）
export https_proxy="$PX" http_proxy="$PX" HF_HUB_ENABLE_HF_TRANSFER=0 HF_ENDPOINT="$BASE"
for try in 1 2 3 4 5; do
  huggingface-cli download TencentGameMate/chinese-wav2vec2-base \
    --local-dir "$W/chinese-wav2vec2-base" \
    --exclude "pytorch_model.bin" "chinese-wav2vec2-base-fairseq-ckpt.pt" "model.safetensors" \
    >> "$LOG" 2>&1 && break
  echo "  小文件第 $try 轮失败，重试"; sleep 20
done
for try in 1 2 3 4 5; do
  huggingface-cli download MeiGen-AI/InfiniteTalk \
    --local-dir "$W/InfiniteTalk" \
    --include "quant_models/infinitetalk_single_fp8.json" "quant_models/t5_map_fp8.json" \
    >> "$LOG" 2>&1 && break
  sleep 20
done

echo "== 终局校验 $(date '+%T') ==" | tee -a "$LOG"
MISSING=0
for f in \
  "$W/Wan2.1-I2V-14B-480P/config.json" \
  "$W/Wan2.1-I2V-14B-480P/Wan2.1_VAE.pth" \
  "$W/Wan2.1-I2V-14B-480P/google/umt5-xxl/tokenizer_config.json" \
  "$W/InfiniteTalk/quant_models/infinitetalk_single_fp8.safetensors" \
  "$W/InfiniteTalk/quant_models/infinitetalk_single_fp8.json" \
  "$W/InfiniteTalk/quant_models/t5_fp8.safetensors" \
  "$W/InfiniteTalk/quant_models/t5_map_fp8.json" \
  "$W/chinese-wav2vec2-base/model.safetensors" \
  "$W/chinese-wav2vec2-base/pytorch_model.bin" ; do
  [[ -s "$f" ]] && echo "  OK  $f" | tee -a "$LOG" || { echo "  缺失 $f" | tee -a "$LOG"; MISSING=1; }
done
if [[ "$MISSING" == 1 ]]; then
  echo "DOWNLOAD_INCOMPLETE $(date '+%F %T')" >> "$LOG"; exit 1
fi
echo "DOWNLOAD_ALL_DONE $(date '+%F %T')" >> "$LOG"
echo "全部下载完成: $W"
