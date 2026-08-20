#!/usr/bin/env bash
# InfiniteTalk V2V 配音实验：wlh-004 同一份 14s 素材（原视频 + 克隆音频），
# 与可灵 / c4 / c5 / c6 对比口径一致。前置：setup + download 两个脚本已跑完。
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-}"
if [[ -z "$PROJECT_ROOT" ]]; then
  for d in "$HOME/duikouxing" /root/duikouxing /root/siton-tmp/duikouxing; do
    [[ -d "$d/external/InfiniteTalk" ]] && PROJECT_ROOT="$d" && break
  done
fi
IT_ROOT="$PROJECT_ROOT/external/InfiniteTalk"
ENV="$PROJECT_ROOT/.conda-envs/infinitetalk"
PY="$ENV/bin/python"
W="$IT_ROOT/weights"
JOB="$PROJECT_ROOT/jobs-cloud/wlh-004-it-v2v"
BASE="$PROJECT_ROOT/jobs-cloud/wlh-004-c3/work/base_duration_matched.mp4"
WAV="$PROJECT_ROOT/jobs-cloud/wlh-004-c3/work/target_normalized.wav"

[[ -x "$PY" ]] || { echo "ERROR: 先跑 setup_cloud_infinitetalk.sh" >&2; exit 2; }
[[ -s "$W/InfiniteTalk/quant_models/infinitetalk_single_fp8.safetensors" ]] || {
  echo "ERROR: 先跑 download_infinitetalk_models.sh" >&2; exit 2; }
[[ -f "$BASE" && -f "$WAV" ]] || { echo "ERROR: 缺少 wlh-004-c3 中间产物" >&2; exit 2; }

mkdir -p "$JOB/work" "$JOB/output"
[[ -s "$JOB/output/final.mp4" ]] && { echo "已存在 $JOB/output/final.mp4，跳过（删掉可重跑）"; exit 0; }

# 14s 对齐可灵：视频取前 14s（内容=原始素材开头），音频取克隆音频前 14s。
ffmpeg -hide_banner -loglevel error -y -t 14 -i "$BASE" -c copy "$JOB/work/cond_video_14s.mp4"
ffmpeg -hide_banner -loglevel error -y -t 14 -i "$WAV" -ar 16000 -ac 1 "$JOB/work/cond_audio_14s.wav"

cat > "$JOB/work/input_v2v.json" <<JSON
{
    "prompt": "A Chinese man in a suit holds a red booklet and speaks earnestly to the camera in an office, frontal medium shot, steady camera.",
    "cond_video": "$JOB/work/cond_video_14s.mp4",
    "cond_audio": {
        "person1": "$JOB/work/cond_audio_14s.wav"
    }
}
JSON

echo "[it-v2v] 开始 $(date +%T)（40 步 fp8 quant + 层流式，14s/480P 预计 30-90 分钟）"
cd "$IT_ROOT"
export HF_HOME="$PROJECT_ROOT/.cache/huggingface"
export HF_HUB_OFFLINE=1
"$PY" generate_infinitetalk.py \
    --ckpt_dir "$W/Wan2.1-I2V-14B-480P" \
    --wav2vec_dir "$W/chinese-wav2vec2-base" \
    --quant fp8 \
    --quant_dir "$W/InfiniteTalk/quant_models/infinitetalk_single_fp8.safetensors" \
    --input_json "$JOB/work/input_v2v.json" \
    --size infinitetalk-480 \
    --sample_steps 40 \
    --sample_text_guide_scale 5.0 \
    --sample_audio_guide_scale 4.0 \
    --mode streaming \
    --motion_frame 9 \
    --num_persistent_param_in_dit 0 \
    --base_seed 1247 \
    --save_file "$JOB/work/infinitetalk_v2v_14s" \
    > "$JOB/work/infinitetalk.log" 2>&1

# save_video_ffmpeg 直接产出已混音的 {save_file}.mp4
[[ -s "$JOB/work/infinitetalk_v2v_14s.mp4" ]] || {
  echo "ERROR: 未生成视频，查看 $JOB/work/infinitetalk.log" >&2; exit 1; }
cp "$JOB/work/infinitetalk_v2v_14s.mp4" "$JOB/output/final.mp4"
cat > "$JOB/manifest.json" <<JSON
{
  "job_id": "wlh-004-it-v2v",
  "experiment": "infinitetalk-v2v-dubbing",
  "repo_commit": "fd631497254e065777f2b2d0642de3600d674e24",
  "base_model": "Wan2.1-I2V-14B-480P (fp8 quant)",
  "sample_steps": 40,
  "text_cfg": 5.0,
  "audio_cfg": 4.0,
  "mode": "streaming",
  "seed": 1247,
  "input_duration_s": 14,
  "reused_intermediates_from": "wlh-004-c3"
}
JSON
echo "[it-v2v] 完成 $(date +%T) -> $JOB/output/final.mp4"
