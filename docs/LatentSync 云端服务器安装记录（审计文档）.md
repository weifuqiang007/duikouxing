# LatentSync 云端服务器安装记录（审计文档）

> 本文档记录 `codex/latentsync-1.6-cloud` 分支在一台租用 GPU 服务器上的完整安装过程，
> 供后续审计与复现使用。执行日期：2026-08-18。
> 操作方式：Claude Code 通过 SSH 远程执行，每一步的完整日志保存在服务器
> `/root/install_logs/` 目录。

## 1. 服务器信息

| 项目 | 值 |
|---|---|
| SSH 入口 | `ssh -p 34300 root@219.147.100.42`（siton 平台容器实例） |
| 容器系统 | Ubuntu 22.04.3 LTS（宿主机内核 openEuler 5.10） |
| GPU | NVIDIA GeForce RTX 4090（24564 MiB） |
| 驱动 | 535.86.05（支持 CUDA 12.2 运行时，满足 cu121 wheel 要求） |
| CPU | 112 逻辑核 Xeon Gold 6330 @ 2.00GHz |
| 内存 | 502GB |
| 磁盘布局 | 系统盘 `/` 仅 22GB；**数据盘 `/root/siton-tmp` 190GB**（项目所在地） |

磁盘结论：项目、conda 环境、权重、缓存全部放在 `/root/siton-tmp` 下，
不写入系统盘，避免 22GB 系统盘写满。

## 2. 网络与镜像策略（与官方手册的偏差）

安装前连通性实测：

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

**conda 配置要点**（审计重点）：

1. Miniconda 自带的 `$CONDA_PREFIX/.condarc`（即
   `/root/siton-tmp/miniconda3/.condarc`）默认写入 `channels: defaults`，
   且其优先级高于 `~/.condarc`——必须直接覆盖安装目录内的文件，
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

## 3. 安装位置总览（每样东西装在哪）

| 组件 | 路径 | 来源 |
|---|---|---|
| Miniconda | `/root/siton-tmp/miniconda3` | 清华镜像 Miniconda3-latest-Linux-x86_64.sh，conda 26.5.3 |
| conda pkgs 缓存 | `/root/siton-tmp/miniconda3/pkgs` | 默认（位于数据盘） |
| 项目代码 | `/root/siton-tmp/duikouxing` | 本地 `git archive 2b619a1` 打包 SFTP 上传（分支未推送 GitHub） |
| 编排环境 | `/root/siton-tmp/duikouxing/.conda-envs/digital-human` | Python 3.11.9 + pip 24.2 |
| 声音克隆环境 | `/root/siton-tmp/duikouxing/.conda-envs/dots-tts` | Python 3.11.9 + torch 2.8.0 cu128 + dots.tts 0.3.1 + pynini |
| 口型推理环境 | `/root/siton-tmp/duikouxing/.conda-envs/latentsync` | Python 3.10.13 + 官方 requirements.txt（torch 2.5.1、diffusers 0.32.2） |
| LatentSync 官方仓库 | `/root/siton-tmp/duikouxing/external/LatentSync` | github.com/bytedance/LatentSync，锁定提交 `a229c39` + 高画质 mux 补丁 |
| LatentSync 权重 | `/root/siton-tmp/duikouxing/external/LatentSync/checkpoints` | hf-mirror `ByteDance/LatentSync-1.6` 全量（约 9.64GB） |
| InsightFace 权重 | `.../checkpoints/auxiliary/models/buffalo_l/` | GitHub insightface v0.7 buffalo_l + 官方 2d106det |
| SD VAE 缓存 | `/root/siton-tmp/duikouxing/.cache/huggingface` | hf-mirror `stabilityai/sd-vae-ft-mse` |
| dots.tts 权重 | `/root/siton-tmp/duikouxing/models/dots.tts-soar`、`dots.tts-mf` | hf-mirror `dots-studio/*` |
| ffmpeg / git-lfs | 系统 apt（`/usr/bin`） | Ubuntu jammy：ffmpeg 4.4.2、git-lfs 3.0.2 |
| 安装过程日志 | `/root/install_logs/*.log`（`*.exit` 为退出码） | 每个后台步骤一份 |

