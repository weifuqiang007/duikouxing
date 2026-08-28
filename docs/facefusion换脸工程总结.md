# FaceFusion 换脸工程总结

> 日期：2026-08-27
> 服务器：`ssh -p 37911 root@219.147.100.42`
> 费率：**32 元/小时**

---

## 一、服务器配置

| 项目 | 规格 |
|------|------|
| GPU | NVIDIA GeForce RTX 4090 24GB |
| 显存 | 24564 MiB（换脸实际占用 < 2GiB） |
| CPU | 112 核 |
| 内存 | 502 GB |
| CUDA | 12.8（Driver 535.86.05） |
| 磁盘 | 22GB overlay（已用 ~6GB） |
| 操作系统 | openEuler 22.03 SP1（Docker 容器） |
| ONNX Runtime | 1.24.4（CUDAExecutionProvider + TensorrtExecutionProvider） |
| ffmpeg | 9.0.1 |

### 磁盘占用

| 目录 | 大小 |
|------|------|
| FaceFusion 代码 + 13 个 ONNX 模型权重 | 2.1 GB |
| conda 环境（facefusion） | 2.1 GB |
| **合计** | **~4.2 GB** |

---

## 二、环境建设过程

### 2.1 从老服务器迁移

老服务器（port 34300）上已有 DreamID-V 项目在跑，为避免资源冲突，将 FaceFusion 完整迁移到新服务器（port 37911）。

**迁移清单：**

| 内容 | 来源 | 方式 | 大小 |
|------|------|------|------|
| 模型权重（.assets/models/） | 新机预置（MinIO 挂载） | 已存在 | 2.0 GB |
| 源代码 | 老服务器 | tar 打包 → SFTP 中转 | 3.8 MB |
| 测试数据（samples/） | 老服务器 | 随代码一起 | 2.9 MB |
| conda 环境 | 新建 | conda create + pip install | 2.1 GB |

### 2.2 镜像源配置

服务器在国内，无法直连官方源，必须配置镜像：

**conda 镜像**（`~/.condarc`）：
```yaml
channels:
  - conda-forge
custom_channels:
  conda-forge: https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud
default_channels:
  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge
```

**pip 镜像：**
```
global.index-url = https://mirrors.aliyun.com/pypi/simple
```

### 2.3 环境安装步骤

```bash
# 1. 创建 conda 环境
conda create -n facefusion python=3.12 -y

# 2. 安装 pip 依赖
pip install onnxruntime-gpu==1.24.4    # GPU 推理，含 CUDA Provider
pip install -r requirements.txt          # gradio, opencv, onnx 等

# 3. 安装 ffmpeg（视频编解码必需）
conda install -n facefusion -c conda-forge ffmpeg -y

# 4. 链接 ffmpeg 到系统 PATH
ln -sf /opt/conda/envs/facefusion/bin/ffmpeg /usr/local/bin/ffmpeg
ln -sf /opt/conda/envs/facefusion/bin/ffprobe /usr/local/bin/ffprobe
```

### 2.4 关键 pip 包版本

```
onnxruntime-gpu==1.24.4   # CUDA 推理后端
opencv-python-headless==5.0.0.93
gradio==5.50.0
numpy==2.5.2
onnx==1.22.0
scipy==1.18.0
ffmpy==1.0.0
pydub==0.25.1
Pillow==11.3.0
```

---

## 三、模型权重清单

全部位于 `/root/siton-tmp/facefusion/.assets/models/`，共 13 个 ONNX 模型：

