# LatentSync 1.6 / RTX 4090 云端安装与运维手册

> 本文档合并了原「云端服务器安装记录（审计文档）」和「RTX4090 云端安装手册」，
> 是云端环境搭建的唯一参考。执行日期：2026-08-18，更新：2026-08-31。

## 1. 实例规格

```text
GPU:   NVIDIA RTX 4090 24GB（单卡）
RAM:   最低 64GB（当前机器 502GB）
CPU:   8～16 vCPU（当前 112 逻辑核 Xeon Gold 6330）
磁盘:  最低 100GB 可用，建议 150GB
OS:    Ubuntu 22.04 LTS
驱动:  NVIDIA 535.86.05（支持 CUDA 12.2 运行时，满足 cu121 wheel 要求）
FFmpeg: 必须含 libx264/AAC
```

更换 A100/H800 不会提高同一 LatentSync 权重的单帧画质，不应在 Demo 阶段浪费预算。

### 1.1 当前服务器信息

| 项目 | 值 |
|---|---|
| SSH 入口 | `ssh -p 34300 root@219.147.100.42`（siton 平台容器实例） |
| 容器系统 | Ubuntu 22.04.3 LTS（宿主机内核 openEuler 5.10） |
| GPU | NVIDIA GeForce RTX 4090（24564 MiB） |
| 驱动 | 535.86.05 |
| CPU | 112 逻辑核 Xeon Gold 6330 @ 2.00GHz |
| 内存 | 502GB |
| 磁盘布局 | 系统盘 `/` 仅 22GB；**数据盘 `/root/siton-tmp` 190GB**（项目所在地） |

> **磁盘结论**：项目、conda 环境、权重、缓存全部放在 `/root/siton-tmp` 下，
> 不写入系统盘，避免 22GB 系统盘写满。

## 2. 网络与镜像策略（中国大陆服务器必读）

### 2.1 连通性实测

| 源 | 直连结果 | 采用方案 |
|---|---|---|
| `repo.anaconda.com` / `conda.anaconda.org` | 不通（000） | conda 走清华镜像 conda-forge |
| `mirrors.tuna.tsinghua.edu.cn` | 通 | conda 唯一频道来源 |
| `huggingface.co` | 不通（000） | `HF_ENDPOINT=https://hf-mirror.com` |
| `hf-mirror.com` | 通 | 权重下载来源 |
| `github.com` | 通 | LatentSync 官方仓库、insightface 权重直连 |
| `download.pytorch.org` | 通（wheel 索引 200） | torch cu128 按脚本原样安装 |
| `pypi.org` / 容器预配 pip 镜像 | 通（容器已预配阿里云 PyPI 镜像） | pip 按容器默认 |
| apt | 容器已预配阿里云 Ubuntu jammy 源 | ffmpeg、git-lfs 走 apt |

### 2.2 conda 配置要点（踩坑记录）

1. Miniconda 自带的 `$CONDA_PREFIX/.condarc`（即
   `/root/siton-tmp/miniconda3/.condarc`）默认写入 `channels: defaults`，
   且其优先级高于 `~/.condarc`——**必须直接覆盖安装目录内的文件**，
   否则 conda 26.x 启动时会访问被墙的 `repo.anaconda.com` 做服务条款检查而卡死。
2. 清华镜像已不再提供 Anaconda 官方 `pkgs/main`、`pkgs/r` 频道（404），
   因此 `defaults` 频道不可用；所有 conda 包（含 Python 3.11.9 / 3.10.13、
   pip 24.2 / 24.3.1、pynini）全部改由 **conda-forge 镜像**提供。

最终 `/root/siton-tmp/miniconda3/.condarc`（同时复制到 `/root/.condarc`）：

```yaml
channels:
  - conda-forge
custom_channels:
  conda-forge: https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud
default_channels:
  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge
show_channel_urls: true
```

## 3. 安装位置总览

