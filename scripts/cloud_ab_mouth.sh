#!/usr/bin/env/bash
# 云端 A/B/C 口型幅度实验：复用 wlh-004-c3 中间产物，仅重跑 LatentSync 推理。
#   A (c4): guidance 1.5          B (c5): guidance 1.8          C (c6): guidance 1.5 + 音频特征放大 1.3
# 用法（在云端服务器上）: bash cloud_ab_mouth.sh
#   或本地一键:  ssh -p 34300 root@<host> 'cat > /root/cloud_ab_mouth.sh && nohup bash /root/cloud_ab_mouth.sh > /root/ab_mouth.log 2>&1 & echo STARTED' < scripts/cloud_ab_mouth.sh
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-}"
if [[ -z "$PROJECT_ROOT" ]]; then
  for d in "$HOME/duikouxing" /root/duikouxing /data/duikouxing /workspace/duikouxing /root/siton-tmp/duikouxing; do
    [[ -d "$d/external/LatentSync" ]] && PROJECT_ROOT="$d" && break
  done
fi
if [[ -z "$PROJECT_ROOT" ]]; then
  hit="$(find / -maxdepth 6 -path '*/scripts/setup_cloud_4090.sh' 2>/dev/null | head -1 || true)"
  [[ -n "$hit" ]] && PROJECT_ROOT="$(dirname "$(dirname "$hit")")"
fi
[[ -n "$PROJECT_ROOT" && -d "$PROJECT_ROOT/external/LatentSync" ]] || {
  echo "ERROR: 未找到项目根目录（需含 external/LatentSync），请 PROJECT_ROOT=/path/to/duikouxing 显式指定" >&2
  exit 2
}
echo "PROJECT_ROOT=$PROJECT_ROOT"

# 与 setup_cloud_4090.sh 一致的缓存环境：VAE/whisper 走项目内 HF 缓存，容器无法直连 huggingface.co
export LANG=C.UTF-8 LC_ALL=C.UTF-8
export HF_HOME="$PROJECT_ROOT/.cache/huggingface"
export HF_HUB_CACHE="$HF_HOME/hub"
export TORCH_HOME="$PROJECT_ROOT/.cache/torch"
export XDG_CACHE_HOME="$PROJECT_ROOT/.cache"

LS="$PROJECT_ROOT/external/LatentSync"
ENV="$PROJECT_ROOT/.conda-envs/latentsync"
JOBS="$PROJECT_ROOT/jobs-cloud"
PY="$ENV/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"   # 容器可能没有 conda，直接用环境内的 python
BASE="$JOBS/wlh-004-c3/work/base_duration_matched.mp4"
WAV="$JOBS/wlh-004-c3/work/target_normalized.wav"
[[ -f "$BASE" && -f "$WAV" ]] || { echo "ERROR: 缺少 wlh-004-c3 中间产物: $BASE / $WAV" >&2; exit 2; }

echo "== GPU =="
nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv || true
echo "== 补丁检查 =="
grep -q "LATENTSYNC_AUDIO_AMP" "$LS/latentsync/whisper/audio2feature.py" || python3 - "$LS/latentsync/whisper/audio2feature.py" <<'PYEOF'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
t = p.read_text(encoding="utf-8")
old = "        concatenated_array = torch.from_numpy(np.concatenate(embed_list, axis=0))\n        return concatenated_array\n"
new = ("        concatenated_array = torch.from_numpy(np.concatenate(embed_list, axis=0))\n"
       "        # 口型幅度实验：环境变量放大 whisper 音频特征，增强嘴部开合驱动。\n"
       "        # 默认 1.0 = 官方原行为；仅在显式设置 LATENTSYNC_AUDIO_AMP 时生效。\n"
       "        amplitude = float(os.environ.get(\"LATENTSYNC_AUDIO_AMP\", \"1.0\"))\n"
       "        if amplitude != 1.0:\n"
       "            concatenated_array = concatenated_array * amplitude\n"
       "        return concatenated_array\n")
if old not in t:
    print("ERROR: 补丁锚点未找到，上游文件与预期不一致，停止") ; sys.exit(1)
p.write_text(t.replace(old, new, 1), encoding="utf-8")
print("audio2feature.py 已打补丁")
PYEOF
grep -q -- "-c:v copy" "$LS/latentsync/pipelines/lipsync_pipeline.py" || echo "WARN: 官方高画质封装补丁未应用（视频流可能被二次编码）"

run_one() {
  local name="$1" guidance="$2" amp="$3"
  local out="$JOBS/wlh-004-$name"
  mkdir -p "$out/work" "$out/output"
  if [[ -s "$out/output/final.mp4" ]]; then echo "[$name] 已存在，跳过"; return 0; fi
  echo "[$name] guidance=$guidance audio_amp=$amp 开始 $(date +%T)"
  export LATENTSYNC_AUDIO_AMP="$amp"
  if ! (cd "$LS" && "$PY" -m scripts.inference \
      --unet_config_path configs/unet/stage2_512.yaml \
      --inference_ckpt_path checkpoints/latentsync_unet.pt \
      --inference_steps 30 \
      --guidance_scale "$guidance" \
      --seed 1247 \
      --video_path "$BASE" \
      --audio_path "$WAV" \
      --video_out_path "$out/work/latentsync_result.mp4" \
      --temp_dir "$out/work/latentsync_temp" \
      >"$out/work/latentsync.log" 2>&1); then
    echo "[$name] 推理失败，详见 $out/work/latentsync.log"; return 1
  fi
  ffmpeg -hide_banner -loglevel error -y \
    -i "$out/work/latentsync_result.mp4" -i "$WAV" \
    -c:v copy -c:a aac -b:a 192k "$out/output/final.mp4"
  printf '{\n  "job_id": "wlh-004-%s",\n  "experiment": "mouth-amplitude-ab",\n  "lipsync_engine": "latentsync_1_6",\n  "inference_steps": 30,\n  "guidance_scale": %s,\n  "latentsync_audio_amp": %s,\n  "seed": 1247,\n  "reused_intermediates_from": "wlh-004-c3"\n}\n' \
    "$name" "$guidance" "$amp" > "$out/manifest.json"
  echo "[$name] 完成 $(date +%T) -> $out/output/final.mp4"
}

set +e
run_one c4-guidance15 1.5 1.0
run_one c5-guidance18 1.8 1.0
run_one c6-guid15-amp13 1.5 1.3
set -e

echo ""
echo "== 全部结束 $(date +%T)，回传到本地（在本机执行）: =="
for n in c4-guidance15 c5-guidance18 c6-guid15-amp13; do
  echo "  scp -P 34300 root@<host>:$JOBS/wlh-004-$n/output/final.mp4 jobs-cloud/wlh-004-$n-output.mp4"
done
