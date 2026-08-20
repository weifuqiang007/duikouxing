#!/usr/bin/env bash
# modelscope 直连版分块下载器（不走 Clash，国内 CDN）。
# 与 fetch_chunks_local.sh 的分块目录/目标路径完全一致 → 已有块自动续传。
# 小文件直接 curl 拉取，不再依赖 huggingface-cli。
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
W="external/InfiniteTalk/weights"
LOG="external/InfiniteTalk/weights_download.log"
MS="https://www.modelscope.cn/models"
CHUNK_MB="${CHUNK_MB:-12}"
WORKERS="${WORKERS:-12}"
CHUNK=$(( CHUNK_MB * 1024 * 1024 ))

file_size() {
  curl -sIL -H "Range: bytes=0-0" "$1" | tr -d '\r' \
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
  echo "[$(date '+%T')] [MS] 分块拉取 $(basename "$dest")：$((total/1024/1024))MB ÷ ${CHUNK_MB}MB = ${nchunk} 块 × ${WORKERS} 工人"
  local w
  for w in $(seq 0 $((WORKERS - 1))); do
    (
      local idx=$w want s e try ok done_count=0
      while (( idx < nchunk )); do
        s=$(( idx * CHUNK ))
        e=$(( s + CHUNK - 1 )); (( e >= total )) && e=$(( total - 1 ))
        want=$(( e - s + 1 ))
        if [[ "$(stat -c%s "$tmpd/$(printf '%06d' "$idx")" 2>/dev/null || echo 0)" == "$want" ]]; then
          idx=$(( idx + WORKERS )); continue
        fi
        ok=0
        for try in 1 2 3 4 5 6 7 8 9 10 11 12; do
          curl -sL --connect-timeout 15 --max-time 300 -r "$s-$e" \
            -o "$tmpd/$(printf '%06d' "$idx")" "$url"
          [[ "$(stat -c%s "$tmpd/$(printf '%06d' "$idx")" 2>/dev/null || echo 0)" == "$want" ]] && { ok=1; break; }
          sleep 3
        done
        (( ok == 1 )) || { echo "[FAIL] 块 $idx 重试耗尽"; exit 1; }
        done_count=$((done_count+1))
        (( done_count % 25 == 0 )) && echo "  [w$w] $done_count 块完成"
        idx=$(( idx + WORKERS ))
      done
    ) &
  done
  wait
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
  echo "[$(date '+%T')] [MS] 完成: $(basename "$dest") ($((total/1024/1024))MB)"
}

{
  echo "== modelscope 直连会话 $(date '+%F %T')（${CHUNK_MB}MB×${WORKERS}工人，无代理）=="
  fetch_chunked "$MS/MeiGen-AI/InfiniteTalk/resolve/master/quant_models/infinitetalk_single_fp8.safetensors" \
    "$W/InfiniteTalk/quant_models/infinitetalk_single_fp8.safetensors"
  fetch_chunked "$MS/MeiGen-AI/InfiniteTalk/resolve/master/quant_models/t5_fp8.safetensors" \
    "$W/InfiniteTalk/quant_models/t5_fp8.safetensors"
  fetch_chunked "$MS/TencentGameMate/chinese-wav2vec2-base/resolve/master/model.safetensors" \
    "$W/chinese-wav2vec2-base/model.safetensors"
  fetch_chunked "$MS/TencentGameMate/chinese-wav2vec2-base/resolve/master/pytorch_model.bin" \
    "$W/chinese-wav2vec2-base/pytorch_model.bin"

  # 小文件直接 curl（wav2vec config；InfiniteTalk 两个 json 已在早前会话完成，幂等重拉）
  mkdir -p "$W/chinese-wav2vec2-base" "$W/InfiniteTalk/quant_models"
  for pair in \
    "TencentGameMate/chinese-wav2vec2-base|config.json|$W/chinese-wav2vec2-base/config.json" \
    "TencentGameMate/chinese-wav2vec2-base|preprocessor_config.json|$W/chinese-wav2vec2-base/preprocessor_config.json" \
    "MeiGen-AI/InfiniteTalk|quant_models/infinitetalk_single_fp8.json|$W/InfiniteTalk/quant_models/infinitetalk_single_fp8.json" \
    "MeiGen-AI/InfiniteTalk|quant_models/t5_map_fp8.json|$W/InfiniteTalk/quant_models/t5_map_fp8.json" ; do
    IFS='|' read -r repo path dest <<< "$pair"
    for try in 1 2 3 4 5; do
      curl -sL --max-time 60 -o "$dest" "$MS/$repo/resolve/master/$path" && [[ -s "$dest" ]] && break
      sleep 5
    done
    [[ -s "$dest" ]] && echo "[MS] OK $dest" || echo "[MS] 缺失 $dest"
  done
} >> "$LOG" 2>&1

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
