#!/usr/bin/env bash
# 修复损坏文件 + 下载 Kijai InfiniteTalk 适配层 + 校验 t5
set -uo pipefail
M=/root/siton-tmp/aigc/ComfyUI-Infinitetalk/models
dl(){ echo "[$(date +%H:%M:%S)] dl $(basename $2)"; curl -fsL --retry 8 --connect-timeout 20 -o "$2.tmp" "$1" && mv "$2.tmp" "$2" && echo "[$(date +%H:%M:%S)] OK $(basename $2) $(stat -c %s "$2")" || echo "FAIL $1"; }

# 1. clip_vision 重下（删除损坏文件，全新下载不用断点续传）
rm -f $M/clip_vision/clip_vision_vit_h.safetensors
dl https://hf-mirror.com/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/clip_vision/clip_vision_h.safetensors $M/clip_vision/clip_vision_vit_h.safetensors &

# 2. Kijai InfiniteTalk Single fp16（multitalk 适配层 4.8G）
rm -f $M/diffusion_models/Wan2_1-InfiniTetalk-Single_fp16.safetensors $M/multitalk/Wan2_1-InfiniTetalk-Single_fp16.safetensors
dl https://hf-mirror.com/Kijai/WanVideo_comfy/resolve/main/InfiniteTalk/Wan2_1-InfiniTetalk-Single_fp16.safetensors $M/multitalk/Wan2_1-InfiniTetalk-Single_fp16.safetensors &

# 3. t5 校验
EXP_T5=$(curl -s "https://hf-mirror.com/api/models/Kijai/WanVideo_comfy?blobs=true" | python3 -c "import json,sys; print([s['size'] for s in json.load(sys.stdin)['siblings'] if s['rfilename']=='umt5-xxl-enc-bf16.safetensors'][0])")
LOC_T5=$(stat -c %s $M/text_encoders/umt5-xxl-enc-bf16.safetensors 2>/dev/null || echo 0)
echo "t5 expected=$EXP_T5 local=$LOC_T5"
if [ "$EXP_T5" != "$LOC_T5" ]; then
  rm -f $M/text_encoders/umt5-xxl-enc-bf16.safetensors
  dl https://hf-mirror.com/Kijai/WanVideo_comfy/resolve/main/umt5-xxl-enc-bf16.safetensors $M/text_encoders/umt5-xxl-enc-bf16.safetensors &
fi
wait
# multitalk 软链到 diffusion_models（loader 在两个目录找）
ln -sf $M/multitalk/Wan2_1-InfiniTetalk-Single_fp16.safetensors $M/diffusion_models/Wan2_1-InfiniTetalk-Single_fp16.safetensors
echo "DL_FIX2_DONE"