| 模型 | 大小 | 用途 |
|------|------|------|
| `inswapper_128.onnx` | 530 MB | **换脸核心模型** |
| `hyperswap_1a_256.onnx` | 384 MB | 备选换脸模型 |
| `nsfw_3.onnx` | 342 MB | NSFW 检测 |
| `wav2lip_gan_96.onnx` | 138 MB | 唇形同步 |
| `arcface_w600k_r50.onnx` | 166 MB | 人脸特征提取（参考脸匹配） |
| `bisenet_resnet_34.onnx` | 89 MB | **人脸区域解析**（region 遮罩用） |
| `nsfw_1.onnx` | 77 MB | NSFW 检测 |
| `fairface.onnx` | 81 MB | 年龄/性别/种族分类 |
| `2dfan4.onnx` | 93 MB | **人脸关键点**（68 点） |
| `kim_vocal_2.onnx` | 64 MB | 人声分离 |
| `xseg_1.onnx` | 67 MB | **人脸精确分割**（occlusion 遮罩用） |
| `yoloface_8n.onnx` | 12 MB | **人脸检测**（YOLO） |
| `fan_68_5.onnx` | 0.9 MB | 人脸关键点备用 |

> **未安装**：`gfpgan_1.4.onnx`（324MB，face_enhancer 画质增强模型），GitHub 下载极慢，需挂代理手动下载后上传。

---

## 四、调参过程：遮罩方案对比

### 4.1 问题

默认 `box` 矩形遮罩导致：
- 换上去的脸比原来小，边缘没贴合
- 耳朵区域切不干净，原脸残留
- 脸与背景交界处有接缝感

### 4.2 三种遮罩方案

#### 方案 v1：box（默认）

```bash
python facefusion.py headless-run \
  --source-paths 11.png \
  --target-path wlh.mp4 \
  --output-path swap_v1.mp4 \
  --processors face_swapper
# 默认 face-mask-types=box, padding=0, blur=0
```

| 指标 | 数值 |
|------|------|
| 处理速度 | **~65 fps** |
| 总耗时 | **12.6 秒** |
| 效果 | ❌ 边缘生硬，有明显接缝 |

#### 方案 v2：region（推荐 ✅）

```bash
python facefusion.py headless-run \
  --source-paths 11.png \
  --target-path wlh.mp4 \
  --output-path swap_v2.mp4 \
  --processors face_swapper \
  --face-mask-types region \
  --face-mask-areas upper-face lower-face mouth \
  --face-mask-blur 0.5 \
  --face-mask-padding 20 20 20 20
```

| 指标 | 数值 |
|------|------|
| 处理速度 | **~48 fps** |
| 总耗时 | **18.3 秒** |
| 依赖模型 | `bisenet_resnet_34.onnx`（人脸区域解析） |
| 效果 | ✅ 遮罩贴合脸型轮廓，padding 覆盖过渡区，blur 柔化边缘 |

**关键参数说明：**
- `--face-mask-types region`：使用 bisenet 解析出的五官区域作为遮罩，不再是简单矩形
- `--face-mask-areas upper-face lower-face mouth`：覆盖额头+下半脸+嘴巴 = 完整人脸
- `--face-mask-padding 20 20 20 20`：遮罩四周各扩展 20px，确保边缘完全覆盖
- `--face-mask-blur 0.5`：遮罩边缘高斯模糊半径，0.5 实现自然过渡

#### 方案 v3：occlusion（最精细）

```bash
python facefusion.py headless-run \
  --source-paths 11.png \
  --target-path wlh.mp4 \
  --output-path swap_v3.mp4 \
  --processors face_swapper \
  --face-mask-types occlusion \
  --face-mask-blur 0.4 \
  --face-mask-padding 10 10 10 10
```

| 指标 | 数值 |
|------|------|
| 处理速度 | **~6 fps** |
| 总耗时 | **71 秒** |
| 依赖模型 | `xseg_1.onnx`（像素级人脸分割） |
| 效果 | ✅ 像素级精确分割，边缘最干净 |

### 4.3 方案对比总结

| 维度 | v1 box | v2 region ⭐ | v3 occlusion |
|------|--------|-------------|---------------|
| 耗时 | 12.6s | **18.3s** | 71s |
| 速度 | 65 fps | **48 fps** | 6 fps |
| 边缘质量 | 差 | **好** | 最好 |
| 耳朵处理 | 残留 | **自然过渡** | 精确切割 |
| 适合场景 | 不推荐 | **量产首选** | 精修/特写镜头 |

