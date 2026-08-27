# 照片换人实验 — DreamID-V 全记录

> 日期: 2026-08-27
> 服务器: 219.147.100.42:34300 (DreamID-V 容器, RTX 4090 24GB)
> 分支: codex/dreamidv-faceswap

## 一、目标

用一张证件照 (11.png) 替换驱动视频 (wlh.mp4) 中的人脸，生成完整长视频（13.68s），保留原音频。

## 二、技术选型：DreamID-V Faster

DreamID-V 是基于 Wan 2.1 的**端到端换脸视频生成**模型（非后处理贴图）。

- **仓库**: DreamID-V（`/root/siton-tmp/DreamID-V`）
- **变体**: `dreamidv_faster` — 加速版，基于 Wan 2.1 1.3B
- **任务**: `--task swapface`
- **原理**: 参考人脸 → 身份特征提取 + 驱动视频 → DWPose 姿态提取 → 扩散模型生成新视频
- **与 FaceFusion 的区别**: FaceFusion 是后处理贴图式换脸；DreamID-V 是生成式，整帧重绘

### 环境配置

| 组件 | 路径 |
|------|------|
| Conda 环境 | `/root/siton-tmp/envs/dreamidv/` (Python 3.11) |
| Wan 2.1 权重 | `/root/siton-tmp/dreamidv/weights/wan2.1-1.3B/` (VAE + DiT + T5-XXL) |
| DreamID-V 权重 | `/root/siton-tmp/dreamidv/weights/dreamidv/dreamidv_faster.pth` (5.3GB) |
| 参考人脸 | `/root/siton-tmp/dreamidv_input/11.png` (证件照截取) |
| 驱动视频 | `/root/siton-tmp/dreamidv_input/wlh.mp4` (720×1280, 30fps, 13.68s, 409帧) |

## 三、遇到的问题与修复

### 问题 1: Flash Attention 2 未安装 → AssertionError

**现象**: 进程启动后立即崩溃，4 个僵尸 python 进程

**错误**:
```
File ".../attention.py", line 112, in flash_attention
    assert FLASH_ATTN_2_AVAILABLE
AssertionError
```

**原因**: dreamidv conda 环境未安装 `flash-attn`。`model.py` 直接调用 `flash_attention()`，该函数在无 FA2 时直接 assert 失败。

**修复**: 将 `model.py` 中的 `flash_attention()` 调用改为 `attention()`。后者有 fallback 到 PyTorch 原生 `scaled_dot_product_attention`（通过 SDPA 后端，实际仍会使用高效的注意力实现）。

```bash
# model.py line 10: import
- from .attention import flash_attention
+ from .attention import attention

# model.py lines 147, 177, 216: 调用
- x = flash_attention(
+ x = attention(
```

**影响**: 无性能退化。PyTorch 2.5 + CUDA 12.1 的 SDPA 在 A100/4090 上同样使用 Flash Attention 内核。

### 问题 2: 长视频需分段生成

**现象**: DreamID-V 默认生成 81 帧（24fps = 3.375s），无法直接处理 13.68s 视频。

**参数限制**: `--frame_num` 需满足 `4n+1` 格式。增大 frame_num 会线性增加显存（81帧已用 21.5GB/24GB）。

**方案**: 分段处理
```bash
# 将 13.68s 视频切为 5 段（每段 ~3.375s）
for i in 0..4; do
    ffmpeg -ss ${i*3.375} -t 3.375 -i wlh.mp4 segments/seg_${i}.mp4
    dreamidv_faster.py --ref_video segments/seg_${i}.mp4 --save_file seg_${i}.mp4
done
# 拼接 + 贴回原音频
ffmpeg -f concat -i concat.txt -i wlh.mp4 -map 0:v -map 1:a -shortest output.mp4
```

**耗时**: 每段 ~5 分钟（DWPose ~2min + 扩散采样 ~2.5min），5 段共 ~25 分钟。

## 四、实验结果

### 生成成功但存在三个严重问题

#### 问题 A: 口型完全不对 ❌

**根因**: DreamID-V **不是口型同步模型**。它通过 DWPose 提取驱动视频的姿态骨架，但 DWPose 的嘴部关键点很粗糙，**不感知音频**。生成的嘴部动作是姿态估算的近似，与实际语音完全对不上。

**结论**: 口型同步必须用专门的 lip sync 模型（LatentSync / MuseTalk），DreamID-V 无法胜任。

#### 问题 B: 分段拼接处脸部跳变 ❌

**根因**: 每段由扩散模型独立生成，拼接边界处脸部位置/表情/光照不连续。

**可能的改进**:
- 交叉淡入淡出（crossfade 0.5s）
- 增大分段重叠区，混合过渡

#### 问题 C: 证件照高光导致油光肤质 ⚠️

**根因**: 参考图 11.png 从身份证截取，面部反光严重。DreamID-V 将高光作为身份特征的一部分学入。

**可能的改进**:
- 预处理参考图：频率分离去高光
- 后处理：CodeFormer/GFPGAN 面部修复
- 调低 `--sample_guide_scale_img`（当前 4.0）

## 五、正确的管线定位

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  原始视频    │────▶│ FaceFusion   │────▶│ LatentSync  │──▶ 最终视频
│  + 参考照片  │     │ 或 DreamID-V │     │ (口型同步)  │
└─────────────┘     │  (换脸)       │     └─────────────┘
                    └──────────────┘
```

| 步骤 | 模型 | 职责 |
|------|------|------|
| 1. 换脸 | DreamID-V / FaceFusion | 把原视频人脸替换为目标人物 |
| 2. 口型同步 | **LatentSync** | 让嘴型跟音频精确对上 |

**DreamID-V 只解决换脸这一步，口型必须由 LatentSync 交付。这是模型架构决定的，不是调参能修的。**

## 六、下一步：DreamID-V 换脸 + LatentSync 口型修复

### 方案 A（当前执行）

在 DreamID-V 换脸结果上跑 LatentSync 做口型修复：
- 输入：DreamID-V 生成的换脸视频 + 原始音频
- 目标：嘴型与音频同步，同时保持换脸后的身份和肤色一致
- 注意：LatentSync 的嘴部区域需要与周围肤色匹配，避免贴片感

### 方案 B（备选）

放弃 DreamID-V，走已有的 FaceFusion + LatentSync 管线（混合合成路线 v7.1）。

## 七、生成参数记录

```bash
/root/siton-tmp/envs/dreamidv/bin/python generate_dreamidv_faster.py \
  --task swapface \
  --size 832*480 \
  --ckpt_dir /root/siton-tmp/dreamidv/weights/wan2.1-1.3B \
  --dreamidv_ckpt /root/siton-tmp/dreamidv/weights/dreamidv/dreamidv_faster.pth \
  --ref_image /root/siton-tmp/dreamidv_input/11.png \
  --ref_video /root/siton-tmp/dreamidv_input/wlh.mp4 \
  --t5_cpu \
  --sample_steps 30 \
  --save_file /root/siton-tmp/dreamidv_output/wlh_result.mp4
```

- `--t5_cpu`: T5-XXL 放 CPU（11GB），否则 24GB 显存装不下全部模型
- `--sample_steps 30`: 采样步数，每步 ~4.7s
- `--offload_model`: 自动启用（True），模型用完即卸载
- 单段生成耗时：~2 分 21 秒（81 帧，24fps）
- 显存占用：21.5GB / 24GB