| 组件 | 路径 | 来源 |
|---|---|---|
| Miniconda | `/root/siton-tmp/miniconda3` | 清华镜像 Miniconda3-latest-Linux-x86_64.sh，conda 26.5.3 |
| conda pkgs 缓存 | `/root/siton-tmp/miniconda3/pkgs` | 默认（位于数据盘） |
| 项目代码 | `/root/siton-tmp/duikouxing` | git clone 或 git archive 上传 |
| 编排环境 | `/root/siton-tmp/duikouxing/.conda-envs/digital-human` | Python 3.11.9 + pip 24.2 |
| 声音克隆环境 | `/root/siton-tmp/duikouxing/.conda-envs/dots-tts` | Python 3.11.9 + torch 2.8.0 cu128 + dots.tts 0.3.1 + pynini |
| 口型推理环境 | `/root/siton-tmp/duikouxing/.conda-envs/latentsync` | Python 3.10.13 + 官方 requirements.txt（torch 2.5.1、diffusers 0.32.2） |
| LatentSync 官方仓库 | `/root/siton-tmp/duikouxing/external/LatentSync` | github.com/bytedance/LatentSync，锁定提交 `a229c39` + 补丁 |
| LatentSync 权重 | `/root/siton-tmp/duikouxing/external/LatentSync/checkpoints` | hf-mirror `ByteDance/LatentSync-1.6` 全量（约 9.64GB） |
| InsightFace 权重 | `.../checkpoints/auxiliary/models/buffalo_l/` | GitHub insightface v0.7 buffalo_l + 官方 2d106det |
| SD VAE 缓存 | `/root/siton-tmp/duikouxing/.cache/huggingface` | hf-mirror `stabilityai/sd-vae-ft-mse` |
| dots.tts 权重 | `/root/siton-tmp/duikouxing/models/dots.tts-soar`、`dots.tts-mf` | hf-mirror `dots-studio/*` |
| ffmpeg / git-lfs | 系统 apt（`/usr/bin`） | Ubuntu jammy：ffmpeg 4.4.2、git-lfs 3.0.2 |

## 4. 分步安装

### 步骤 1：系统软件

```bash
sudo apt-get update
sudo apt-get install -y git git-lfs ffmpeg libgl1 build-essential curl unzip
git lfs install
nvidia-smi
ffmpeg -version
```

确认：ffmpeg 含 libx264/AAC，nvidia-smi 可见 RTX 4090。

### 步骤 2：Miniconda

```bash
# 数据盘安装（避免写满系统盘）
curl -fsSL -o /root/siton-tmp/miniconda.sh \
  https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash /root/siton-tmp/miniconda.sh -b -p /root/siton-tmp/miniconda3

# 写入 PATH
echo 'export PATH=/root/siton-tmp/miniconda3/bin:$PATH' >> /root/.bashrc
source /root/.bashrc

# 配置国内镜像（覆盖安装目录内的 .condarc，详见第 2.2 节）
cat > /root/siton-tmp/miniconda3/.condarc << 'EOF'
channels:
  - conda-forge
custom_channels:
  conda-forge: https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud
default_channels:
  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge
show_channel_urls: true
EOF
cp /root/siton-tmp/miniconda3/.condarc /root/.condarc
```

### 步骤 3：获取项目代码

```bash
mkdir -p /root/siton-tmp
cd /root/siton-tmp

# 方式 A：git clone（推荐，如果仓库已推送）
git clone <your-repository-url> duikouxing
cd duikouxing
git switch feature/idcard-faceswap

# 方式 B：本地打包上传（无 .git，适合首次部署）
# 本地执行：git archive --format=tar.gz HEAD > duikouxing-src.tar.gz
# scp -P 34300 duikouxing-src.tar.gz root@219.147.100.42:/root/siton-tmp/
cd /root/siton-tmp
tar xzf duikouxing-src.tar.gz
mv duikouxing-src duikouxing  # 或按实际解压目录名

# Windows CRLF 修复（如果从 Windows 打包上传）
cd /root/siton-tmp/duikouxing
find . -type f -name '*.sh' -exec sed -i 's/\r$//' {} +
find . -type f -name '*.py' -exec sed -i 's/\r$//' {} +
find . -type f -name '*.patch' -exec sed -i 's/\r$//' {} +
find . -type f -name '*.yaml' -exec sed -i 's/\r$//' {} +
```