---

## 五、成本分析

### 5.1 基准数据

- **服务器费率**：2 元/小时 = 0.033 元/分钟
- **测试素材**：720×1280 竖屏视频，30fps，409 帧，时长 13.6 秒

### 5.2 推荐方案（v2 region）单条成本

```
处理耗时：18.3 秒 = 0.305 分钟
单条成本：0.305 × 0.033 = 0.010 元

每小时产能：3600 ÷ 18.3 = 197 条/小时
每小时成本：2 元
```

### 5.3 三方案成本对比

| 方案 | 单条耗时 | 单条成本 | 每小时产能 | 每小时费用 |
|------|----------|----------|------------|------------|
| v1 box | 12.6s | **0.007 元** | 285 条 | 2 元 |
| v2 region ⭐ | 18.3s | **0.010 元** | **197 条** | **2 元** |
| v3 occlusion | 71s | 0.039 元 | 51 条 | 2 元 |

> **结论**：使用推荐的 region 遮罩方案，**每条 13 秒视频换脸成本约 1 分钱**。

### 5.4 成本影响因素

- **视频分辨率**：720p 以下速度更快，1080p 耗时约翻倍
- **视频时长**：线性关系（帧数 × 单帧耗时）
- **人脸数量**：多人大幅增加耗时（每张脸都要检测+替换）
- **人脸大小**：人脸占画面比例越大，处理像素越多
- **是否加 face_enhancer**：加 gfpgan 画质增强约增加 30-50% 耗时

---

## 六、快速使用指南

### 6.1 SSH 连接

```bash
ssh -p 37911 root@219.147.100.42
# 密码：见内部记录
```

### 6.2 激活环境

```bash
source /opt/conda/bin/activate facefusion
cd /root/siton-tmp/facefusion
```

### 6.3 推荐命令（region 遮罩）

```bash
python facefusion.py headless-run \
  --source-paths <源脸图片.png> \
  --target-path <目标视频.mp4> \
  --output-path <输出路径.mp4> \
  --processors face_swapper \
  --face-mask-types region \
  --face-mask-areas upper-face lower-face mouth \
  --face-mask-blur 0.5 \
  --face-mask-padding 20 20 20 20
```

### 6.4 图片换脸

```bash
python facefusion.py headless-run \
  --source-paths <源脸.png> \
  --target-path <目标图片.png> \
  --output-path <输出.png> \
  --processors face_swapper \
  --face-mask-types region \
  --face-mask-areas upper-face lower-face mouth \
  --face-mask-blur 0.5 \
  --face-mask-padding 20 20 20 20
```

### 6.5 项目结构

```
/root/siton-tmp/facefusion/
├── facefusion.py          # 入口脚本
├── facefusion/             # Python 包
├── .assets/models/         # 13 个 ONNX 模型（2.0GB）
├── .jobs/                  # 任务记录
├── samples/                # 测试数据
│   ├── 11.png              # 源脸照片
│   ├── wlh.mp4             # 目标视频
│   └── swap_test_v2.mp4    # 生成结果
├── requirements.txt
└── facefusion.ini          # 默认配置（空=用代码默认值）
```

---

## 七、待办

- [ ] **下载 gfpgan_1.4.onnx**（324MB）：
      地址 `https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/gfpgan_1.4.onnx`
      下载后放到 `/root/siton-tmp/facefusion/.assets/models/`
      可额外加 `--processors face_swapper face_enhancer` 提升画质
- [ ] **长视频/高分辨率测试**：当前仅测试 720p 13s 素材，需验证 1080p 长视频稳定性
- [ ] **多人场景测试**：验证 `--face-selector-order` 对多人的筛选效果


可以，但**不能靠当前这套 FaceFusion `face_swapper` 直接做到**。

