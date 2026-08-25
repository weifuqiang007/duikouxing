#!/usr/bin/env bash
# ComfyUI+InfiniteTalk 部署续跑脚本（torch 之后的部分；tuna 源 + 超时重试）
set -uo pipefail
AIGC=/root/siton-tmp/aigc
C=$AIGC/ComfyUI-Infinitetalk
OLD=/root/siton-tmp/duikouxing/external/InfiniteTalk/weights
export PIP_CACHE_DIR=$AIGC/.pipcache TMPDIR=$AIGC/.tmp
PY=$AIGC/venv-comfy/bin/python
PIPOPT="-i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 30 --retries 10"
step(){ echo "[$(date +%H:%M:%S)] $*"; }

step "3/6 torch cu121"
$PY -m pip install $PIPOPT torch==2.4.1 torchvision==0.19.1 torchaudio==2.4.1
$PY -m pip install $PIPOPT xformers==0.0.28 || step "  WARN xformers 失败"
$PY -m pip install $PIPOPT triton==3.0.0 || step "  WARN triton 失败"
$PY -m pip install $PIPOPT sageattention==1.0.6 || step "  WARN sageattention 失败（回退 sdpa）"

step "4/6 requirements"
$PY -m pip install $PIPOPT -r $C/requirements.txt
cd $C/custom_nodes
for d in ComfyUI-WanVideoWrapper ComfyUI-VideoHelperSuite ComfyUI-KJNodes ComfyUI-Manager; do
  [ -f $d/requirements.txt ] && $PY -m pip install $PIPOPT -r $d/requirements.txt || true
done

step "5/6 模型"
M=$C/models
mkdir -p $M/diffusion_models $M/multitalk $M/text_encoders $M/vae $M/clip_vision $M/loras $M/wav2vec
ln -sf $OLD/InfiniteTalk/quant_models/infinitetalk_single_fp8.safetensors $M/diffusion_models/Wan2_1-InfiniTetalk-Single_fp16.safetensors
ln -sf $OLD/InfiniteTalk/quant_models/infinitetalk_single_fp8.safetensors $M/multitalk/Wan2_1-InfiniTetalk-Single_fp16.safetensors
ln -sfn $OLD/chinese-wav2vec2-base $M/wav2vec/TencentGameMate--chinese-wav2vec2-base
dl(){ step "  dl $(basename $2)"; curl -fL --retry 5 -C - --connect-timeout 20 -o "$2.part" "$1" && mv "$2.part" "$2"; }
dl https://hf-mirror.com/ComfyUI/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors $M/vae/Wan2_1_VAE_bf16.safetensors
dl https://hf-mirror.com/ComfyUI/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/clip_vision/clip_vision_h.safetensors $M/clip_vision/clip_vision_vit_h.safetensors
dl https://hf-mirror.com/ComfyUI/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5-xxl-enc-bf16.safetensors $M/text_encoders/umt5-xxl-enc-bf16.safetensors
dl https://hf-mirror.com/Kijai/WanVideo_comfy/resolve/main/Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank128_bf16.safetensors $M/loras/lightx2v_I2V_14B_480p_cfg_step_distill_rank128_bf16.safetensors
dl https://hf-mirror.com/ComfyUI/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/diffusion_models/wan2.1_i2v_480p_14B_fp8_e4m3fn.safetensors $M/diffusion_models/Wan2_1-I2V-14B-480P_fp8_e4m3fn.safetensors

step "6/6 验证"
$PY - <<'PYEOF'
import torch
print("torch", torch.__version__, "cuda", torch.cuda.is_available())
for m in ("sageattention", "xformers"):
    try:
        __import__(m); print(m, "OK")
    except Exception as e:
        print(m, "MISSING", e.__class__.__name__)
PYEOF
step "SETUP_ALL_DONE"
