# FaceFusion 换脸工程总结

> 日期：2026-08-27
> 服务器：`ssh -p 37911 root@219.147.100.42`
> 费率：**2 元/小时**

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