你现在文档里的方案是 `region` 遮罩，覆盖的是 `upper-face lower-face mouth`，也就是额头、下半脸、嘴巴这类“脸部区域”，见 [facefusion换脸工程总结.md](G:/duikouxing/docs/facefusion换脸工程总结.md:161)。另一份实现设计里 `mask_regions` 也只列了 skin、眉毛、眼睛、鼻子、嘴唇等，没有 hair，见 [FACE_SWAP_HIGH_FIDELITY_IMPLEMENTATION.md](G:/duikouxing/docs/FACE_SWAP_HIGH_FIDELITY_IMPLEMENTATION.md:410)。

所以结论是：

**FaceFusion 默认是“换脸”，不是“换头/换发型”。**  
它会尽量保留目标视频原本的头发、头型、身体和背景。把 padding 调大最多只能改善发际线、额头边缘、耳朵附近的融合，不能真正把源用户的头发带过去。

如果你想要“源用户的头发也过去”，有三条路线：

1. **最稳：拍摄/选择发型接近的目标视频**  
   让目标演员或素材本身的发型接近源用户，然后 FaceFusion 只换脸。这是商业交付里最稳定、成本最低的路线。

2. **中等：FaceFusion 后再做头发 inpainting / ComfyUI 修发型**  
   先用 FaceFusion 得到脸，再用发型区域 mask 做生成式重绘。适合图片、短视频、正脸镜头；视频会有闪烁风险，需要逐帧/关键帧一致性处理。

3. **重型：改成“整头替换/生成式 V2V”链路**  
   比如用 DreamID-V、Wan/ComfyUI、InstantID/IP-Adapter 类方案，让模型按参考人生成头发+脸。它更可能带上发型，但代价是画面可控性下降，可能影响衣服、背景、身份稳定性。

我的建议：当前 FaceFusion 方案继续作为量产基线；下一步先做一个 **10 秒 A/B 测试**：  
A 组只微调发际线融合，B 组 FaceFusion 后加头发 inpainting。看你是否接受发型一致性和视频闪烁，再决定要不要上整头生成链路。


明白，你的目标是：**尽量保留待替换 PNG 里这个人的头发、头型，让结果更像这个人**。

用 FaceFusion 这条链路，答案是：**只能有限做到，不能真正完整带发型**。

FaceFusion 的 `region mask` 可选区域里没有 `hair`，官方/当前参数只支持 `skin、眉毛、眼睛、鼻子、嘴、嘴唇、眼镜` 这些脸部区域，所以它本质上不会把 PNG 的头发语义迁移到视频里。你现在看到“脸像了、头发没变”，正是这个模型边界。

当前最现实的调法是把脸部替换范围尽量推到发际线附近：

```bash
python facefusion.py headless-run \
  --source-paths <待替换照片.png> \
  --target-path <目标视频.mp4> \
  --output-path <输出.mp4> \
  --processors face_swapper \
  --face-mask-types box region \
  --face-mask-regions skin left-eyebrow right-eyebrow left-eye right-eye nose mouth upper-lip lower-lip \
  --face-mask-padding 35 20 20 20 \
  --face-mask-blur 0.45
```

这里的核心是：

`--face-mask-padding 35 20 20 20`  
把上方 padding 加大，尽量覆盖额头、发际线边缘，让“头型感觉”更接近源图一点。

但注意，**这不是换头发**。调太大可能会把目标原视频的头发边缘、额头上方背景、刘海区域弄脏，出现糊边或脸皮扩散。建议做三组小样：

```text
A: padding 20 20 20 20, blur 0.5
B: padding 35 20 20 20, blur 0.45
C: padding 50 25 25 25, blur 0.55
```

我的判断：  
如果只是想“更像被替换的人”，可以先试 B。  
如果必须明显保留 PNG 的发型、头型，那就要走 FaceFusion 后处理：对头发区域做单独 mask，再用 ComfyUI / inpainting / V2V 修发型。FaceFusion 单独做不到稳定换发。
---

