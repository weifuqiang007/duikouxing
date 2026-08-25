# 4090 云服务器部署 ComfyUI + InfiniteTalk 快速验证手册

本文档用于在一台 NVIDIA RTX 4090 24GB 云服务器上快速验证 InfiniteTalk / WanVideo 的视频转数字人口型效果。

目标不是马上做完整工程化产品，而是先验证一个核心问题：

> 输入原始人物视频 + 新音频后，生成的视频口型是否自然，嘴部纹理是否明显优于当前 LatentSync 方案。

如果验证效果达标，再把该方案接入 `duikouxing2` 项目。

## 1. 适用场景

客户提供：

- 一段人物口播视频，人物动作基本不变。
- 人物可能手持广告牌、证书、身份证等物体。
- 一段替换话术，项目侧会先生成对应音频。

本次快速验证只测试：

- 原视频是否能保留人物形象和大体动作。
- 新音频是否能驱动自然口型。
- 嘴部是否还有明显糊、磨皮、贴片、椭圆蒙版感。
- 手持证件/广告牌区域是否被明显改坏。

## 2. 推荐服务器配置

推荐：

- GPU：RTX 4090 24GB
- 显存：24GB
- 内存：64GB 起，最低 32GB
- 磁盘：至少 200GB 可用空间，建议 SSD
- 系统：Ubuntu 22.04
- CUDA：12.1 或 12.x 驱动兼容环境

注意：

- 3060/4070 12GB 本地机器不建议作为本方案的主验证环境。
- 12GB 显存即使能跑，也会非常慢，且容易被迫使用更激进的 offload/量化，影响判断。

## 3. 目录规划

建议所有内容放在数据盘，不要放系统盘。

示例：

```bash
mkdir -p /data/aigc
cd /data/aigc
```

最终目录建议如下：

```text
/data/aigc/
  ComfyUI-Infinitetalk/
    input/
    output/
    models/
    custom_nodes/
  test_assets/
    source.mp4
    driving_audio.wav
    workflow_infinitetalk_v2v.json
```

## 4. 安装基础环境

先确认显卡和驱动：

```bash
nvidia-smi
```

期望能看到 RTX 4090 和 24GB 显存。

安装系统依赖：

```bash
sudo apt update
sudo apt install -y git git-lfs wget curl ffmpeg aria2 build-essential
git lfs install
```

安装 Miniconda 或确认已有 Conda：

```bash
conda --version
```

如果没有 Conda，请自行安装 Miniconda。安装后重新进入 shell。

## 5. 创建 Conda 环境

```bash
conda create -n comfy-infinitetalk python=3.10 -y
conda activate comfy-infinitetalk
```

安装 PyTorch：

```bash
pip install torch==2.4.1 torchvision==0.19.1 torchaudio==2.4.1 --extra-index-url https://download.pytorch.org/whl/cu121
```

验证：

```bash
python - <<'PY'
import torch
print("torch:", torch.__version__)
print("cuda:", torch.cuda.is_available())
print("gpu:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)
PY
```

## 6. 安装 ComfyUI

```bash
cd /data/aigc
git clone https://github.com/comfyanonymous/ComfyUI.git ComfyUI-Infinitetalk
cd /data/aigc/ComfyUI-Infinitetalk
pip install -r requirements.txt
```

启动测试：

```bash
python main.py --listen 0.0.0.0 --port 8188
```

浏览器打开：

```text
http://服务器IP:8188
```

确认能打开后，按 `Ctrl+C` 停止服务，继续安装节点。

## 7. 安装 ComfyUI Manager

```bash
cd /data/aigc/ComfyUI-Infinitetalk/custom_nodes
git clone https://github.com/ltdrdata/ComfyUI-Manager.git
```

后面如果工作流提示缺节点，可以通过 Manager 安装。

## 8. 安装工作流所需自定义节点

这个工作流 JSON 中出现了以下关键节点类型：

- `WanVideoModelLoader`
- `WanVideoVAELoader`
- `WanVideoImageToVideoMultiTalk`
- `WanVideoSampler`
- `MultiTalkModelLoader`
- `MultiTalkWav2VecEmbeds`
- `VHS_LoadVideo`
- `VHS_VideoCombine`
- `ImageResizeKJv2`
- `AudioSeparation`
- `SoundFlow_GetLength`
- `rgthree`
- `easy cleanGpuUsed`