## 4. 分步安装记录

### 步骤 1：系统软件（2026-08-18 15:09–15:10，exit 0）

```bash
apt-get update && apt-get install -y ffmpeg git-lfs
git lfs install
```

结果：ffmpeg 4.4.2（含 libx264/AAC）、git-lfs 3.0.2 安装完成。
日志：`/root/install_logs/aptpkg.log`。

### 步骤 2：Miniconda（2026-08-18 15:09–15:10，exit 0）

```bash
curl -fsSL -o /root/siton-tmp/miniconda.sh \
  https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash /root/siton-tmp/miniconda.sh -b -p /root/siton-tmp/miniconda3
```

结果：conda 26.5.3。PATH 写入 `/root/.bashrc`：
`export PATH=/root/siton-tmp/miniconda3/bin:$PATH`。
日志：`/root/install_logs/miniconda.log`。

### 步骤 3：上传项目代码（2026-08-18 15:07）

- 本地：`git archive --format=tar.gz HEAD(2b619a1)` → 50KB 源码包。
- SFTP 上传到 `/root/siton-tmp/duikouxing-src.tar.gz`，解压至
  `/root/siton-tmp/duikouxing`。
- **修复**：仓库内文本文件为 CRLF 换行，Linux bash 报
  `set: pipefail: invalid option name`。已对服务器副本全量执行
  `sed -i 's/\r$//'`（`git archive` 打包时 Windows 提交的 CRLF 被原样带出，
  根治方案见第 6 节）。

### 步骤 4：三个 conda 环境 + LatentSync 仓库 + 补丁（2026-08-18 15:22–16:38，exit 0）

```bash
export PATH=/root/siton-tmp/miniconda3/bin:$PATH
export HF_ENDPOINT=https://hf-mirror.com
export PIP_DEFAULT_TIMEOUT=60 PIP_RETRIES=10
cd /root/siton-tmp/duikouxing && bash scripts/setup_cloud_4090.sh
```

各环境实际版本（`pip list` / `conda list` 摘要）：

| 环境 | Python | 关键包 |
|---|---|---|
| digital-human | 3.11.9 | numpy 2.2.6、opencv-python-headless 4.12.0.88、PyYAML 6.0.3、pytest 8.4.1、ruff 0.15.12、local-digital-human 0.1.0（`pip install -e .[dev]`） |
| dots-tts | 3.11.9 | torch 2.8.0+cu128、torchaudio 2.8.0+cu128、dots.tts 0.3.1（--no-deps）、transformers 4.57.0、pynini（conda-forge）等 |
| latentsync | 3.10.13 | 官方 requirements.txt：torch 2.5.1、diffusers 0.32.2、transformers 4.48.0、huggingface-hub 0.30.2、insightface 0.7.3、onnxruntime-gpu 1.21.0、mediapipe 0.10.11、gradio 5.24.0 等 |

官方仓库与补丁：

- `external/LatentSync` 克隆自 `github.com/bytedance/LatentSync`，
  `git checkout a229c3948406bc2cf6eaf4873e662e70c6a04746` ✓
- `patches/latentsync-1.6-quality-mux.patch` 已应用
  （`git status` 显示 `M latentsync/pipelines/lipsync_pipeline.py`）✓

过程记录：

1. 第一次运行因 CRLF 换行失败（见步骤 3 修复）。
2. latentsync 环境的 pip 下载中途被平台代理拖到 18KB/s，
   手动终止后重跑（pip 缓存复用），第二次正常完成。
日志：`/root/install_logs/setup.log`（4400+ 行）。

### 步骤 5：模型权重下载（2026-08-18 16:43–17:25，exit 0）

**脚本修复（重要）**：仓库脚本原来调用 `hf download`，但官方
requirements 固定的 `huggingface-hub==0.30.2` 没有 `hf` 子命令
（0.34 才引入）。已将本地仓库与服务器副本的
`scripts/download_latentsync_models.sh`、`scripts/download_cloud_models.sh`
改为 `huggingface-cli download`（参数完全一致）。

下载实测（经平台代理 10.10.20.5:8025 出网）：