# 八、本地复现实验（2026-08-28，家里电脑）

> 机器：Windows 11 · RTX 4070 Ti 12GB · E 盘 1.2TB 可用
> 分支：`codex/dreamidv-faceswap`（已合并 `codex/faceswap-facefusion-local` 的本地化基础设施）
> 素材：同服务器（`samples/11.png` + `samples/wlh.mp4`，720×1280 / 30fps / 409 帧 / 13.6s）

## 8.1 本地环境与服务器差异

| 项 | 服务器（4090） | 本地（4070 Ti） |
|----|--------------|----------------|
| 推理后端 | onnxruntime-gpu (CUDA) | **onnxruntime-directml (DirectML)** |
| 相对速度 | 48fps（v2 配置） | 15.5fps（v2 配置），约 1/3 |
| FaceFusion | 3.8.2（commit `4b1dedb`） | 同 |

**为什么用 DirectML**：onnxruntime-gpu 在 Windows 缺 `cudnn64_9.dll`（Error 126），手工把 NVIDIA pip 包 DLL 布置到 onnxruntime 目录的几种做法均被本机安全沙箱拦截，遂改用官方 installer 的 directml 路线（`python install.py directml`），零依赖折腾。日后要补 CUDA：把 `.conda-envs\facefusion\Lib\site-packages\nvidia\*\bin\*.dll` 复制进 `...\onnxruntime\capi\` 后重跑 `install.py cuda@12`。

## 8.2 工程内目录布局（全部在 E:\duikouxing 内）

```
external\facefusion\        # 3.8.2 源码（.assets\models 经 junction 指向 ↓）
models\facefusion\          # 全部 ONNX 权重（junction 双向验证通过）
.conda-envs\facefusion\     # Python 3.12 环境
jobs-home\fs-*\{input,output,logs,work}\   # 每任务归档
samples\                    # 素材（含新增三视图）
```

## 8.3 本地新踩的坑（服务器没有的）

1. **GitHub releases 直连被重置** → 模型一律走 `https://hf-mirror.com/facefusion/<collection>/resolve/main/<file>`（FaceFusion 内置回退源，实测 ~3MB/s）
2. **pip 混入系统 Python 用户目录**：本机 `%APPDATA%\Python\Python312\site-packages` 里有大量包，必须 `PYTHONNOUSERSITE=1`，否则环境不自包含
3. **`.hash` 校验是 CRC32**（`zlib.crc32` 取 8 位十六进制），不是 SHA256——用 `sha256sum` 对不上是正常的，别误判文件损坏（本次差点误删完好的 gfpgan）
4. **默认 occluder 是 xseg_1**：即使只用 region 遮罩也会预检下载，服务器清单里没有它，需补（xseg_2 不顶替）
5. **NSFW 内容检查默认启用**：首次运行会下 nsfw_1/2/3 三个模型（共 ~460MB），走 GitHub 会超时，提前从镜像下好

## 8.4 五次跑版记录

| # | 任务目录 | 换脸模型@boost | 遮罩 | 特殊 | 速度(fps) | 边缘分界线 |
|---|---------|---------------|------|------|-----------|-----------|
| 1 | fs-local-0001 | ghost_2_256@512 | box+occlusion+region，blur 0.30 | 表情恢复80 | 4.71 | **明显** ❌ |
| 2 | fs-v2-0001 | inswapper_128@128 | region，blur 0.5，padding 20 | 复现服务器v2 | 15.47 | 无抱怨 ✅ |
| 3 | fs-fix-0001 | ghost_2_256@512 | occlusion+region，blur 0.50 | +gfpgan_1.4 | 4.11 | 仍明显 ❌ |
| 4 | fs-256-0001 | ghost_2_256@256 | occlusion+region，blur 0.60 | 正+侧脸多源 | 6.00 | 轻微 |
| 5 | fs-128-0001 | inswapper_128@128 | occlusion+region，blur 0.60 | 正+侧脸多源 | 5.90 | **初步判优** ✅ |

