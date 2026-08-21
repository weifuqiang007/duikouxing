# LatentSync 1.6 参数调优手册

> 基于 2026-08-19 ~ 2026-08-21 云端 RTX 4090 实验总结。

---

## 一、全部可调参数

### A. 推理核心参数（lipsync 段）

| 参数 | 默认值 | 范围 | 含义 | 对嘴部的影响 |
|------|--------|------|------|-------------|
| `guidance_scale` | 1.3 | 1.0~3.0 | CFG 引导权重。控制"跟着音频走"vs"自由生成"的平衡。=1.0 时无引导 | **最核心参数**。太低→嘴不动；太高→过冲、闪烁、牙齿异常。**推荐 1.5** |
| `inference_steps` | 30 | 20~50 | 去噪步数。步数越多生成质量越高，但速度线性下降 | 间接影响清晰度。30 已是好的平衡点，提到 40 在嘴部边缘细节上有轻微改善。超过 40 收益极小 |
| `seed` | 1247 | 任意整数 | 随机种子。控制初始噪声 | 不同 seed 嘴部幅度和时序会有细微差异，可用于挑选最佳结果 |
| `audio_amp` | 1.0 | 0.5~2.0 | **自定义旋钮**：放大 Whisper 音频特征的幅度。实现见 `patches/latentsync-audio-amplitude.patch` | **直接放大嘴部开合驱动信号**。1.3 是实验验证的最佳值；>1.5 会过冲、闪烁 |
| `enable_deepcache` | false | — | DeepCache 加速，每 3 帧缓存一次 UNet 分支 | **会降低嘴部质量**，不建议在最终交付中使用 |

### B. UNet 配置层（stage2_512.yaml，一般不改）

| 参数 | 值 | 含义 |
|------|------|------|
| `num_frames` | 16 | 每个推理窗口的帧数（25fps 下 = 0.64 秒） |
| `resolution` | 512 | 生成分辨率（512×512 裁剪人脸区域） |
| `audio_feat_length` | [2, 2] | 音频上下文窗口：当前帧前后各取 2 帧的 whisper 特征 |
| `cross_attention_dim` | 384 | Whisper tiny 的特征维度 |

### C. TTS 参数（tts 段）

| 参数 | 默认值 | 含义 |
|------|--------|------|
| `max_chars_per_segment` | 60 | 每段 TTS 最大字符数，超过则按标点切分。**不要降到 40**，会导致 dots.tts 第一段输出截断（实验证实仅生成 0.16 秒） |
| `guidance_scale` | 1.2 | TTS 的声音克隆引导权重 |
| `seed` | 42 | TTS 随机种子，每段自动 +index 避免重复 |

### D. 视频参数（video 段）

| 参数 | 默认值 | 含义 |
|------|--------|------|
| `fps` | 25 | 输出帧率 |
| `duration_policy` | pingpong | 视频比音频短时的循环策略（pingpong=正反交替） |
| `final_crf` | 12 | 最终 MP4 的 H.264 CRF 质量值。**对 LatentSync 路径无效**（视频流直接 copy），仅 MuseTalk 路径生效。LatentSync 内部已用 CRF 13 编码 |

### E. composite 参数（仅 MuseTalk 路径，LatentSync 不涉及）

| 参数 | 默认值 | 含义 |
|------|--------|------|
| `mode` | native | LatentSync 使用 native（不经过 composite） |
| `texture_strength` | 0.0 | 纹理强度 |
| `detail_sigma` | 1.2 | 细节模糊 |
| `mask_feather_pixels` | 6 | 遮罩羽化 |
| `temporal_ema` | 0.80 | 时序 EMA 平滑 |
| `optical_flow` | true | 光流追踪 |

---

## 二、实验结论

### 口型幅度实验（2026-08-19，wlh-004 任务组）

| 配置 | guidance_scale | audio_amp | 效果 |
|------|--------------|-----------|------|
| c3（基线） | 1.3 | 1.0 | 嘴几乎不张 ❌ |
| c4 | 1.5 | 1.0 | 小幅改善 |
| c5 | 1.8 | 1.0 | 明显改善（纯官方参数备选） |
| **c6（推荐）** | **1.5** | **1.3** | **开口幅度最接近可灵基准** ✅ |

