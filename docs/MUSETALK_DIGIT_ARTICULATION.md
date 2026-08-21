# MuseTalk 数字吐字与口型幅度调参手册

> 结论来源：wlh 系列任务实测（office 机 RTX 3060，MuseTalk v1.5 + dots.tts SOAR）。
> 关联文档：[三引擎对比](LIPSYNC_ENGINE_COMPARISON.md)。本文专注 MuseTalk 链路里
> "念数字嘴张不开"这一类问题的定位与调参顺序。

## 问题

数字人播报身份证号等数字串时，嘴部开合幅度明显偏小，观感"含糊"。

## 根因链

MuseTalk 的嘴部动作完全由 whisper 特征驱动（**没有** LatentSync 那个 `audio_amp`
幅度旋钮——那是 LatentSync audio2feature 的专属补丁）。嘴张不开是三层因素叠加：

1. **TTS 层（主因）**：数字串快速连读时每个音节只有 ~100ms，元音开口度没起来
   就滑过去，whisper 特征里的"开口"信号弱。
2. **合成层**：`composite.texture_strength` 偏高时，原片（闭嘴状态）的高频纹理
   会把 MuseTalk 的开嘴形变往回拉。
3. **裁剪层**：`lipsync.extra_margin` 偏小时，大张嘴的下巴运动被裁剪框截断。

## 已验证：文本停顿改法（最便宜、先做）

| 版本 | 话术写法 | 成片时长 | 数字段抽帧观察 |
|---|---|---|---|
| wlh-004 | 数字间**纯空格**：`1 5 2 6 3 0 1 9 9 2 ...` | 28.96s | 开口帧少、幅度小，仍含糊 |
| wlh-005 | 数字间空格 + **组间顿号**：`1 5 2、6 3 0、1 9 9 2、1 0 1 8、7 2 1 0` | 29.76s | 开口帧数量与幅度明显提升 ✅ |

顿号分组长出的 ~0.8s 就是新增组间停顿，证明 TTS 确实按分组加重了停顿；音节
拉长后 whisper 开口信号变强，嘴动自然跟上。对承诺类视频，数字逐组念出也更正式。

**规范**：数字间只空格、组间只顿号；顿号后**不要再加空格**（wlh-005 话术里
`、 ` 混用了，若听感停顿不齐可去掉顿号后的空格再验一次）。

另一个有利条件：参考音频本身就是逐字念数字的，克隆语音会模仿参考的节奏，
文本写法与参考节奏双向一致时效果最好。

## 排查顺序（成本从低到高）

| 步骤 | 动作 | 成本 | 验证什么 |
|---|---|---|---|
| 1 | 话术改顿号分组（见上） | 只改文本 | TTS 停顿是否够（已验证有效） |
| 2 | `refine` 命令 A/B `texture_strength` 0.55 → 0.45 | 几分钟，不重跑 GPU | 纹理回填是否在拉扯开嘴形变 |
| 3 | `tts.guidance_scale` 1.2 → 1.3~1.4 | 全量重跑 | 吐字是否更有力 |
| 4 | 换 `tts.seed`（如 42 → 1234） | 全量重跑 | 重抽吐字力度（TTS 有随机性） |
| 5 | `lipsync.extra_margin` 5 → 10 | 全量重跑 | 仅当嘴形有"被压扁/截断"观感 |

refine 用法（复用 wlh-005 的 MuseTalk 结果，只重跑合成与封装）：

```powershell
# 先把 config/job.local.yaml 的 composite.texture_strength 改成 0.45
& $Python -m digital_human.cli refine --profile office --job .\config\job.local.yaml --output .\jobs-office\wlh-005\output\final-ts045.mp4
```

## wlh-005 交付基线参数快照（2026-08-21 验证，用户认可）

复现整套效果所需的完整参数。任务配置在 `config/job.local.yaml`（当前文件即此状态）：

