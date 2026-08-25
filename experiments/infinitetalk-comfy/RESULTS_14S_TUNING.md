# Wan2.1 + InfiniteTalk 14s 调参实验归档（2026-08-25）

分支：`wan2.1andinfinitetalk`（已归档，不合并到 main）

## 实验目的

验证 Wan2.1 + InfiniteTalk 能否通过调参解决"动作幅度大"的问题，
评估其作为 LatentSync 替代方案的可行性。

## 背景

- 6s 片段（8 步 480×832 竖屏）口型质量和身份保真度已验证通过
- 8 步版本皮肤纹理接近真实（毛孔/胡茬可辨）
- 核心问题：**动作幅度太大，手部/身体晃动明显**

## 实验设计

### 素材

- 源视频：`source_full.mp4`（29s, 720×1280, 25fps）
- 驱动音频：`driving_14s.wav`（从 driving_full.wav 裁剪 14s）
- 分辨率：480×832（竖屏）
- 步数：8 步
- 帧数：449（round(14 × 32) + 1）

### 调参矩阵

| # | 名称 | audio_scale | motion_frame | denoise | 提示词 |
|---|------|------------|-------------|---------|--------|
| A | baseline | 1.0 | 9 | 1.0 | 原始 |
| B | calm_audio | **0.5** | 9 | 1.0 | 原始 |
| C | calm_full | **0.5** | **5** | **0.9** | 加强版（"身体保持静止，手部不动"） |

### 服务器

- SSH：`ssh -p 34300 root@219.147.100.42`
- 工作目录：`/root/siton-tmp/aigc/`
- ComfyUI：`/root/siton-tmp/aigc/ComfyUI-Infinitetalk/`
- 提交方式：`venv-comfy/bin/python submit_and_wait.py <workflow.json>`

### 耗时

| 实验 | 耗时 | 文件大小 |
|------|------|----------|
| A baseline | 14.3 min | 2.0M |
| B calm_audio | ~12 min | 2.7M |
| C calm_full | ~20 min | 2.1M |

## 实验结果

### A baseline（原参数）

- 与之前 6s 片段一致，动作幅度大
- 口型自然，身份保真度好
- 作为对照组

### B calm_audio（audio_scale=0.5）

- ✅ 动作幅度确实减小
- ❌ **面部表情过多**：眉毛一直挑，模型自带"表演欲"
- ❌ **口型不同步**：念身份证号码时嘴动过快，与音频不匹配
- ❌ **身份证信息变化**：证件上的文字被重新生成，内容改变
- ❌ **说话前大喘气**：非语音段的自由发挥

### C calm_full（全降 + 加强提示词）

- ❌ **手直接离开身份证**：denoise=0.9 + motion_frame=5 过度抑制
- 完全不可用

## 结论：方案不适用于当前场景

### 根本原因

**Wan2.1 + InfiniteTalk 是整帧重绘架构**，与 LatentSync 的局部修改架构有本质区别：

```
LatentSync：只改嘴部区域 → 身份证文字 100% 保留 ✅
Wan2.1+IT：整帧重绘 → 身份证文字被"重新生成" ❌
```

### 不可调参解决的问题

| 问题 | 原因 |
|------|------|
| 身份证文字变化 | 整帧重绘必然重绘文字，无区域保护机制 |
| 表情过多 | 模型生成特性，提示词控制力有限 |
| 口型-音频同步精度 | audio_scale 降→同步降，是 tradeoff 不是可解问题 |

### 可调参解决的问题（但意义有限）

| 问题 | 可调 | 但... |
|------|------|-------|
| 动作幅度 | ✅ audio_scale / motion_frame | 降到可接受时其他问题暴露 |
| 大喘气 | ⚠️ 裁掉音频开头静音段 | 治标不治本 |

### LatentSync vs Wan2.1+IT 对比（手持证件场景）

| 维度 | LatentSync 1.6 | Wan2.1 + InfiniteTalk |
|------|---------------|---------------------|
| 证件文字保真 | ✅ 100% 原始 | ❌ 被重绘 |
| 嘴部画质 | ★★★★ 512×512 | ★★★★★ 480×832 但整帧重绘 |
| 动作幅度 | ✅ 原视频动作不变 | ❌ 需要调参且 tradeoff 大 |
| 身份保真 | ✅ 原始像素 | ✅ 但周围细节被重绘 |
| 口型同步 | ✅ 精确 | ⚠️ 降幅度后精度下降 |
| 分辨率 | 512×512 → 需后处理 | 480×832（原生更高） |
| 速度 | ~8s/1s 视频 | ~1.2s/1s 视频（更快） |
| 显存 | ~24GB | ~24GB |

## 适用场景判断

**Wan2.1 + InfiniteTalk 适合：**
- 纯口播（无手持文字物品）
- 虚拟主播
- 短视频生成
- 对"AI 质感"可接受的场景

**不适合：**
- ❌ 人物手持身份证/证书/广告牌（文字必须不变）
- ❌ 需要严格保持原视频非口型区域不变的场景

## 后续方向

- ✅ **继续投入 LatentSync 1.6**：后处理增强（超分辨率 + 高频纹理回贴 + 泊松融合）
- 🔒 本分支归档保留，不合并到 main
- 📌 若未来遇到"纯口播无证件"场景，可回到本分支继续探索

## 归档文件清单

```
experiments/infinitetalk-comfy/
  workflow_it14s_a_baseline.json        # A 组工作流
  workflow_it14s_b_calm_audio.json      # B 组工作流
  workflow_it14s_c_calm_full.json       # C 组工作流
  RESULTS_14S_TUNING.md                 # 本文档
  RESULTS.md                            # 6s 阶段实验记录（2026-08-24）
  source_6s.mp4 / driving_6s.wav        # 6s 素材

jobs-cloud/it14s-experiments/
  it14s_a_baseline_00001-audio.mp4      # A 组输出
  it14s_b_calm_audio_00001-audio.mp4    # B 组输出
  it14s_c_calm_full_00001-audio.mp4     # C 组输出

config/job.cloud6.yaml                  # wlh-006 任务配置（参考）
```

## 服务器状态（截至 2026-08-25 19:00）

- ComfyUI 仍在运行（端口 8188）
- 服务器上输出文件位于 `/root/siton-tmp/aigc/ComfyUI-Infinitetalk/output/`
- 可通过 `start_comfy.sh` 启动 ComfyUI