建议先安装这些常用节点：

```bash
cd /data/aigc/ComfyUI-Infinitetalk/custom_nodes
git clone https://github.com/kijai/ComfyUI-WanVideoWrapper.git
git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git
git clone https://github.com/kijai/ComfyUI-KJNodes.git
git clone https://github.com/rgthree/rgthree-comfy.git
```

安装各节点依赖：

```bash
cd /data/aigc/ComfyUI-Infinitetalk/custom_nodes/ComfyUI-WanVideoWrapper
pip install -r requirements.txt

cd /data/aigc/ComfyUI-Infinitetalk/custom_nodes/ComfyUI-VideoHelperSuite
pip install -r requirements.txt

cd /data/aigc/ComfyUI-Infinitetalk/custom_nodes/ComfyUI-KJNodes
pip install -r requirements.txt

cd /data/aigc/ComfyUI-Infinitetalk/custom_nodes/rgthree-comfy
pip install -r requirements.txt || true
```

如果启动后仍提示缺少 `AudioSeparation`、`SoundFlow_GetLength` 或 `easy cleanGpuUsed`，先用 ComfyUI Manager 的 `Install Missing Custom Nodes` 自动安装。

## 9. 安装可选加速依赖

官方 InfiniteTalk 推荐环境中包含 `xformers`、`flash_attn` 等加速组件。ComfyUI 工作流能否使用取决于节点实现。

建议先安装：

```bash
pip install xformers==0.0.28
pip install flash_attn==2.7.4.post1 --no-build-isolation
```

如果 `flash_attn` 编译失败，不要卡死在这里。先跳过，启动 ComfyUI 继续验证。部分 WanVideo 节点也可用 `sageattn` 或普通注意力跑，只是速度会慢。

## 10. 权重文件清单

从工作流 JSON 看，至少需要以下模型文件：

```text
Wan2_1-InfiniTetalk-Single_fp16.safetensors
Wan2_1-I2V-14B-480P_fp8_e4m3fn.safetensors
Wan2_1_VAE_bf16.safetensors
clip_vision_vit_h.safetensors
umt5-xxl-enc-bf16.safetensors
lightx2v_I2V_14B_480p_cfg_step_distill_rank128_bf16.safetensors
TencentGameMate/chinese-wav2vec2-base
```

权重来源优先级：

1. InfiniteTalk 官方仓库 README 给出的 Hugging Face 下载命令。
2. ComfyUI-WanVideoWrapper README 给出的模型目录说明。
3. ComfyUI Manager 节点详情页提示的模型路径。

官方 InfiniteTalk 相关仓库：

```text
https://github.com/MeiGen-AI/InfiniteTalk
https://huggingface.co/MeiGen-AI/InfiniteTalk
https://huggingface.co/Wan-AI/Wan2.1-I2V-14B-480P
https://huggingface.co/TencentGameMate/chinese-wav2vec2-base
```

建议统一放在 ComfyUI 模型目录，不要放系统盘。

常见放置方式如下，具体以节点提示为准：

```text
/data/aigc/ComfyUI-Infinitetalk/models/diffusion_models/
  Wan2_1-I2V-14B-480P_fp8_e4m3fn.safetensors
  Wan2_1-InfiniTetalk-Single_fp16.safetensors

/data/aigc/ComfyUI-Infinitetalk/models/vae/
  Wan2_1_VAE_bf16.safetensors

/data/aigc/ComfyUI-Infinitetalk/models/clip_vision/
  clip_vision_vit_h.safetensors

/data/aigc/ComfyUI-Infinitetalk/models/text_encoders/
  umt5-xxl-enc-bf16.safetensors

/data/aigc/ComfyUI-Infinitetalk/models/loras/
  lightx2v_I2V_14B_480p_cfg_step_distill_rank128_bf16.safetensors

/data/aigc/ComfyUI-Infinitetalk/models/wav2vec/
  chinese-wav2vec2-base/
```

如果节点找不到模型：

- 先看 ComfyUI 控制台报错里的 expected path。
- 按报错路径移动或软链接模型。
- 不要重复下载到多个目录，优先用软链接。

