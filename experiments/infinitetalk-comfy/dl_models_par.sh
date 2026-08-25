#!/usr/bin/env bash
# 4 文件并行下载（绕过 hf-mirror 单连接限速）
set -uo pipefail
M=/root/siton-tmp/aigc/ComfyUI-Infinitetalk/models
dl(){ echo "[$(date +%H:%M:%S)] start $(basename $2)"; curl -fsL --retry 8 -C - --connect-timeout 20 -o "$2.part" "$1" && mv "$2.part" "$2" && echo "[$(date +%H:%M:%S)] OK $(basename $2) $(du -h "$2" | cut -f1)" || echo "[$(date +%H:%M:%S)] FAIL $(basename $2)"; }
dl https://hf-mirror.com/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors $M/vae/Wan2_1_VAE_bf16.safetensors &
dl https://hf-mirror.com/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/clip_vision/clip_vision_h.safetensors $M/clip_vision/clip_vision_vit_h.safetensors &
dl https://hf-mirror.com/Kijai/WanVideo_comfy/resolve/main/umt5-xxl-enc-bf16.safetensors $M/text_encoders/umt5-xxl-enc-bf16.safetensors &
dl https://hf-mirror.com/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/diffusion_models/wan2.1_i2v_480p_14B_fp8_e4m3fn.safetensors $M/diffusion_models/Wan2_1-I2V-14B-480P_fp8_e4m3fn.safetensors &
wait
echo "DL_PARALLEL_DONE"
