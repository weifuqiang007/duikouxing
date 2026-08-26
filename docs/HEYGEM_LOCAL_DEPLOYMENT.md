# HeyGem 本地引擎部署与实施手册（本机适配版）

依据：`src/HEYGEM_LOCAL_PROTECTED_REGION_IMPLEMENTATION.md`（实施规格，公司电脑撰写）
分支：`codex/heygem-local-protected`（基于 `codex/latentsync-1.6-cloud`）
本文把规格中的公司机器路径适配到本机，并补充本机资产盘点与国内镜像策略。

## 1. 目标与范围

在现有数字人口型流水线中新增第三个口型引擎 `heygem_local`：

- 声音：继续用本仓库 `dots.tts`（本地克隆音色），不部署 HeyGem 自带 TTS/ASR。
- 口型：HeyGem / Duix.Avatar 的本地 Docker 视频合成服务（Lite：只起 `duix-avatar-gen-video` 容器）。
- 证件保护区：以 HeyGem 完整帧为画面（不做嘴部贴片），从与 HeyGem 输入完全相同的
  `base_duration_matched.mp4` 逐帧恢复证件/画板等多边形保护区，FFV1 无损中间件，
  一次 H.264 编码封装目标音频。

硬性红线（引自规格 §1，实施全程有效）：

- 禁止裁小嘴贴回原脸；禁止生成式模型重绘证件内容。
- 禁止在输入输出帧无法一一对应时强行合成。
- 禁止静默缩放、丢帧、补帧或截断视频。

## 2. 本机资产盘点（2026-08-25 实测）

### 2.1 硬件与系统

| 项 | 实际情况 | 规格/配置预期 | 结论 |
|---|---|---|---|
| GPU | NVIDIA RTX 4070 Ti 12GB，驱动 591.86 | home 档 "RTX 4070" 12GB | 兼容（doctor 用子串匹配，可通过） |
| 仓库 | `E:\HEYGEM`（fresh clone，远端 GitHub） | 规格 `G:\duikouxing` | 路径适配 |
| Conda | `D:\tools\conda`（conda 24.5.0） | — | 可用，作可执行文件不改 |
| FFmpeg | `D:\tools\ffmpeg\bin`（已在 PATH） | — | 可用 |
| Docker | Docker Desktop 28.0.1（WSL2，`docker-desktop` 发行版当前 Stopped） | 需要 GPU 容器 | 已装，需启动并迁移盘镜像位置 |
| 磁盘 | E: 剩 1.2T；C: 剩 79G | — | 充足；Docker 盘镜像必须迁到 E 盘 |
| 网络 | Clash 代理 127.0.0.1:7890 可用；优先国内镜像 | — | 见 §4 |

### 2.2 旧工作副本可复用资产（`E:\duikouxing`，本机旧目录）

本机就是规格所称的"家里电脑"，旧副本里已有可复用的资产。**旧副本被另一个项目依赖：
对 `E:\duikouxing` 一律只读——只复制，不移动、不修改、不删除**（2026-08-25 用户确认）。

| 资产 | 大小 | 处置 |
|---|---:|---|
| `.conda-envs\dots-tts`（torch 2.8.0 cu128 全套） | 8.2G | **复制**到 `E:\HEYGEM\.conda-envs\dots-tts`，见 §5.2 |
| `models\dots.tts-soar` | 4.9G | **复制**到 `E:\HEYGEM\models\dots.tts-soar` |
| `.conda-envs\digital-human` | 379M | 不迁，用脚本新建（体积小、保证入口脚本路径正确） |
| `external\MuseTalk`、`external\LivePortrait`、musetalk/liveportrait 环境 | ~19G | **不迁**：`heygem_local` 不需要本地口型环境，口型在 Docker 容器内 |
| `.cache`（HF/torch/pip 缓存 12G） | 12G | 不迁，模型已本地化后无用 |
| `jobs-home`、旧 samples | ~250M | 保留在旧目录，不迁移 |

