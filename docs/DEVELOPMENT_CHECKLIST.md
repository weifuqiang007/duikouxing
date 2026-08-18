# 开发清单

## 已实现

- [x] 独立分支 `codex/liveportrait-performance-drive`。
- [x] 配置增加LivePortrait环境、仓库、驱动输入和后端参数。
- [x] 配置拒绝 `all/pose`，只允许 `exp/lip`。
- [x] `prepare-driving` 生成客户音色导读和逐字录制文本。
- [x] 驱动视频时长阈值检查和小幅全局重定时。
- [x] 官方CLI适配：relative motion、regional control、stitching、paste-back。
- [x] 驱动视频音轨丢弃，最终强制mux客户音色新话术。
- [x] 分离公司/家庭任务目录和GPU配置。
- [x] Python、PyTorch、官方提交和权重来源固定。
- [x] 项目内缓存、环境和权重路径。
- [x] manifest加入后端、驱动文件哈希和参数。
- [x] 配置与适配器单元测试。

## 部署后必须执行

- [ ] 运行 `setup_conda.ps1`、`download_voice_model.ps1`。
- [ ] 运行 `setup_liveportrait.ps1`、`download_liveportrait_models.ps1`。
- [ ] `doctor`全部通过。
- [ ] 用官方示例完成一次source-video + driving-video冒烟测试。
- [ ] 用客户5～10秒素材生成 `exp@0.75`、`exp@0.85`、`exp@1.0`。
- [ ] 同素材生成 `lip@0.85`保守对照。
- [ ] 逐帧检查首帧、人脸检测、牙齿/口腔和下颌边缘。
- [ ] 1倍速和0.25倍速完成主观评审。
- [ ] 使用独立指标测LSE-D/LSE-C（仅辅助，不替代主观验收）。

## 下一阶段建议

- [ ] 增加 `benchmark` 命令记录3060/4070/4090峰值显存和速度。
- [ ] 增加三倍率批量A/B命令和HTML对照页。
- [ ] 增加驱动首帧正脸/闭嘴检测和逐帧人脸可见率报告。
- [ ] 增加音素时间对齐评分，代替单纯总时长判断。
- [ ] 长视频按自然停顿分段并处理重叠过渡。
- [ ] 商用前替换InsightFace检测权重并重新回归。

## 禁止事项

- 不把 `animation_region` 改成 `all`。
- 不启用官方WIP `flag_lip_retargeting`。
- 不把固定椭圆ROI叠加到LivePortrait输出。
- 不在最终输出上默认运行美颜/超分。
- 不允许后续模型猜测权重名、仓库提交或CLI参数；以本仓库脚本和固定提交为准。
