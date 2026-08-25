#!/usr/bin/env bash
# 修正版模型下载（Comfy-Org 而非 ComfyUI；t5 用 Kijai 原名文件）
set -uo pipefail
M=/root/siton-tmp/aigc/ComfyUI-Infinitetalk/models
mkdir -p $M/vae $M/clip_vision $M/text_encoders $M/diffusion_models
dl(){ echo "[$(date +%H:%M:%S)] dl $(basename $2)"; curl -fL --retry 5 -C - --connect-timeout 20 -o "$2.part" "$1" && mv "$2.part" "$2" && echo "  OK $(du -h "$2" | cut -f1)" || echo "  FAIL $1"; }
dl https://hf-mirror.com/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors $M/vae/Wan2_1_VAE_bf16.safetensors
dl https://hf-mirror.com/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/clip_vision/clip_vision_h.safetensors $M/clip_vision/clip_vision_vit_h.safetensors
dl https://hf-mirror.com/Kijai/WanVideo_comfy/resolve/main/umt5-xxl-enc-bf16.safetensors $M/text_encoders/umt5-xxl-enc-bf16.safetensors
dl https://hf-mirror.com/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/diffusion_models/wan2.1_i2v_480p_14B_fp8_e4m3fn.safetensors $M/diffusion_models/Wan2_1-I2V-14B-480P_fp8_e4m3fn.safetensors
echo "DL_FIX_DONE"