> 结论：依赖下载量从"两个 torch 环境 + 10G 模型"降为"orchestrator 小环境 + Docker 镜像"。

### 2.3 需要修的本机遗留问题

1. 仓库根 `.condarc` 的 `pkgs_dirs` 仍指向 `E:/duikouxing/.conda-pkgs`（旧路径，且该文件被
   Git 跟踪）。必须改为 `E:/HEYGEM/.conda-pkgs`，否则 conda 包缓存写回旧目录。
2. `config/job.cloud6.yaml` 有一处未提交修改（`source_video` 改为 `../samples/wlh.mp4`），
   与本任务无关，**保留不动**。
3. Docker Desktop 磁盘镜像默认在 C 盘，需手动迁移（见 §5.4）。

## 3. 存储布局：全部在 E:\HEYGEM 下，未来可整体删除

规格原本把运行时放独立的 `F:\duikouxing-runtime`；本机按用户要求收敛进仓库目录：

```text
E:\HEYGEM\
├── .conda-envs\
│   ├── digital-human\        # 新建，编排环境（pytest/ruff/CLI）
│   └── dots-tts\             # 复制自旧副本（旧副本保留不动），8.2G
├── .conda-pkgs\              # conda 包缓存（.condarc 已指向此处）
├── models\
│   └── dots.tts-soar\        # 复制自旧副本（旧副本保留不动），4.9G
├── .cache\                   # HF/torch/pip 缓存（CLI 启动时自动定向）
├── .tmp\                     # 临时目录
├── runtime\                  # ★ 本任务新增运行时根
│   ├── heygem\
│   │   └── data\face2face\   # HeyGem 共享目录 → 容器 /code/data
│   │       └── temp\         # 适配器按任务码建 stage 目录
│   └── docker-desktop\       # Docker Desktop 磁盘镜像位置（手动迁移）
├── jobs-home\<job_id>\       # 任务输出（input/work/output/logs）
├── deploy\heygem\            # docker-compose.yml（Git 跟踪）
└── samples\wlh.mp4           # 测试源视频（720×1280 竖屏 30fps 13.6s，未跟踪）
```

预计占用：环境 8.6G + 模型 4.9G + Docker 镜像（duix.avatar，约 10–25G）≈ 25–40G。

`.gitignore` 需补充：`runtime/`（`.conda-envs/`、`models/`、`.cache/`、`.tmp/`、
`jobs-home/` 均已在列）。

**配置代码层面的简化说明**：规格 §5.1 要求把 `LocalConfig` 改成显式 `storage_root`，
动机是公司机器"仓库在 G:、运行时在 F:"跨盘。本机两者同在 `E:\HEYGEM`，现有
`PROJECT_STORAGE_ROOT` 校验（config.py，一切环境/模型/任务路径必须位于仓库根下）
已经天然满足"全部在本文件夹下"的要求。因此实施时**保留现有校验机制**，只为
HeyGem 新增字段，不引入跨盘 `storage_root` 重构——这是对规格的有意精简，其余
路径校验规则（`require_under`、拒绝目录穿越）照搬。

## 4. 网络策略：国内镜像优先，Clash 兜底

| 依赖 | 首选（镜像） | 兜底（代理 127.0.0.1:7890） |
|---|---|---|
| conda 包 | 南京大学镜像 `mirror.nju.edu.cn/anaconda`（仓库 `.condarc` 已配置，setup 脚本 `--override-channels` 强制） | 无需 |
| pip 包 | 清华 `https://pypi.tuna.tsinghua.edu.cn/simple` | `HTTPS_PROXY=http://127.0.0.1:7890` |
| HuggingFace（如需补模型） | `HF_ENDPOINT=https://hf-mirror.com`，且 `NO_PROXY` 加 `hf-mirror.com`（Clash 会把 hf-mirror 的 308 重写回被墙域名，`download_models.ps1` 已内置该处理） | 直连走代理 |
| Docker 镜像 `guiji2025/duix.avatar` | Registry 镜像前缀拉取后 retag，例如 `docker pull docker.1ms.run/guiji2025/duix.avatar`（备选 `docker.m.daocloud.io`、`dockerpull.org`，可用性会漂移，实测为准） | Docker Desktop → Settings → Resources → Proxies 填 `http://127.0.0.1:7890` 后直接 `docker pull` |
| GitHub（clone/push） | — | `git config http.proxy http://127.0.0.1:7890` |