> **建议**：在仓库添加 `.gitattributes`（`*.sh text eol=lf`、`*.patch text eol=lf`）
> 根治 CRLF 问题。

### 步骤 4：创建环境 + 克隆 LatentSync + 应用补丁

```bash
export PATH=/root/siton-tmp/miniconda3/bin:$PATH
export HF_ENDPOINT=https://hf-mirror.com
export PIP_DEFAULT_TIMEOUT=60 PIP_RETRIES=10
cd /root/siton-tmp/duikouxing
bash scripts/setup_cloud_4090.sh
```

该脚本会：

1. 创建 Python 3.11.9 编排环境（`.conda-envs/digital-human`）。
2. 创建 Python 3.11.9 dots.tts 环境（`.conda-envs/dots-tts`）。
3. 创建 Python 3.10.13 LatentSync 环境（`.conda-envs/latentsync`）。
4. 克隆官方 `bytedance/LatentSync`，固定到 `a229c3948406bc2cf6eaf4873e662e70c6a04746`。
5. 安装官方 `requirements.txt`。
6. 应用 `patches/latentsync-1.6-quality-mux.patch`（高画质封装）。
7. 应用 `patches/latentsync-audio-amplitude.patch`（口型幅度旋钮）。

各环境实际版本：

| 环境 | Python | 关键包 |
|---|---|---|
| digital-human | 3.11.9 | numpy 2.2.6、opencv-python-headless 4.12、PyYAML 6.0.3 |
| dots-tts | 3.11.9 | torch 2.8.0+cu128、dots.tts 0.3.1、pynini |
| latentsync | 3.10.13 | torch 2.5.1、diffusers 0.32.2、insightface 0.7.3 |

### 步骤 5：下载权重

```bash
export PATH=/root/siton-tmp/miniconda3/bin:$PATH
export HF_ENDPOINT=https://hf-mirror.com
cd /root/siton-tmp/duikouxing
bash scripts/download_cloud_models.sh
```

下载内容：

| 内容 | 大小 | 来源 |
|---|---|---|
| LatentSync-1.6 全量权重 | 9.3GB | hf-mirror `ByteDance/LatentSync-1.6` |
| sd-vae-ft-mse | 670MB | hf-mirror |
| dots.tts-soar | 4.9GB | hf-mirror |
| dots.tts-mf | 4.9GB | hf-mirror |
| insightface buffalo_l.zip | 277MB | GitHub（可能需要代理） |

> **InsightFace 下载注意**：GitHub 直连可能仅 10～24KB/s，
> 可通过 `gh-proxy.com` 代理加速。手动下载后解压至
> `external/LatentSync/checkpoints/auxiliary/models/buffalo_l/`。

下载完成后验证文件存在：

```text
external/LatentSync/checkpoints/latentsync_unet.pt
external/LatentSync/checkpoints/stable_syncnet.pt
external/LatentSync/checkpoints/whisper/tiny.pt
external/LatentSync/checkpoints/auxiliary/models/buffalo_l/det_10g.onnx
external/LatentSync/checkpoints/auxiliary/models/buffalo_l/2d106det.onnx
.cache/huggingface/  (sd-vae-ft-mse)
models/dots.tts-soar/
models/dots.tts-mf/
```

不要手工改权重文件名。

## 5. 环境验证

```bash
export PATH=/root/siton-tmp/miniconda3/bin:$PATH
cd /root/siton-tmp/duikouxing
conda run -p .conda-envs/digital-human \
  python -m digital_human.cli doctor --profile cloud
```

预期输出（全部 OK）：

```text
[OK] FFmpeg: 4.4.2
[OK] FFprobe: ...
[OK] dots.tts CUDA: 2.8.0+cu128
[OK] LatentSync CUDA: 2.5.1+cu121 NVIDIA GeForce RTX 4090
[OK] GPU profile cloud: NVIDIA GeForce RTX 4090
[OK] 权重: latentsync_unet.pt / whisper/tiny.pt / stage2_512.yaml
[OK] 权重: buffalo_l/det_10g.onnx / 2d106det.onnx
[OK] dots.tts SOAR / MF
[OK] LatentSync commit: a229c3948406bc2cf6eaf4873e662e70c6a04746
```