输出统一在 `jobs-home\<任务>\output\`。

## 8.5 边缘分界线归因（本次核心结论）

**主因：锐度落差，不是遮罩。** 证据链：

- #2（128 渲染）没被抱怨过硬边；#1/#3（512 渲染）都有明显分界线——唯一稳定差异是换入脸的清晰度
- 512/256 渲染的脸比压缩视频的皮肤清晰一大截，人眼把这种"清晰度台阶"看成一条分割线
- 遮罩（box/region/occlusion）只决定这条线画在哪，决定不了"看不出线"；blur 只能弱化
- **gfpgan 是提锐度的，对此问题无效甚至加重**（#3 实测），不要再往这个方向调

**推论**：换脸管线的合成边界无法根除，只能靠"软度匹配"（低 boost）弱化，代价是身份相似度下降。**彻底解决 = 整头生成**（LivePortrait 再演 + 回贴，即换头业务线方案A），没有贴脸边界。

**#5 复现命令**（当前最优）：

```bash
cd external/facefusion
PYTHONNOUSERSITE=1 ../../.conda-envs/facefusion/python.exe facefusion.py headless-run \
  --source-paths <正脸.png> <侧脸.png> \
  --target-path <目标.mp4> \
  --output-path <输出.mp4> \
  --processors face_swapper expression_restorer \
  --face-selector-mode one \
  --face-swapper-model inswapper_128 --face-swapper-weight 0.85 \
  --expression-restorer-model live_portrait --expression-restorer-factor 80 \
  --face-mask-types occlusion region \
  --face-mask-blur 0.6 \
  --execution-providers directml \
  --output-video-encoder libx264 --output-video-quality 95 --output-video-preset slow
```

## 8.6 三视图素材的使用结论

| 文件 | 结论 |
|------|------|
| `samples/zehnglian.png` 正脸 | ✅ 多源换脸源之一（正对镜头帧自动选用） |
| `samples/celian.png` 侧脸 | ✅ 多源换脸源之一（转头帧自动选用，身份更稳） |
| `samples/hounaoshao.png` 后脑勺 | ❌ 换脸用不上（无脸可检测）；留作 LivePortrait 换头线的头型/发型参考 |

注意：AI 生成的三视图形象与真人照片（11.png）不要混在同一次任务里当源脸。

## 8.7 待验证 / 待办

- [ ] **#5 vs #4 的优劣需扩样本确认**（当前只有 1 条视频，光照/分辨率/角度单一）
- [ ] gfpgan_1.4.onnx 已就位（用户手动下载，CRC32 校验通过）——画质增强用途保留，但对边缘问题无效
- [ ] 多人场景、1080p、长视频稳定性（沿用服务器待办）
- [ ] 换脸管线天花板已探明，**下一步评估 LivePortrait 再演+回贴（方案A）**，从根上消灭贴脸边界

## 8.8 扩样本实测：person1（1080×1920 竖屏，26.6s，798 帧）

**新坑 6：iPhone HDR 视频直接产出全黑输出。** 输入为 HEVC 10-bit / BT.2020 / HLG（arib-std-b67），FaceFusion 抽帧管线只认 8-bit SDR：黑帧 → 检不到脸 → 56fps 假速度 → 输出 3.9MB 全黑视频（正常应为 43MB）。旋转元数据（rotation=-90）是伴生问题但非根因（烘焙后仍黑）。

**修复：喂给 FaceFusion 前先做 HDR→SDR 色调映射**：

```bash
ffmpeg -i 输入.mp4 -vf "zscale=t=linear:npl=100,format=gbrp,tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p" \
  -c:v libx264 -crf 16 -preset medium -c:a copy 预处理.mp4