> 本次部署实际上只有 Docker 镜像是大额下载；dots.tts 环境与模型从旧目录平移，
> orchestrator 依赖走清华 pip 源（几十 MB）。

## 5. 部署步骤（阶段 0：环境就绪，不改业务代码）

按序执行；每步带验证命令。PowerShell，仓库根 `E:\HEYGEM`。

### 5.1 修正 `.condarc`

把 `pkgs_dirs` 改为 `E:/HEYGEM/.conda-pkgs`（其余南京大学镜像配置保留）：

```powershell
Set-Location E:\HEYGEM
notepad .condarc   # pkgs_dirs: - E:/HEYGEM/.conda-pkgs
```

### 5.2 复制 dots-tts 环境与模型（旧副本保持原样）

同盘复制约 13G，SSD 上几分钟完成。用 robocopy（多线程、可断点续传；返回码 0–7 均为
成功，≥8 才算失败）：

```powershell
robocopy E:\duikouxing\.conda-envs\dots-tts E:\HEYGEM\.conda-envs\dots-tts /E /MT:16 /R:2 /W:5
robocopy E:\duikouxing\models\dots.tts-soar E:\HEYGEM\models\dots.tts-soar /E /MT:16 /R:2 /W:5
```

复制品的 pip console-script 入口（如 `dots.tts.exe`）内嵌的是旧副本的 Python 绝对
路径，会失效。对新副本执行 `--force-reinstall --no-deps` 重写入口（只重装几 KB 的
包装器，不动 8.2G 依赖，也不碰旧副本）：

```powershell
conda run -p E:\HEYGEM\.conda-envs\dots-tts python -m pip install `
  --force-reinstall --no-deps -i https://pypi.tuna.tsinghua.edu.cn/simple `
  dots.tts==0.3.1
```

验证（GPU 可见 + 模型目录完整）：

```powershell
conda run -p E:\HEYGEM\.conda-envs\dots-tts python -c `
  "import torch; assert torch.cuda.is_available(); print(torch.__version__, torch.cuda.get_device_name(0))"
conda run -p E:\HEYGEM\.conda-envs\dots-tts dots.tts --help
Get-ChildItem E:\HEYGEM\models\dots.tts-soar | Measure-Object -Property Length -Sum
```

> 若入口重装后仍异常（极小概率），退路是用 `environments/dots-tts.yml` 重建环境
> （torch 2.8.0 cu128 约 3G，走清华 pip + 必要时代理）。旧副本始终不动。

### 5.3 新建 orchestrator 环境

只建编排环境，不跑全量 `setup_conda.ps1`（那会连 MuseTalk 一起装，本任务不需要）：

```powershell
conda create -y -p E:\HEYGEM\.conda-envs\digital-human --override-channels `
  -c https://mirror.nju.edu.cn/anaconda/pkgs/main python=3.11.9 pip=24.2
conda run -p E:\HEYGEM\.conda-envs\digital-human python -m pip install `
  -i https://pypi.tuna.tsinghua.edu.cn/simple -e "E:\HEYGEM[dev]"
```

### 5.4 Docker Desktop：启动（2026-08-25 实测后调整：数据不迁移）

实测发现本机 Docker Desktop 28.0.1 装在 `D:\tools\docker`，WSL 数据目录已通过
`CustomWslDistroDir` 指到 `D:\tools\wsl`（16G，含其他项目的 n8n/redis 镜像）——
**本来就不在 C 盘**。

