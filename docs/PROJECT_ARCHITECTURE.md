# 项目架构书：真人表演驱动 + 面部动作迁移 + 原视频回贴

## 1. 项目背景与目标

输入是客户单人固定机位视频，人物可能手持广告牌、证书或身份证，身体动作近似不变。客户提供替换话术；客户原视频音轨作为音色参考；操作者用iPhone跟随最终客户音色音频录制表演。

系统只改变客户人物的口唇、脸颊、下颌相关表情和声音，保留原视频的身份外观、头部姿态、身体、手、手持物、背景、画幅和时间顺序。质量目标优先于速度。

不做：整脸换脸、身体替换、背景替换、主动修改证件内容、直播实时推理、多人物选择。

## 2. 总体数据流

```text
客户source.mp4 ──提取参考声音──> dots.tts ──新话术──> recording_guide.wav
       │                                                  │
       │                                                  └──操作者同步跟录──> driving.mov
       │                                                                       │
       └──归一化/匹配目标时长──> source_25fps ──────────────┐                  │
                                                            ▼                  ▼
                                          LivePortrait source-video + driving-video
                                                            │
                                         regional expression/lip motion transfer
                                                            │
                                            stitching + official paste-back
                                                            │
                                            liveportrait_pasteback.mp4
                                                            │
                                 recording_guide.wav ──重新封装──> final.mp4
```

## 3. 两阶段工作流

阶段A的 `prepare-driving` 从客户视频截取参考音频，使用 `dots.tts-soar` 按 `script` 合成最终客户音色音轨，并输出录制文本。此时 `driving_video` 可为空。

阶段B中操作者跟随阶段A音频录制。`run` 检查驱动视频存在、时长误差不超过阈值，将客户视频和驱动视频统一为25fps和相同总时长，再调用固定提交的LivePortrait。最终强制使用阶段A音频，驱动视频原音轨被忽略。

## 4. 核心算法决策

1. `source` 是客户逐帧原视频而不是单张照片，因此身体、手持物和源头部动作按帧保留。
2. `flag_relative_motion=true`，将驱动者相对首帧的表情变化应用到客户对应帧，降低身份泄漏。
3. `animation_region=exp` 为默认值，包含嘴部及必要的脸颊/下颌联动；`lip` 仅用于A/B。
4. 配置禁止 `pose` 和 `all`，不迁移操作者的头部旋转、平移或尺度。
5. 官方 `flag_lip_retargeting` 是WIP开关，本项目不启用；使用regional control。
6. `flag_stitching=true`、`flag_pasteback=true`，使用官方随人脸变换的回贴，不再使用固定椭圆ROI。
7. 驱动总时长仅允许小幅全局 `setpts` 修正；超过12%直接失败，防止用拉伸掩盖错误跟读。
8. 最终音频始终重新mux，避免LivePortrait选择客户旧音轨或操作者音轨。

## 5. 代码层次与职责

| 层 | 文件 | 职责 |
|---|---|---|
| CLI | `src/digital_human/cli.py` | `doctor`、`prepare-driving`、`run`、错误码 |
| 配置 | `src/digital_human/config.py` | 双机配置、输入解析、区域/倍率/时长阈值校验 |
| 编排 | `src/digital_human/pipeline.py` | 两阶段产物、幂等、任务指纹、后端选择、manifest |
| 声音 | `src/digital_human/adapters/dots_tts.py` | 客户参考声音到新话术音频 |
| 动作迁移 | `src/digital_human/adapters/liveportrait.py` | 官方固定版本CLI、区域迁移、结果定位 |
| 媒体 | `src/digital_human/ffmpeg.py` | 音频提取/拼接、帧率与时长归一、最终mux |
| 进程 | `src/digital_human/process.py` | Conda prefix隔离执行、UTF-8日志、失败回显 |
| 安装 | `scripts/setup_liveportrait.ps1` | Python 3.10.13、官方仓库固定提交、依赖 |
| 权重 | `scripts/download_liveportrait_models.ps1` | 下载并检查官方目录结构 |
| 用户流程 | `scripts/prepare_driving.ps1`、`scripts/run_job.ps1` | 两阶段PowerShell入口 |

## 6. 运行目录与可追溯性

任务根目录为 `jobs-office/<job_id>` 或 `jobs-home/<job_id>`。输入复制到任务目录；中间文件与日志不覆盖原素材。`manifest.json` 记录source、reference、driving、output的哈希、任务参数、后端版本和机器配置。相同 `job_id` 的输入变化会失败，除非明确使用 `--force`。

## 7. 配置策略

- `local.office.yaml`：RTX 3060 12GB，写入 `jobs-office`。
- `local.home.yaml`：RTX 4070 12GB，写入 `jobs-home`。
- 路径均相对配置文件解析，不写死盘符，因此可复制到云服务器新挂载点。
- 环境、缓存、仓库和权重必须位于项目根目录；配置加载器拒绝环境/仓库越界路径。

## 8. 失败策略

源/驱动视频缺失、授权标志未确认、参考文字/话术为空、驱动时长误差超标、GPU不可用、提交不匹配、权重缺失、没有生成唯一成片、相同任务ID输入变化时，必须失败而不是静默降级。

## 9. 后续开发边界

优先扩展：自动三倍率A/B、音素级跟录评分、关键点轨迹质量报告、长视频分段与无缝拼接、可商用检测器替换。不要首先加入新的固定ROI、美颜、人脸增强或整脸换脸，这些会重新引入纹理和身份问题。
