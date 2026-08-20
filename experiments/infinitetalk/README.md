# InfiniteTalk V2V 配音实验（experiments/infinitetalk）

**目的**：验证 MeiGen-AI/InfiniteTalk（Wan2.1-I2V-14B 底座，sparse-frame video dubbing）
在"客户原视频 + 克隆音频"场景下，口型自然度能否超过现有 LatentSync 1.6 交付链路
（对照可灵基准）。**与交付链路无关**：本目录是独立实验，不进 pipeline。

## 为什么独立目录

InfiniteTalk 是全身重生成模型（背景/手持物会被重绘），与"背景像素级不变"的交付
红线冲突；代码、权重、脚本全部隔离在 `external/InfiniteTalk` + 本目录，实验结束后
可直接整目录删除，不污染主链路。

## 布局

| 文件 | 作用 |
| --- | --- |
| `setup_cloud_infinitetalk.sh` | 云服务器建 venv（容器无 conda/pip，用 ensurepip）+ 装依赖 |
| `download_infinitetalk_models.sh` | 经 hf-mirror.com 选择性下载权重（fp8 quant 路径，约 19GB） |
| `run_v2v_14s.sh` | 准备 wlh-004 的 14s 视频+音频，V2V 生成，产物定位 |
| `results/` | 生成结果回传目录（gitignored） |

## 关键决策记录

- **commit 锚定**：`external/InfiniteTalk` @ `fd63149`（2025-12-18，main）。
- **fp8 quant 而非 bf16**：`--quant fp8` 走 `multitalk.py:194`，跳过 7 个 base DiT
  分片（27GB）与 bf16 T5（11GB），24G 显存的 4090 用
  `--num_persistent_param_in_dit 0` 层流式驻留。若追求极限画质，再下全量 bf16。
- **网络约束**：服务器不通 huggingface.co/github，通 hf-mirror.com/pypi/modelscope；
  代码经 gitclone.com 镜像克隆到本地再上传。
- **对比口径**：与可灵/c4/c5/c6 相同的 14s 素材 + 相同嘴部放大条参数
  （见 `tmp_compare/`），保证公平。

## 运行（云服务器上）

```bash
bash experiments/infinitetalk/setup_cloud_infinitetalk.sh
bash experiments/infinitetalk/download_infinitetalk_models.sh
bash experiments/infinitetalk/run_v2v_14s.sh          # 前台观察，或 nohup 后台
```

## 结果（实验后填写）

- 口型幅度 vs 可灵：待测
- 身份/背景/手持物保真：待测（预期：证件文字重绘，为交付红线所不容）
- 14s 480P 耗时：待测