决定：**不迁移数据目录**。迁移会把其他项目的 Docker 数据卷进本任务的删除范围，
破坏隔离；HeyGem 将来的清理路径是 `docker rm` 容器 + `docker rmi` 镜像，与数据目录
位置无关。`runtime\docker-desktop` 目录不再创建。

启动与就绪判定：

```powershell
Start-Process 'D:\tools\docker\docker\Docker Desktop.exe'
# 就绪判定：Server 版本非空。注意启动中途 docker info 会短暂返回 500，勿误判失败
docker version --format '{{.Server.Version}}'
```

容器内 GPU 可见性并入 §5.7 健康检查第 2 项（`docker exec ... nvidia-smi`），
不为单独验证再拉 CUDA base 镜像。

### 5.5 拉取 HeyGem 镜像（Lite 只需这一个）

```powershell
# 首选：镜像站前缀拉取后改标签
docker pull docker.1ms.run/guiji2025/duix.avatar
docker tag  docker.1ms.run/guiji2025/duix.avatar guiji2025/duix.avatar

# 兜底：Docker Desktop 代理设为 http://127.0.0.1:7890 后直接拉
docker pull guiji2025/duix.avatar

# 记录 ID 与 digest（生产前固定 digest，规格 §4 要求）
docker images --digests guiji2025/duix.avatar
```

> **实际执行记录（2026-08-25/26）**：本机网络下 Docker Hub 大层经任何路径（daemon 直连/
> daemon 走系统代理）都会挂起，15 家公共国内镜像站的 blob 通道全部不可用。最终方案：
> 宿主机分段下载器 `.tmp/duix_fetch.py`（5GB 大层切 75×64MB 块、10 并发走 Clash 7890、
> 断点续传、SHA256 校验，耗时约 3 小时）→ `.tmp/duix_build.py` 从 NTFS ADS 流恢复并组装
> legacy docker-archive（diff_id 校验通过）→ `docker load -i duix.tar`。
> 镜像 ID `24aeba09f70b`（9.89GB）。`.tmp` 目录不入 Git，重装时按此记录重建；
> 另注意 Windows 文件名不能含冒号，digest 命名文件会落入 NTFS ADS 流。

### 5.6 建目录 + 首启容器

阶段 A 尚无 compose 文件时，可先手工起容器验证（正式形态由阶段 B 的
`deploy/heygem/docker-compose.yml` 固化，参数一致）：

```powershell
New-Item -ItemType Directory -Force E:\HEYGEM\runtime\heygem\data\face2face\temp

docker run -d --name heygem-gen-video --restart unless-stopped `
  --runtime nvidia --privileged --gpus all `
  --shm-size 8gb -p 127.0.0.1:8383:8383 `
  -v E:/HEYGEM/runtime/heygem/data/face2face:/code/data `
  -e NVIDIA_VISIBLE_DEVICES=0 `
  -e NVIDIA_DRIVER_CAPABILITIES=compute,graphics,utility,video,display `
  -e PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:512 `
  guiji2025/duix.avatar python /code/app_local.py
```

### 5.7 健康检查（规格 §4 四项）

```powershell
docker inspect -f '{{.State.Status}}' heygem-gen-video        # 1) running
docker exec heygem-gen-video nvidia-smi                        # 2) 容器可见 GPU
curl.exe -s http://127.0.0.1:8383/easy/query?code=not-exist    # 3)+4) 返回 JSON 而非连接错误
```

四项全过 → 阶段 0 完成。

## 6. 阶段 A：HeyGem 原生效果手工验证（已完成，实测结果见 §6.1）

规格 §12：阶段 A 不通过，不进入代码集成。

1. 准备 5–10 秒、**不含真实证件**的测试素材。`samples/wlh.mp4`（13.6s）可截取前 6 秒
   或不含证件的画面；也可用旧副本 `E:\duikouxing\samples\driving.mp4`（LivePortrait
   官方样例，无证件）。
2. 目标音频：可先用 ffmpeg 从原片抽一段 wav（阶段 B 之前不必跑 dots.tts）。
3. 两个文件放入 `E:\HEYGEM\runtime\heygem\data\face2face\temp\<自选任务码>\`。
4. 手工调用（路径语义以实测为准，容器把共享目录视为 `/code/data`）：

```powershell
curl.exe -s -X POST http://127.0.0.1:8383/easy/submit `
  -H "Content-Type: application/json" `
  -d '{"audio_url":"temp/<任务码>/target.wav","video_url":"temp/<任务码>/base.mp4","code":"<任务码>","chaofen":0,"watermark_switch":0,"pn":1}'
curl.exe -s "http://127.0.0.1:8383/easy/query?code=<任务码>"
```