```

**验证方法**（客观判定换脸是否真的发生）：ffmpeg 同时刻抽帧对比输入输出——黑屏版整帧差 181（全黑 vs 实际内容）；成功版整帧差 2.6、人脸区 6.2+、背景 2.5（编码噪声），变化精确集中在脸区。

| 任务 | 输入 | 输出 | 速度 | 结果 |
|------|------|------|------|------|
| fs-p1-128-0001（第一次） | 原始 HDR | 3.9MB 全黑 | 56fps(假) | ❌ |
| fs-p1-128-0001（SDR 版） | 色调映射后 | 43.3MB | 6.45fps | ✅ 差异热图确认脸区换脸 |

素材：`zhenglian.png`（正脸）+ `celian.png`（侧脸）多源，配置同 #5（inswapper_128@128 + occlusion/region + blur 0.6）。

## 8.9 修正：8.8 的 HDR 转换配方会产生"彩铅画"（已迭代）

8.8 里的 `tonemap=hable:desat=0` 配方虽然能出画，但**偏暗、发灰、蜡笔感**（实测整帧对比度仅 22、饱和度仅 15）——hable 的高光压缩对室内人物场景过重。经同帧多配方对比（hable/mobius/reinhard/clip/colorspace × npl 参数扫描 + 图像模型目检），**最终配方**：

```bash
ffmpeg -i 输入.mp4 -vf "zscale=t=linear:npl=500,format=gbrp,tonemap=clip,zscale=t=bt709:m=bt709:r=tv,format=yuv420p,eq=gamma=1.06:saturation=1.05" \
  -c:v libx264 -crf 16 -preset medium -c:a copy 预处理.mp4