**推荐交付参数：`guidance_scale: 1.5` + `audio_amp: 1.3` + `inference_steps: 40`**

- 若新客户出现过冲/闪烁：先降 `audio_amp` 到 1.0，再不行换 c5 参数（`guidance_scale: 1.8`、无补丁依赖）
- `inference_steps` 30→40 的提升在嘴部边缘细节上有轻微改善，40→50 收益极小，时间增加 1.6 倍

### max_chars_per_segment 实验（2026-08-21）

| 值 | TTS 第一段时长 | 结果 |
|----|---------------|------|
| 40 | **0.16 秒** | 严重截断，后续段内容被参考文本污染 ❌ |
| **60** | **9.44 秒** | 正常生成 ✅ |

**结论：必须保持 `max_chars_per_segment: 60`，不要降低。**

---

## 三、关于"念数字嘴张不开"的归因分析

### 用户猜测

> 是不是因为数字连读导致嘴张不开？

### 结论：部分反对。

### 真正的原因：数字音频能量太弱

脚本中使用 `1 5 2、6 3 0、 1 9 9 2、 1 0 1 8、 7 2 1 0` 格式，每个数字是**独立的短音节**（100-200ms）。问题不在"连读"，而在以下三个层面：

#### 1. 中文数字的语音特性

| 数字 | 拼音 | 口型特征 |
|------|------|----------|
| 一 | yī | 嘴唇几乎不大幅张开 |
| 五 | wǔ | 嘴唇几乎不大幅张开 |
| 二 | èr | 轻微张嘴 |
| 六 | liù | 轻微张嘴 |
| 三 | sān | 较小张嘴 |
| 九 | jiǔ | 较小张嘴 |
| 零 | líng | 微张 |

这些音本身开口幅度就小，尤其是"一"和"五"。

#### 2. Whisper 特征的能量映射

短促的数字音节 → 音频能量低 → Whisper 提取的 embedding 幅度小 → LatentSync 得到的"张嘴驱动信号"弱 → 嘴张不开。

#### 3. TTS 端的音量曲线问题

短数字序列 TTS 可能生成较为平缓的音高/音量曲线，缺乏自然说话时的重音变化，进一步弱化了音频驱动信号。

### 不是连读的证据

- script 里用 `、`（顿号）和空格分隔了每组数字，TTS 会自然产生停顿
- 真正"连读"的问题是**相反方向**的——如果写成"一百五十二万六千三百"这样的连续数字，嘴反而可能动得更多（因为"百""十""万"这些字的口型更大）

### 推荐优化方向

1. **`audio_amp: 1.3`**（已验证）——直接放大 Whisper 特征幅度，增强嘴部驱动
2. **`guidance_scale: 1.5`**（已验证）——提高音频引导权重，让模型更紧跟音频
3. **script 格式**——保持当前的顿号+空格分隔格式，给 TTS 足够的停顿时间

---

## 四、已知限制与注意事项

### audio_amp 实现机制

- 通过 `patches/latentsync-audio-amplitude.patch` 修改 `external/LatentSync/latentsync/whisper/audio2feature.py`
- 环境变量 `LATENTSYNC_AUDIO_AMP` 控制，默认 1.0 = 官方行为
- 适配器 `src/digital_human/adapters/latentsync.py` 从 job yaml 透传到环境变量
- `src/digital_human/process.py` 的 `run_command()` 支持 `env` 参数

### reference_text 必须准确

- `reference_text` 必须与 `reference_audio`（源视频提取的音频）内容**完全一致**
- 如果 reference_text 与实际音频不匹配，dots.tts 会生成错误内容（实验证实：reference_text 末尾多出"专业证书"导致整段音频内容被污染）

### CRF 对 LatentSync 无效

- LatentSync 内部以 `-c:v copy` 封装 CRF 13 视频，job yaml 中的 `final_crf` 不影响最终画质
- 仅在 MuseTalk 路径下 `final_crf` 才生效

### 服务器环境要点

- 无 conda 二进制 → 直接用 `.conda-envs/<env>/bin/python`
- 访问不了 huggingface.co → 必须 `HF_HOME=$PROJECT_ROOT/.cache/huggingface`
- conda 不在默认 PATH → `local.cloud.yaml` 中使用绝对路径 `/root/siton-tmp/miniconda3/bin/conda`
