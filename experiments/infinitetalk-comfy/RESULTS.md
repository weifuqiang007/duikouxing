# InfiniteTalk ComfyUI 蒸馏版实验结果（2026-08-24）

服务器：ssh -p 34300 root@219.147.100.42（密钥登录），部署于 `/root/siton-tmp/aigc/`
部署手册：`docs/4090_COMFYUI_INFINITETALK_DEPLOY.md`

## 结论速览

| 版本 | 耗时(6s片) | 画质判定 |
|---|---|---|
| 官方仓库 V2V 40步（8-20 已弃） | 5.4h / 14s | 无可见提升 |
| **ComfyUI 4步 832×480 横屏** | 236s（含模型加载） | 口型真实开合+露齿；皮肤过度平滑；构图裁头顶（横屏中心裁竖屏素材） |
| **ComfyUI 6步 竖屏 480×832** | ~4.5min | 构图完整（头顶/下巴/证件/半身全在），口型自然，证件文字可辨 |
| **ComfyUI 8步 832×480** | ~5min | 皮肤纹理显著改善（毛孔/胡茬可辨），证件边缘文字锐利，"接近真实视频的数字复刻" |

**每 6s 片约 4-5 分钟（约 45-50 倍实时），24G 4090 稳定跑通。**

## 关键修正（相对部署手册/原工作流）

1. **素材方向**：对客素材是 1080×1920 竖屏。横屏 832×480 center-crop 会裁掉头顶+证件，
   必须用 **480×832 竖屏**（Wan 480P 原生支持）。改节点 15 (ImageResizeKJv2) 和 19
   (WanVideoImageToVideoMultiTalk) 的 width/height。
2. **Kijai multitalk 层**：`Wan2_1-InfiniTetalk-Single_fp16.safetensors` 是 4.8G 适配层
   （5125258232 字节），不是全量 DiT，放 `models/multitalk/`。
3. torch 必须 ≥2.6（comfy_kitchen schema），实测 **2.7.1+cu126 + sageattn 1.0.6** 可用。
4. `HF_HUB_DISABLE_XET=1` 必须设（hf-mirror 下 Xet 401）。
5. wav2vec 路径：`models/transformers/TencentGameMate/chinese-wav2vec2-base`（不是 models/wav2vec）。

## 工作流文件

- `workflow_api_wlh004_6s.json` — 4/6/8 步基准（横屏 832×480）
- `workflow_api_wlh004_6s_portrait.json` — **生产候选**：竖屏 480×832 + 6 步
- 提交方式：`venv-comfy/bin/python submit_and_wait.py <workflow.json>`（服务器 aigc 目录）
- 帧数公式：`num_frames = round(duration_s × 32) + 1`（6s→193），`frame_window_size 81` 滑窗

## 输出与对比（本地 jobs-cloud/wlh-004-it-comfy/）

- `it_wlh004_6s_00001-audio.mp4`（4步）/ `it_wlh004_6s_s6/s8`（6/8步）/ `it_wlh004_6s_portrait_s6`
- `compare_it4/it6/it8/portrait_vs_c6.mp4` — 左 LatentSync c6 基线，右 InfiniteTalk

## 与 LatentSync 的定位差异

- InfiniteTalk：整帧扩散重生成 → 口型有真实开合/牙齿/口腔内部，无贴图感；
  代价是全画面 AI 质感（8 步已接近真实）+ 480P 分辨率上限（对客竖屏 480×832 需评估是否够用）
- LatentSync：原视频保真（皮肤/证件 100% 原始），仅嘴部区域形变；c6(g1.5+amp1.3) 无贴图感
- 证件/身份保真：8 步版证件文字锐利（视觉模型确认），但仍是重生成，审核级保真不如 LatentSync

## 长音频（>视频时长）处理

up主的"倒放拼接"技巧在工程上不需要手工做：WanVideoEncode 只用首帧+建 latent 容器，
视频时长 ≥ 音频即可。沿用仓库已有 `base_pingpong_cycle.mp4` 预处理。

## 待用户看片裁决

四个 compare_*.mp4。裁决点：8 步 AI 质感 vs LatentSync 原生质感的取舍、480×832 分辨率是否够交付。