5. 核对返回的 `data.result` 相对路径语义、下载结果的分辨率 / FPS / 帧数 / 时长是否与
   输入一致（输入先统一 25fps；wlh.mp4 原生 30fps 须先转）。
6. 与原片做差异热力图，确认变化集中在人脸，身体与背景未被重绘。

产出记录到 `docs/`（API 字段实测结果），作为适配器的既定事实。

### 6.1 阶段 A 实测结果（2026-08-26，stagea-005，6 秒 720×1280@30fps）

**结论：通过。** 生成 20.9 秒完成；变化集中在嘴部，证件区为压缩噪声级变化，
无双嘴、无伪影、无可见画质劣化。

API 实测语义（**与规格 §6 假设不同，适配器必须按此实现**）：

| 项 | 实测语义 |
|---|---|
| `audio_url` / `video_url` | **必须是容器可下载的 HTTP(S) URL**，不是共享目录相对路径。实测用宿主机 `python -m http.server 8123` + `http://host.docker.internal:8123/temp/<code>/...` 成功。纯相对路径/绝对容器路径均失败（`三次获取音频时长失败`） |
| 失败清理 | 任务失败会**删除** `temp/<code>/` 输入目录；成功后产物为 `temp/<code>.wav`（下载的音频）、`temp/<code>.mp4`（中间件）、`temp/<code>-r.mp4`（最终 H.264 CRF15+AAC） |
| `data.result` | 形如 `/stagea-005-r.mp4`：**相对 `temp/` 目录**（前导斜杠无意义），即共享根下 `temp/<code>-r.mp4` |
| `/easy/query` | `status`: 2=成功 3=失败；成功附带 `width/height/video_duration(ms)/cost`；**任务记录在首次返回成功/失败后即删除**（一次性查询，适配器必须缓存结果或立即拷贝文件） |
| 并发 | 单任务串行；占用时 submit 返回 `10001 忙碌中` |
| 输入要求 | 输入视频建议带音轨（`-an` 静默视频行为未单独验证）；轮询间隔 ≥2s |

输出不变式实测（输入 180 帧 / 6.000s）：

| 不变式 | 结果 |
|---|---|
| 分辨率 720×1280 | ✅ 保持 |
| 帧率 30fps | ✅ 保持 |
| 时长 | ⚠️ 5.933s（-0.067s） |
| 帧数 | ⚠️ **178 vs 180，恒定少 2 帧**（另一次 001 任务同样现象） |

差异统计（阈值 >12，178 帧逐帧对比，脚本 `.tmp/stageA_diff.py`）：
证件区变化 **0.53%**、脸部 **6.46%**、全图 **1.42%** —— 变化集中于人脸，
证件区域未发生内容级重绘。热力图与对比图见 `.tmp/stageA_result/`。

### 6.2 画质时序稳定性调优记录（2026-08-26，wlh-007~011 五代对照）

| 版本 | 变更 | 鼻唇区附加抖动 | 人脸 Y 帧间跳变 p95/max |
|---|---|---|---|
| wlh-007 | 25fps 基准（3 次编码） | 1.49 | — |
| wlh-008 | **基准改 30fps 原生** | 1.31 | 0.71 / 1.18 |
| wlh-009 | 单遍 pingpong（1 次编码、CRF15）+ 色调稳定器 | 1.33 | 0.66 / 1.09 |
| wlh-010 | 裁掉源片前 3 秒不稳定段（source_start_seconds） | 客户认可基线 | 0.66 / 1.09 |
| wlh-011 | **+ 光流几何稳定器** | 见 §6.4 验收 | 0.65 / 1.08 |