| 内容 | 大小 | 来源 | 实测 |
|---|---|---|---|
| LatentSync-1.6 全量权重 | 9.3GB | hf-mirror `ByteDance/LatentSync-1.6` | ≈60MB/s，约 3 分钟 |
| sd-vae-ft-mse | 670MB | hf-mirror | 正常完成，字节数与 API 清单逐一核对一致 |
| dots.tts-soar | 4.9GB | hf-mirror | ≈16MB/s，约 6 分钟 |
| dots.tts-mf | 4.9GB | hf-mirror | ≈12MB/s，约 6 分钟 |
| insightface buffalo_l.zip | 277MB | GitHub Releases **直连仅 24KB/s** | 改经 `gh-proxy.com` 代理 1.78MB/s，约 3 分钟 |

buffalo_l.zip 经 gh-proxy.com 下载后解压至
`external/LatentSync/checkpoints/auxiliary/models/buffalo_l/`，
包含 det_10g.onnx、2d106det.onnx、1k3d68.onnx、w600k_r50.onnx、genderage.onnx。
（ghfast.top 实测前 30 秒 562KB/s 后跌至 48KB/s，弃用；本机 GitHub 直连仅 11KB/s，不可行。）

日志：`/root/install_logs/models.log`、`/root/install_logs/buffalo.log`。

安装后清理：删除 `.tmp` 内 buffalo_l.zip、pip-unpack 临时目录、
源码 tarball、miniconda 安装包（`.tmp` 现仅 91MB）；
保留 `.cache/pip`（约 9GB，重装时可复用）。

## 5. 验证结果

### 5.1 doctor --profile cloud（2026-08-18 17:28，exit 0，全部 OK）

```text
[OK] FFmpeg / FFprobe（4.4.2）
[OK] dots.tts CUDA: 2.8.0+cu128
[OK] LatentSync CUDA: 2.5.1+cu121 NVIDIA GeForce RTX 4090
[OK] GPU profile cloud: NVIDIA GeForce RTX 4090
[OK] 权重: latentsync_unet.pt / whisper/tiny.pt / stage2_512.yaml（512 配置，非 256）
[OK] 权重: buffalo_l/det_10g.onnx / 2d106det.onnx
[OK] dots.tts SOAR / MF
[OK] LatentSync commit: a229c3948406bc2cf6eaf4873e662e70c6a04746
```

### 5.2 磁盘占用（2026-08-18 17:30，`/root/siton-tmp` 共 190G，已用 51G）

| 路径 | 大小 |
|---|---|
| `.conda-envs/digital-human` | 513MB |
| `.conda-envs/dots-tts` | 7.6GB |
| `.conda-envs/latentsync` | 7.4GB |
| `external/LatentSync/checkpoints` | 9.3GB |
| `models/dots.tts-soar` + `models/dots.tts-mf` | 9.8GB |
| `.cache`（pip 为主，可复用） | 9.0GB |
| `/root/siton-tmp/miniconda3` | 1.9GB |

### 5.3 启动第一个任务的方式

```bash
export PATH=/root/siton-tmp/miniconda3/bin:$PATH
cd /root/siton-tmp/duikouxing
# 上传客户视频到 samples/ 后：
cp config/job.example.yaml config/job.cloud.yaml   # 按手册第 8 节修改
bash scripts/run_job.sh config/job.cloud.yaml
```

首轮务必先按手册第 10 节用 3～5 秒素材做 A/B，不要直接跑长视频。

## 6. 遗留事项与建议

1. **仓库换行符**：建议本地仓库添加 `.gitattributes`
   （`*.sh text eol=lf`、`*.patch text eol=lf`）并重新规范化，
   否则每次 `git archive` 到 Linux 都会重现 CRLF 问题。
2. **服务器 root 密码**：已在对话中明文出现，安装验收后应立即修改密码
   或改用 SSH 密钥登录。
3. **服务器代码非 git 克隆**：`/root/siton-tmp/duikouxing` 是
   `git archive` 快照（无 `.git`），对应本地提交 `2b619a1`。
   如需在服务器上 git 管理，可改为克隆 GitHub 仓库（需先推送该分支）。
4. 首轮任务务必按手册第 10 节先跑 3～5 秒素材 A/B，不要直接跑长视频。