```yaml
job_id: "wlh-005"
source_video: "../samples/wlh.mp4"
reference_audio: ""                # 从源视频截取
reference_start_seconds: 0.0
reference_duration_seconds: 14.0

tts:
  profile: "auto"                  # office 机解析为 quality（dots.tts SOAR）
  language: "ZH"
  max_chars_per_segment: 60
  guidance_scale: 1.2              # 1.3 已证伪：压停顿、反作用
  seed: 42                         # 换 seed 已证伪：无增益

video:
  fps: 25
  duration_policy: "pingpong"
  final_crf: 12

lipsync:                           # engine 缺省 = musetalk_1_5
  parsing_mode: "jaw"
  extra_margin: 5
  left_cheek_width: 60
  right_cheek_width: 60

composite:
  mode: "dynamic_texture"
  texture_strength: 0.55           # 0.45 已证伪：无开口增益，肤质更差
  detail_sigma: 1.2
  mask_feather_pixels: 6
  temporal_ema: 0.80
  optical_flow: true
  detect_interval: 5
  change_threshold: 2.5

mouth_roi:                         # dynamic_texture 下仅作检测失败兜底
  center_x: 0.5181
  center_y: 0.2992
  width: 0.275
  height: 0.0969
  feather_pixels: 8
```

机器侧（`config/local.office.yaml` runtime）：`musetalk_batch_size: 2` /
`use_float16: true` / `gpu_id: 0` / `tts_profile: quality`（RTX 3060）。

话术写法（本基线的组成部分，勿改动数字分隔）：
`身份证1 5 2、6 3 0、1 9 9 2、1 0 1 8、7 2 1 0`（数字间空格、组间顿号）。

产物：`jobs-office/wlh-005/output/final.mp4`（29.76s）。

## 已知坑

- **dots.tts 模型加载崩溃：`OSError: 页面文件太小，无法完成操作 (os error 1455)`**
  —— Windows 提交内存（物理内存 + 页面文件）不足。dots.tts 用 safetensors
  内存映射加载模型，需要大量 commit 配额；后台应用多（Chrome/微信/抖音等）
  内存压力大时必现，压力小时能过，表现为"偶发"。流水线按产物断点续跑，
  直接重跑即可续上，但根治需要**调大页面文件**（系统属性 → 高级 → 性能 →
  虚拟内存，改大或设为系统管理）或跑任务前关闭吃内存的应用。
- 每次改话术/参数换实验必须**换新 job_id**（如 `wlh-006-g13`），否则指纹校验
  拦截或 `--force` 覆盖旧结果。
- `refine` 出片默认 CRF 18（正式流水线 `final_crf` 12），对比纹理清晰度时
  注意码率混杂，嘴部开合对比不受影响。

## 实验记录

2026-08-21 同一话术单变量对照（office 机，抽帧观察数字段 0~8s，最终以播放观感为准）：

| job_id | 变量 | 时长 | 输出 | 结论 |
|---|---|---|---|---|
| wlh-004 | 纯空格数字 | 28.96s | `jobs-office/wlh-004/output/final.mp4` | 开口不足 |
| wlh-005 | 顿号分组 | 29.76s | `jobs-office/wlh-005/output/final.mp4` | 明显改善 ✅ 当前最优基线 |
| wlh-005 refine | texture_strength 0.55→0.45 | 29.76s | `jobs-office/wlh-005/output/final-ts045.mp4` | 开口幅度与 0.55 相近；纹理更平滑（注意 CRF 18 混杂），无明确增益 |
| wlh-006-g13 | guidance_scale 1.2→1.3 | 28.48s | `jobs-office/wlh-006-g13/output/final.mp4` | 语速更紧凑、停顿被压短，数字段开口帧反而减少 ❌ 不推荐 |
| wlh-007-s1234 | seed 42→1234 | 28.80s | `jobs-office/wlh-007-s1234/output/final.mp4` | 与 wlh-005 相近、无明确增益，纯抽签波动 |

**结论**：决定数字段开口度的是**话术停顿写法**（顿号分组），不是 TTS 采样参数——
guidance 升到 1.3 反而压缩停顿起反作用，seed 只是重抽。后续同类问题直接改话术，
参数层面维持 guidance 1.2 / seed 42 基线即可。