结论：

1. **25fps 重采样是抖动主放大器**（同素材严格对照：30fps 输入 HeyGem 零附加抖动，
   25fps 输入 +36%）。heygem_local 任务必须用源视频原生帧率。
2. 基准链路编码次数 3→1、CRF 18→15 有小幅收益（Y max 1.18→1.09）。
3. `chaofen`、`pn` 参数在此镜像版为无效参数（输出逐字节一致）。
4. 源片"调整姿势/举证件"的不稳定段在正放与倒放中都会放大重绘抖动（用户实测
   0-3s 与 23-26s 两窗口，倒放段映射回同一源段）；`video.source_start_seconds`
   裁剪后消除。

### 6.3 色调稳定器 stabilize.py（2026-08-26）

**问题**：HeyGem 逐帧重绘使人脸出现 ±1 亮度级的帧间随机闪变（观感"曝光一闪一闪"；
实测 8-16s 窗口 Y 帧间跳变为基准 1.5 倍、Cb 色度达 2.2 倍）。

**方案**：两遍处理。第一遍统计每帧人脸羽化框内 Y/Cr/Cb 三通道均值，对每个通道的
均值序列求 EMA（0.9）平滑轨迹，校正量 = 平滑轨迹 − 实测值（死区 0.5 内不校正、
限幅 ±3.0）。第二遍把校正量按羽化掩膜加回帧内对应通道。编码走 rawvideo →
libx264 CRF10 + `chroma-qp-offset=-9` 管道——细色度量化使亚整数级偏移不被 4:2:0
量化重新打散成块噪声（第一版未加此参数时曾把 Cb 闪变放大 4 倍，教训在案）。

**边界（诚实记录）**：加性色调校正在 ±1 亮度级的闪变幅度上已到理论下限（校正量
与编码量化步长同量级）；实测收益为"无害 + 略微削峰"，颜色问题的客户认可主要来自
此稳定器与 30fps/单遍编码的组合。

### 6.4 光流几何稳定器 motion_stabilize.py（2026-08-26，wlh-011 客户验收通过）

**问题**：鼻/上唇等本应稳定的区域存在 ±0.3~1px 的帧间随机偏移（高频几何噪声），
是 HeyGem 逐帧重绘（无跨帧时序模型）的架构性残差。

**方案（五步）**：

1. **特征点播种**：脸部区 `goodFeaturesToTrack`（≤150 点、quality 0.05、
   minDistance 8、blockSize 9），并**挖掉嘴部 bbox**（归一化 0.38~0.62 ×
   0.30~0.44）——嘴部点随说话运动，是头部位移估计的污染源。点的放置纪律比
   数量重要：中位数聚合的估计误差 ~1.25σ/√n，150 点约 0.02px，比待消除的
   抖动小一个数量级，盲目加点无益。
2. **LK 光流跟踪**：帧对独立（31px 窗口、3 层金字塔；不逐帧链式传递，避免误差
   沿链累积）；有效点 < 20 判失败。
3. **中位数聚合**：每帧全体跟踪点位移的中位数 = 头部整体位移（对 ≤50% 离群点
   免疫；均值会被嘴部运动污染）。累加成位置轨迹。
4. **居中滑动平均分频**：15 帧@30fps 窗口，通带 <0.9Hz（真实头部运动）、抑制带
   >2Hz（重绘抖动）。**居中**（前 7 + 后 7 帧）而非因果窗口：离线两遍处理可
   非因果，零相位滞后、无拖影感。窗口不宜小（2 帧窗口通带 <6.6Hz 几乎全通，
   抖动原样通过），也不宜大（61 帧会连真实点头一起纠掉，且校正量超限幅会在
   羽化边界露接缝）。
