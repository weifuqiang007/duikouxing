# 算法学习计划：dots.tts 精读 + 流水线架构学习

> **本文档用途**：给未来会话的 AI（和新会话的用户）做上下文恢复。
> 用户目标：以"算法老师带学生"的模式，逐模块精读本项目算法实现，
> 最终目的是**封装本项目 + 与另一分支 merge**。
> 新会话开始时，请先读本文档，再根据"当前进度"一节继续，不要重复已完成的工作。

---

## 1. 用户是谁 & 想要什么

- 用户是本项目作者（git user: 魏富强），但对自己项目的**底层算法实现细节**还不熟。
- 明确要求的教学模式：
  - **一点一点讲**，不要一次灌全部。
  - 讲"算法是怎么实现的"，不是讲"怎么用"。
  - 用户会随时提问，AI 需要结合**本仓库实际源码**讲，不要泛泛而谈。
- 长期工程目标：**封装 + 跨分支 merge**（详见 §6）。

## 2. 项目一句话概括

换台词数字人系统：给定人物竖屏视频 + 新话术文本，
→ dots.tts 克隆声音生成新音频
→ LatentSync 1.6 音频驱动口型同步
→ ffmpeg 封装输出 `final.mp4`。

实现分支：`feature/idcard-faceswap`（当前所在分支）。

## 3. 已完成的学习进度

### ✅ 第 0 步：执行链路全景（已完成，2026-09-09）

已完整追踪并讲解"从终端敲命令到出片"的全链路，核心结论：

1. **真正入口是 shell 脚本**：`scripts/run_job.sh` 最后一行
   `conda run -p .conda-envs/digital-human python -m digital_human.cli run --profile cloud --job <yaml>`
2. **shell 脚本负责注入环境变量**：`DIGITAL_HUMAN_PROFILE=cloud`、`HF_HOME`、`HF_HUB_OFFLINE=1` 等（缓存全指向项目内 `.cache/`，禁止运行时联网）。
3. **`pyproject.toml` 注册了 `digital-human` 命令** → `digital_human.cli:main`，包布局是 `src` layout。
4. **`cli.py:main()` 启动顺序**：
   ① `_configure_project_local_storage()`（再次兜底设置 HF/TORCH/TEMP 环境变量）
   ② `build_parser().parse_args()`
   ③ `load_local_config()` 读 `config/local.{profile}.yaml` → `LocalConfig` 数据类
   ④ `load_job_config()` 读任务 yaml → `JobConfig` 数据类（含 `validate_job()` 校验）
   ⑤ `Pipeline(local, job, force).run()`
5. **环境切换机制只有一个**：`process.py:conda_run(conda, prefix, cmd)` 仅返回
   `[conda, "run", "-p", prefix, *cmd]` 命令列表，真正的执行在 `process.py:run_command()`
   里 `subprocess.run()`。编排环境（digital-human）只装 numpy/opencv/yaml，
   阶段 3 经 `conda run -p dots-tts`、阶段 6 经 `conda run -p latentsync` 派生子进程。
6. **Pipeline 六阶段**（`pipeline.py:45-188`，每阶段用 `_should_run()` 断点续传）：
   参考音频 → 视频标准化(25fps) → TTS 分句克隆 → 音频拼接 loudnorm → 时长匹配(裁剪/乒乓) → 口型推理 → 音轨封装。

### 📚 关键事实（新会话必读，避免重新发现）