示例软链接：

```bash
ln -s /data/models/Wan2_1-I2V-14B-480P_fp8_e4m3fn.safetensors /data/aigc/ComfyUI-Infinitetalk/models/diffusion_models/
```

## 11. 上传工作流和测试素材

将以下文件上传到服务器：

```text
workflow_infinitetalk_v2v.json
source.mp4
driving_audio.wav
```

建议：

- `source.mp4`：从客户测试视频中裁 5-8 秒。
- `driving_audio.wav`：与测试时长接近，推荐 16kHz 或 24kHz wav。
- 第一次不要跑完整 20-30 秒视频。

拷贝到 ComfyUI：

```bash
cp /data/aigc/test_assets/source.mp4 /data/aigc/ComfyUI-Infinitetalk/input/
cp /data/aigc/test_assets/driving_audio.wav /data/aigc/ComfyUI-Infinitetalk/input/
```

## 12. 启动 ComfyUI

```bash
cd /data/aigc/ComfyUI-Infinitetalk
conda activate comfy-infinitetalk
python main.py --listen 0.0.0.0 --port 8188
```

浏览器打开：

```text
http://服务器IP:8188
```

如果云服务器有安全组，请放行 TCP `8188`，或使用 SSH 端口转发：

```bash
ssh -L 8188:127.0.0.1:8188 用户名@服务器IP
```

然后本地浏览器打开：

```text
http://127.0.0.1:8188
```

## 13. 导入工作流并替换输入

在 ComfyUI 页面中：

1. 拖入 `workflow_infinitetalk_v2v.json`。
2. 找到“上传视频”节点，即 `VHS_LoadVideo`。
3. 替换成 `source.mp4`。
4. 找到“上传音频”节点，即 `LoadAudio`。
5. 替换成 `driving_audio.wav`。
6. 检查模型节点是否都能正常选到权重。
7. 点击 Queue Prompt 运行。

## 14. 第一轮快速验证参数

第一轮只验证能否跑通，不追求极限质量。

建议参数：

```text
分辨率：832 x 480
帧数：81
帧率：32 fps
steps：4
LoRA：lightx2v 开启
CRF：19
视频长度：5-8 秒
```

工作流中的对应节点：

```text
WanVideoImageToVideoMultiTalk:
  width = 832
  height = 480
  num_frames = 81
  mode = infinitetalk

WanVideoSampler:
  steps = 4
  cfg = 1.0
  shift = 11
  scheduler = dpm++_sde

VHS_VideoCombine:
  frame_rate = 32
  crf = 19
```

## 15. 第二轮质量验证参数

如果第一轮能跑通，但嘴部细节仍然不够好，再做第二轮。

建议测试：

```text
steps：6 或 8
LoRA：先保持开启；如细节异常，再尝试降低 LoRA 强度或关闭
CRF：17-19
视频长度：仍然只跑 5-8 秒
```

判断标准：

- 嘴唇边缘不能明显糊成一片。
- 下颌线不能像被抹掉。
- 嘴周皮肤纹理不能明显比原视频干净一大截。
- 不能出现明显椭圆贴片感。
- 证件/广告牌文字不能明显扭曲。

## 16. 输出位置

默认输出在：

```text
/data/aigc/ComfyUI-Infinitetalk/output/
```

下载输出文件后，与当前 LatentSync 结果做并排对比。

重点对比：

```text
当前 LatentSync:
  G:\duikouxing\jobs-office\wlh-004\output\final.mp4

InfiniteTalk 测试:
  ComfyUI output 目录下新生成 mp4
```

## 17. 验收标准

本阶段不是验收完整产品，只验收模型路线是否值得工程化。

达到以下标准，才进入 `duikouxing2` 工程接入：

- 口型和音频基本同步，无明显延迟。
- 嘴部没有明显椭圆蒙版感。
- 嘴唇、牙齿、下巴过渡自然。
- 嘴周纹理比 LatentSync 明显更自然。
- 人物身份保持良好。
- 原视频中的手、证件、衣服、背景没有严重变形。
- 5-8 秒片段单次生成成功率可接受。

如果以下情况严重，则暂不工程化：