5. **限幅校正回写**：校正量 = 原始轨迹 − 平滑轨迹（逐轴限幅 ±3px，超限不硬纠）；
   `cv2.warpAffine` 双线性亚像素平移整帧后与原帧按羽化掩膜融合——**脸部框内
   生效，背景与证件区一个像素不动**。失败帧校正沿用上一帧。与色调校正合并为
   同一对视频遍历、同一次编码。

**符号约定**（曾因方向写反把抖动放大 2 倍、被单测当场逮住）：
`motion_corrections` 返回"原始轨迹对平滑轨迹的偏离"方向，`warp_frame` 按
`(-dx,-dy)` 平移内容，使脸部落到平滑轨迹上。

**验收数据**（中位轨迹高频分解，wlh-011 素材）：

| 指标 | HeyGem 原始 | 稳定后 | 基准视频（真实运动地板） |
|---|---|---|---|
| 平移抖动（高频能量） | 0.554px | **0.257px（−54%）** | 0.605px |
| 真实运动（低频能量） | 4.60px | **4.48px（保留 97.4%）** | 5.09px |

特征点间散布（0.33px）稳定前后不变——那是嘴部说话的真实形变，稳定器只杀
整体平移抖动、不碰嘴部运动，属设计使然。窗口值按 7/15/31 三档 A/B 定档，
默认 15。

**配置**：`composite.face_motion_smooth_frames`（0/1 关闭）、
`composite.face_motion_max_shift`（默认 3.0px）。

### 6.5 质量问题归因与对策总表

| 客户观感 | 根因 | 对策 | 配置落点 |
|---|---|---|---|
| 整片口型区明显抖动 | 25fps 重采样放大逐帧重绘噪声 | 基准用源原生帧率 | `video.fps` |
| 鼻/上唇高频哆嗦 | HeyGem 无时序模型的架构残差 | 光流几何稳定 | `face_motion_smooth_frames` |
| 人脸"曝光闪" | 逐帧重绘的亮度/色度闪变 | 色调 EMA 校正 | `face_tone_ema` |
| 前 3 秒 + 23-26 秒抖 | 源片不稳定段被正放+倒放两次放大 | 裁剪源段 | `video.source_start_seconds` |
| 画质逐代劣化 | 基准链路 3 次编码 | 单遍 pingpong + CRF15 + 同帧率流拷贝 | `ffmpeg.py` |

**对后续阶段的直接影响**：

1. 适配器（阶段 B）需要内置**本地 HTTP 文件服务**组件（或复用一个固定端口的静态服务），
   并把 `data.result` 的 temp 相对路径解析、以及"查询即删"的缓存逻辑写进适配器。
2. `require_exact_frame_count`（规格 §5.2/§8）默认会在 178≠180 时拒绝合成。
   阶段 C 需决策帧对齐策略：候选为(a)按帧号对齐+容忍恒定偏移的核验、
   (b)向 HeyGem 输入前把 base 末尾补 2 帧冗余、(c)以 HeyGem 帧数为准截齐 base。
   在策略落地前，保护合成不可默认开启逐帧强校验。

## 7. 阶段 B–D：代码实施清单（阶段 B 已完成，2026-08-26）

**阶段 B 交付记录**：`config.py`（heygem 七字段 + protected_regions 校验 + 引擎白名单）、
`adapters/heygem.py`（HTTP 文件服务 + 实测 API 语义 + 清理策略）、`pipeline.py` 三分支、
`cli.py` doctor heygem 检查、`deploy/heygem/docker-compose.yml`、三个 PS 脚本、
`tests/test_heygem.py`（10 项 fake-server 测试）、`config/job.heygem.local.yaml`。
验收：pytest 21 项全过、ruff 无告警、doctor 全绿（仅 dots.tts-mf WARN，quality 档在位）、
端到端 `wlh-007-heygem-local` 任务完成——dots.tts 两段话术 → pingpong 32.2s 基准 →
HeyGem 71.3s 生成 → final.mp4（720×1280@25fps）。manifest 记录镜像 ID `24aeba09f70b`。