- **dots.tts 有完整可读源码**，两处位置：
  - pip 包源码（**精读用这个**）：`G:\duikouxing\.conda-envs\dots-tts\Lib\site-packages\dots_tts\`（约 1.7MB 纯 Python）
  - 官方仓库：<https://github.com/studio-dots-ai/dots.tts>（Apache-2.0）
  - 核心文件：`models/dots_tts/core.py`、`models/dots_tts/model.py`、`cli.py`
- **模型权重**：`models/dots.tts-soar/`（SOAR quality，`model.safetensors` +
  `speaker_encoder.safetensors` + `vocoder.safetensors` + `latent_stats.pt`）和 `dots.tts-mf/`（MF fast）。
- pip 简介（重要算法线索）：*"a fully continuous autoregressive TTS system with
  **SOAR alignment** and **CFG-aware MeanFlow distillation**"* —— 精读时围绕这两个词展开。
- LatentSync 侧：官方仓库固定 commit `a229c394...`，本项目有两个补丁
  （`patches/latentsync-1.6-quality-mux.patch` = CRF13 流 copy 不重编码；
  `patches/latentsync-audio-amplitude.patch` = `LATENTSYNC_AUDIO_AMP` 口型幅度旋钮）。
- 架构唯一事实来源：`docs/LatentSync 1.6 云端高画质口型项目架构书.md`（v1.1）。
- 历史实验结论在用户 auto-memory 中（guidance 1.5 + audio_amp 1.3 最接近可灵等）。

## 4. 学习计划总表（按顺序执行）

> 每完成一项，把 ☐ 改成 ☑ 并在"当前进度"追加日期和一句话结论。

| # | 主题 | 材料 | 状态 |
|---|------|------|------|
| 1 | **执行链路全景**（环境从哪来、管道怎么启动） | `run_job.sh` → `cli.py` → `config.py` → `pipeline.py` → `process.py` | ☑ 2026-09-09 |
| 2 | **dots.tts 算法精读**（用户点名要精讲） | site-packages 源码 + 官方 GitHub | ☐ ← **下一步从这里开始** |
| 3 | LatentSync 1.6 算法精读（扩散 + 音频条件 + 时序窗口） | `external/LatentSync/latentsync/` 源码 + 补丁 | ☐ |
| 4 | ffmpeg 关键函数精读（时长匹配/响度标准化/封装） | `src/digital_human/ffmpeg.py` | ☐ |
| 5 | 中文分句算法（`audio.py:split_script`） | `src/digital_human/audio.py` | ☐ |
| 6 | 封装设计讨论（基于前 5 步理解提出方案） | 全仓库 | ☐ |
| 7 | merge 冲突预分析（与另一分支 diff） | git | ☐ |

### 第 2 步详细子计划：dots.tts 精讲路线（下一步执行这个）

按"数据流方向"讲，每小节结合源码文件：

1. **宏观**：自回归 TTS 是什么？和传统 TTS（Tacotron/VITS）差别；
   "fully continuous" 指什么（无离散 codebook）。
2. **入口**：`dots_tts/cli.py` 的 `dots.tts` 命令如何走到推理（对照本项目
   `adapters/dots_tts.py` 传的参数：`--num-steps`、`--guidance-scale`、`--seed`）。
3. **声音克隆机制**：`speaker_encoder.safetensors` 如何把 15s 参考音频变成
   说话人嵌入；为什么 `reference_text` 必须和参考音频逐字一致。
4. **SOAR 对齐**（pip 简介第一个关键词）：语音-文本对齐怎么在自回归框架里做。
5. **MeanFlow 蒸馏**（第二个关键词）：`--num-steps 10/4` 背后的流匹配蒸馏，
   为什么 SOAR 要 10 步、MF 只要 4 步；CFG-aware 是什么。
6. **vocoder**：`vocoder.safetensors` 怎么把 latent 变回波形。
7. **落地回顾**：回到 `adapters/dots_tts.py`，解释本项目每个参数的算法含义。

## 5. 教学约定（用户与 AI 的默契）

- 讲解必须**引用本机实际文件路径和行号**（如 `dots_tts/models/dots_tts/core.py:123`），
  允许用户点开对照。
- 每讲完一个小节，AI 主动问"这块清楚了吗，还是先看代码？"，等用户确认再推进。
- 涉及数学（流匹配、CFG、对齐）时：先给直觉和图示（ASCII/类比），再给公式，
  最后给源码对应位置。
- 用户中途换会话后：新 AI 先读本文件 §3 和 §4，从第一个 ☐ 继续。

## 6. 长期工程目标（学习结束后的正事）

1. **封装**：把 CLI/pipeline 封装成可编程接口（用户在 `pipeline.py` 已留注释：
   TTS 需解耦接口化、lipsync 需 adapter/代理模式，见 `pipeline.py:42-44` 和 `:119` 注释）。
2. **merge**：与另一分支合并。当前已知独立模块 `src/idcard_faceswap/`
   （证件替换，与口型链路正交，冲突风险低），但 `cli.py` 可能两边都注册了子命令。
   merge 前先做 §4 第 7 项的冲突预分析。

## 7. 环境备忘（给新会话的 AI）

- 平台 Windows 11，shell 是 Git Bash；文件操作一律用完整 Windows 绝对路径
  （如 `G:\duikouxing\...`），不要用 `/g/...` 或相对路径。
- dots.tts 源码在 conda 环境内：`G:\duikouxing\.conda-envs\dots-tts\Lib\site-packages\dots_tts\`。
- 读 shell 脚本可用 `Bash` 工具 `cat -n`（Read 工具偶发路径解析失败时用它兜底）。
- 任务产物目录：云端 `jobs-cloud/`、本地 `jobs-office/`。