```

**要点**：① HLG 输入的 `npl` 用 400~500（不是网上常见教程的 100，那会把画面压暗压灰）；② 室内场景用 `tonemap=clip`（只裁高光、不动色彩），比 hable 的电影级压缩自然得多；③ 转完用亮度/对比度/饱和度三项指标验收（正常参考：~175/54/37，坏版本是 148/22/15）。成品 `jobs-home/fs-p1-128-0001/output/swap_p1_final.mp4`。

**新坑 7：FaceFusion 启动时可能僵死在 GitHub 连通性探测。** 症状：日志停在 `processing step 1 of 1`、python 内存仅 ~68MB（模型未加载）、GPU 0%、无 ffmpeg 进程。根因是 `ping_static_url` 起的 curl 僵死。**修复：加 `--download-providers huggingface`**（hf-mirror 在其 URL 列表内），模型已全量本地化后此参数还能加速启动。建议所有本地跑批命令固定带上。

## 8.10 故障速查表：症状 → 原因 → 解决（本次会话经验汇总）

> 遇到问题先查这张表，再跳转到对应小节看细节。按"症状"列搜索即可。

| # | 症状（你看到的） | 根因 | 解决 |
|---|----------------|------|------|
| 1 | 输出视频**全黑**、只有 ~4MB、处理速度异常快（50+fps 假速度） | 输入是 iPhone HDR 视频（HEVC 10-bit / BT.2020 / HLG），FaceFusion 只认 8-bit SDR：抽帧全黑→检不到脸→空转 | 先做 HDR→SDR 预处理（配方见 #2），再喂 FaceFusion。判断：`ffprobe -show_entries stream=codec_name,pix_fmt,color_transfer`，出现 `yuv420p10le` + `arib-std-b67` 即中招 |
| 2 | HDR 转码后画面**偏暗、发灰、蜡笔/彩铅感** | 通用教程配方 `hable + npl=100` 对室内人物场景压缩过猛（实测对比度 22、饱和度 15） | 用 8.9 节配方（clip + npl=500 + eq 微调）。**HLG 的 npl 用 400~500，不是 100**；室内场景用 clip（只裁高光不动色彩），别用 hable |
| 3 | FaceFusion **启动后僵死**：日志停在 `processing step 1 of 1`、GPU 0%、python 内存仅 ~68MB（模型没加载）、无 ffmpeg 进程 | 探测 GitHub 下载源的 curl 子进程僵死 | 命令加 **`--download-providers huggingface`**（模型已本地化后必加，还能加速启动）；已僵死的要结束进程后重跑 |
| 4 | 日志报 `validating hash for xxx failed` 后中止 | 对应模型文件缺失（FaceFusion 会预检一些没显式用到的模型：默认 occluder xseg_1、NSFW 三件套等） | 从 hf-mirror 预下载缺失模型到 `models\facefusion\`。规律：**首次跑新配置，先备齐它的模型清单** |
| 5 | 用 `sha256sum` 校验 .hash 永远对不上，误判文件损坏 | FaceFusion 的 .hash 是 **CRC32 前 8 位十六进制**（zlib.crc32），不是 SHA256 | 按 CRC32 算法校验（一行 python：读文件字节算 crc32，格式化 08x，与 .hash 内容比对） |
| 6 | 环境"好了但很脆"，系统 Python 一动换脸环境就坏 | pip 把系统 Python 用户目录里的包当依赖混进来了（完整路径见 8.3 坑2） | 所有 pip/python 调用带 **`PYTHONNOUSERSITE=1`** |
| 7 | GitHub 下载连不上/被重置/龟速 | 国内直连 GitHub releases 不稳定 | 下载一律走 `https://hf-mirror.com/facefusion/<collection>/resolve/main/<file>`（~3MB/s） |
| 8 | Windows 上报 `Error 126: cudnn64_9.dll missing` | onnxruntime-gpu 需要 cuDNN 9，Windows 不自带；Python 3.8+ 也不搜 PATH 里的 DLL | 简单路线：改装 onnxruntime-directml + `--execution-providers directml`（本机现状）。要 CUDA：按 8.1 的说明复制 NVIDIA DLL 后重装 onnxruntime-gpu |
| 9 | 换的脸**边缘有明显分界线** | 主因是**锐度落差**（512/256 渲染的脸比压缩视频清晰太多），遮罩只是次要因素；gfpgan 提锐度会加重 | pixel-boost 降到 128/256 + blur 0.5~0.6 + 去掉 box 遮罩。根治只能整头生成（LivePortrait 方案A），详见 8.5 |
| 10 | 输入视频带 `rotation=-90` 元数据，输出方向乱 | FaceFusion 对旋转元数据处理不可靠 | 转码预处理时 ffmpeg 自动烘焙旋转（8.9 配方顺带解决），不必单独处理 |
| 11 | 怀疑"根本没换脸" | 需要客观验证，肉眼逐帧看不现实 | ffmpeg 同时刻抽帧对比输入输出：整帧差 >100 = 全黑/坏了；背景 ~2 + 人脸区 5~7 = 正常换脸（差异热图法，见 8.8） |
| 12 | 全片亮度/对比度逐帧恒定，怀疑画面卡死 | 也可能就是**静态场景**（如举证件 26 秒不动），属正常 | 先看内容再下结论，配合 #11 的抽帧对比确认 |

**色彩验收三项指标**（转码/成品通用，OpenCV YCrCb/HSV 统计）：正常室内人物视频参考值 = 亮度 150~180 / 对比度 45~60 / 饱和度 30~45；"灰雾/彩铅画"特征 = 对比度 <25 且饱和度 <20。

**本地跑批标准命令骨架**（在 8.5 的 #5 命令上加两处硬化）：

```bash
# 1) HDR/旋转输入先预处理（普通 SDR 输入可跳过）
ffmpeg -i 输入.mp4 -vf "zscale=t=linear:npl=500,format=gbrp,tonemap=clip,zscale=t=bt709:m=bt709:r=tv,format=yuv420p,eq=gamma=1.06:saturation=1.05" -c:v libx264 -crf 16 -preset medium -c:a copy 预处理.mp4
# 2) 换脸命令务必带：
#    PYTHONNOUSERSITE=1          （坑6）
#    --download-providers huggingface  （坑3）
```