## 6. 磁盘占用参考

`/root/siton-tmp` 共 190G，全新安装后约 51G：

| 路径 | 大小 |
|---|---|
| `.conda-envs/digital-human` | 513MB |
| `.conda-envs/dots-tts` | 7.6GB |
| `.conda-envs/latentsync` | 7.4GB |
| `external/LatentSync/checkpoints` | 9.3GB |
| `models/dots.tts-soar` + `models/dots.tts-mf` | 9.8GB |
| `.cache`（pip 为主，可复用） | 9.0GB |
| `miniconda3` | 1.9GB |

## 7. 运行任务

### 7.1 配置任务

```bash
cp config/job.example.yaml config/job.cloud.yaml
```

必须修改：

```yaml
job_id: "customer-test-001"
consent_confirmed: true
local_only: false
source_video: "../samples/source.mp4"
reference_audio: "../samples/reference.wav"  # 或留空从源视频截取
reference_text: "与参考音频逐字一致"
script: "新话术"
```

保持：

```yaml
lipsync:
  engine: "latentsync_1_6"
  inference_steps: 30
  guidance_scale: 1.3
  seed: 1247
  enable_deepcache: false

composite:
  mode: "native"
```

### 7.2 执行

```bash
export PATH=/root/siton-tmp/miniconda3/bin:$PATH
bash scripts/run_job.sh config/job.cloud.yaml
```

配置变更但复用同一 `job_id`：

```bash
bash scripts/run_job.sh config/job.cloud.yaml --force
```

产物：

```text
jobs-cloud/<job_id>/output/final.mp4
jobs-cloud/<job_id>/manifest.json
jobs-cloud/<job_id>/logs/latentsync.log
```

### 7.3 首轮 A/B 测试

**不要直接跑 30 秒完整视频。** 先准备同一 3～5 秒素材，依次测试：

```yaml
# A
inference_steps: 20
guidance_scale: 1.3

# B
inference_steps: 30
guidance_scale: 1.3

# C
inference_steps: 30
guidance_scale: 1.2

# D
inference_steps: 40
guidance_scale: 1.3
```

每次使用新 `job_id`，避免缓存产物混淆。
盲测文件名不得包含引擎名和参数。
不允许只看单帧，必须以 1× 和 0.25× 观看动态结果。

## 8. 常见问题

### 缺少 VAE / 尝试连网

重跑 `bash scripts/download_cloud_models.sh`。

### 高画质 mux 补丁未应用

重跑 `bash scripts/setup_cloud_4090.sh`。
不要手工删除 Adapter 中的补丁校验。

### 嘴部闪烁

1. `guidance_scale: 1.3` 降到 `1.2`。
2. 保持 seed 不变重跑。
3. 检查源视频是否有快速转头/遮挡。

### OOM

1. 确认没有其他 GPU 进程（`nvidia-smi`）。
2. 确认任务使用 FP16。
3. 仍 OOM 则使用 32/48GB 实例，**不降到 256 分辨率**。

### conda 启动卡死

检查 `.condarc` 是否配置了国内镜像（见第 2.2 节）。
conda 26.x 会访问 `repo.anaconda.com` 做服务条款检查，被墙会卡死。

### pip 下载超慢

平台代理可能导致速度骤降（记录到 18KB/s）。
终止后重跑，pip 缓存可复用。

## 9. 遗留事项与建议

1. **仓库换行符**：建议添加 `.gitattributes`（`*.sh text eol=lf`、`*.patch text eol=lf`）。
2. **SSH 密钥登录**：当前使用密码登录，建议改用 SSH 密钥。
3. **服务器代码管理**：`/root/siton-tmp/duikouxing` 如为 `git archive` 快照，
   建议改为 `git clone` 以便后续更新。
4. **首轮任务**：务必先跑 3～5 秒素材 A/B，不要直接跑长视频。