### 阶段 B：适配器接入

| 文件 | 动作 |
|---|---|
| `config/local.home.yaml` | 增加 `heygem:` 段（base_url/shared_root/timeout/poll），`primary_lipsync_engine: heygem_local`；路径全部相对仓库根，无需 storage_root 重构（见 §3 说明） |
| `src/digital_human/config.py` | `LocalConfig` 增 4 个 heygem 字段；engine 白名单加 `heygem_local`；共享目录等新路径纳入 `PROJECT_STORAGE_ROOT` 校验 |
| `src/digital_human/adapters/heygem.py` | `HeyGemAdapter`（规格 §6 骨架 + 项目异常类型；submit → 轮询 query → 安全结果路径 → 拷出） |
| `src/digital_human/pipeline.py` | 显式三分支 `latentsync_1_6 / musetalk_1_5 / heygem_local`，未知引擎直接抛错；manifest 增 `heygem_image_id/digest`、`protected_regions`、验收标志 |
| `deploy/heygem/docker-compose.yml` + `scripts/*_heygem_home.ps1` | 固化 §5.6 参数：端口仅绑 127.0.0.1，卷 `runtime/heygem/data/face2face:/code/data` |
| `tests/test_heygem.py` | fake HTTP 服务覆盖：字段、轮询、失败码、超时、非 JSON、`..`/绝对路径拒绝、空结果拒绝、日志脱敏 |
| `config/job.heygem.local.yaml` | 以 job.cloud6.yaml 为底：engine 换 `heygem_local`，话术沿用其 L11 `reference_text`（原话术）与 L13 `script`（新话术） |

完成标准：端到端跑出 `jobs-home/<job>/work/heygem_result.mp4`，暂不做任何二次合成。

### 阶段 C：证件保护

- `src/digital_human/protection.py`（规格 §8 逐帧恢复多边形保护区 + 帧不变量强校验）
- `src/digital_human/quality.py`（SSIM ≥ 0.995、核心区逐像素相同、热力图、关键帧对比图）
- CLI 增 `annotate-protected-region` / `preview-protected-regions`
- `tests/test_protection.py`、`tests/test_storage_layout.py`
- 先用虚构证件素材验收，再上已授权真实素材（不得提交 Git）

### 阶段 D：A/B 决策

同一 base + 同一目标音频输出四版对比：MuseTalk 最佳参数 / LatentSync 最佳参数 /
HeyGem 原生 / HeyGem+保护区。出现脸型漂移、双嘴、牙齿闪烁或帧不对应 → 只保留为实验
引擎，不设生产默认。

## 8. 完成定义（规格 §13 摘要）

pytest 全过、ruff 通过、`doctor --profile home` 覆盖 HeyGem 容器/端口/GPU/目录检查、
C 盘零新增大文件、5–10 秒虚构证件素材端到端成功、无损中间件保护区逐像素等于 base、
最终编码 SSIM ≥ 0.995、人工看片无嘴漂移/双嘴/贴片边、manifest 记录镜像 digest 且不含
证件号、README 与安装文档同步更新。

## 9. 风险与待决事项

1. ~~身份证号已入 Git 并推送远端~~ **已决（2026-08-25）**：`config/job.cloud*.yaml` 中
   的姓名与身份证号为虚构测试数据，无需治理；后续不再标记此风险。
2. **Docker 镜像许可证**：DUIX.COM Community License 非宽松许可。商用/外发前复核
   LICENSE 并留档镜像 digest 与许可证副本（规格 §15）。
3. **镜像站可用性漂移**：§4 的 Docker registry 镜像列表以实测为准，失败即切 Clash 代理。
4. **conda 环境复制**：复制 + 入口重装是主路线（旧副本不受任何影响）；异常时按 §5.2
   退路重建。
5. **素材合规**：`samples/wlh.mp4` 为竖屏 30fps，流水线会标准化 25fps；含真实证件画面，
   阶段 A 验证避免直接使用含证件片段。
