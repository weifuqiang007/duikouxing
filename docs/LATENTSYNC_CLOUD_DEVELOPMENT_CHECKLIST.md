# LatentSync 1.6 云端开发清单

## A. 分支与文档

- [x] 创建 `codex/latentsync-1.6-cloud` 分支。
- [x] 建立 LatentSync 云端架构书。
- [x] 建立独立安装手册。
- [x] 建立可量化验收标准。
- [x] 固定官方仓库提交。
- [x] 列出全部 Python 依赖和权重位置。

## B. 云端环境

- [x] 增加 `config/local.cloud.yaml`。
- [x] 增加 Python 3.10.13 LatentSync 环境定义。
- [x] 增加 Ubuntu/4090 安装脚本。
- [x] 增加 LatentSync 1.6 完整权重下载脚本。
- [x] 增加离线缓存环境变量。
- [ ] 在实际 RTX 4090 云实例执行安装脚本。
- [ ] 保存 `nvidia-smi`、Conda、FFmpeg 和 pip freeze 快照。
- [ ] 生成 LatentSync 权重 SHA-256 清单。

## C. 核心代码

- [x] 增加 `LatentSyncAdapter`。
- [x] 固定 `stage2_512.yaml`。
- [x] 校验 20～50 steps 和 1.0～3.0 guidance。
- [x] 流水线按 `lipsync.engine` 分发。
- [x] LatentSync 链路跳过 MuseTalk 纹理合成。
- [x] 增加官方二次编码补丁。
- [x] 最终封装支持 `-c:v copy`。
- [x] manifest 记录口型引擎和官方提交。
- [ ] 新增 lipsync-only 入口，可直接读取本地生成的 base MP4 + target WAV。
- [ ] 对超过 60 秒的视频增加分段和重叠融合。
- [ ] 增加每帧人脸检测预检，推理前拒绝无脸/多脸。
- [ ] 增加本地预对齐 512 人脸与逆仿射还原，避免云端处理整帧。

## D. 质量评估

- [ ] 准备同一 3～5 秒 MuseTalk/LatentSync A/B 素材。
- [ ] 运行 20/30/40 steps A/B。
- [ ] 运行 guidance 1.2/1.3 A/B。
- [ ] 实现嘴部皮肤环 Laplacian 清晰度报告。
- [ ] 实现人脸区外保护矩形 SSIM 报告。
- [ ] 集成官方 SyncNet 评分，仅作工程辅助。
- [ ] 执行 5 人盲测。
- [ ] 记录 RTX 4090 峰值显存、耗时和输出码率。

## E. 上线前门禁

- [ ] `pytest` 全通过。
- [ ] `ruff check src tests` 全通过。
- [ ] `doctor --profile cloud` 全通过。
- [ ] 30 秒 720p 任务无 OOM。
- [ ] 三组素材至少两组通过完整验收。
- [ ] 与 MuseTalk 基线盲测中至少 4/5 人选择 LatentSync。
- [ ] 证件、广告牌、手和背景无生成式改变。