- 嘴部仍然明显糊。
- 人脸身份变化大。
- 证件文字被大面积改坏。
- 口型同步明显不如 LatentSync。
- 4090 24GB 上频繁 OOM，无法稳定跑 5-8 秒。

## 18. 常见问题

### 18.1 缺少节点

表现：

```text
When loading the graph, the following node types were not found
```

处理：

1. 打开 ComfyUI Manager。
2. 点击 `Install Missing Custom Nodes`。
3. 安装后重启 ComfyUI。

### 18.2 找不到模型

表现：

```text
model not found
```

处理：

1. 看控制台报错中节点期望的目录。
2. 将模型移动到对应目录。
3. 或创建软链接。
4. 重启 ComfyUI。

### 18.3 CUDA out of memory

处理顺序：

1. 确认只跑 5-8 秒，不跑完整视频。
2. 保持 832x480。
3. 保持 fp8 主模型。
4. 增大 block swap。
5. 关闭其他占用 GPU 的进程。
6. 重启 ComfyUI。

查看显存：

```bash
nvidia-smi
```

### 18.4 flash_attn 安装失败

先跳过，不要卡住验证。

后续可尝试：

```bash
pip install ninja packaging
pip install flash_attn==2.7.4.post1 --no-build-isolation
```

### 18.5 输出视频没声音

检查：

- `VHS_VideoCombine` 的 audio 输入是否连接。
- `LoadAudio` 是否成功读取音频。
- 音频是否被 `AudioCrop` 截断。
- 输出节点是否选择保存最终带音频的视频。

### 18.6 生成内容和原视频差异太大

处理：

- 降低生成长度，先跑短片段。
- 检查 prompt 是否过于离谱。
- 使用更贴近原视频的 prompt，例如“一个男人正在面对镜头说话，室内白色背景，动作保持稳定”。
- 后续工程化时加入原视频局部回贴保护。

## 19. 官方脚本兜底验证

如果 ComfyUI 工作流长时间无法跑通，可以用 InfiniteTalk 官方仓库先做兜底验证。

```bash
cd /data/aigc
git clone https://github.com/MeiGen-AI/InfiniteTalk.git
cd InfiniteTalk
conda create -n infinitetalk python=3.10 -y
conda activate infinitetalk
pip install torch==2.4.1 torchvision==0.19.1 torchaudio==2.4.1 --extra-index-url https://download.pytorch.org/whl/cu121
pip install -r requirements.txt
```

按官方 README 下载模型权重后，使用官方 video-to-video 示例测试。

官方仓库：

```text
https://github.com/MeiGen-AI/InfiniteTalk
```

注意：

- 官方脚本验证更接近论文/项目原始效果。
- ComfyUI 工作流更方便后续产品化和可视化调参。
- 如果两者效果差异很大，以官方脚本效果作为模型能力上限判断。

## 20. 给执行模型/运维的交付物

部署完成后，请回传以下内容：

```text
1. nvidia-smi 截图或文本
2. ComfyUI 启动日志
3. 已安装 custom_nodes 列表
4. models 目录树
5. 第一轮 4 steps 输出视频
6. 第二轮 6/8 steps 输出视频
7. 是否出现 OOM 或缺节点
8. 最终推荐参数
```

目录树命令：

```bash
find /data/aigc/ComfyUI-Infinitetalk/custom_nodes -maxdepth 1 -type d
find /data/aigc/ComfyUI-Infinitetalk/models -maxdepth 2 -type f | head -200
```

## 21. 后续工程化方向

如果验证通过，再在 `G:\duikouxing2` 中做新分支工程化。

工程化模块建议：

```text
configs/
  engine_infinitetalk_4090.yaml

docs/
  ARCHITECTURE_INFINITETALK.md
  QUICKSTART_COMFYUI_INFINITETALK.md

src/
  engines/
    infinitetalk_comfyui.py
  pipelines/
    run_infinitetalk_job.py
  postprocess/
    protect_original_regions.py
```

工程化时的主流程：

```text
客户视频
  -> 生成或克隆新音频
  -> 调用 ComfyUI InfiniteTalk 工作流
  -> 输出初版口型视频
  -> 原视频非脸部区域回贴
  -> 颜色匹配和锐化
  -> 合成最终视频
```

本阶段不要提前重写主项目。先验证模型效果，再决定是否接入。
