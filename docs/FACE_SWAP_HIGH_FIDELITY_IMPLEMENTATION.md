# 高保真视频换脸：产品与技术实施规格

> 交给 GLM-5 的开发依据。本文不是概念介绍，而是本项目的实现边界、目录规范、核心代码骨架、验收标准与分阶段交付清单。
>
> 核准日期：2026-08-26  
> 目标设备：Windows 11、32 GB 内存、NVIDIA RTX 4070 12 GB、F 盘 1 TB 以上可用空间  
> 代码仓库：`G:\duikouxing`  
> 运行时根目录：`F:\duikouxing-runtime\faceswap`

---

## 0. 给 GLM-5 的强制指令

1. 先完整阅读本文，再写代码；不允许把换脸功能硬塞进 MuseTalk、LatentSync、InfinityTalk 或 HeyGem 的口型流水线。
2. 当前工作区已有未提交实验改动。新建分支和独立 worktree 开发，不得覆盖、清理、还原或提交用户现有改动。
3. 源代码可以位于 G 盘；**Conda 环境、Python 包缓存、模型权重、FaceFusion 仓库、下载缓存、临时帧、任务数据、日志和输出必须全部位于 F 盘统一目录**。
4. 不得向 `C:\Users\...`、`%TEMP%`、默认 Hugging Face 缓存、默认 Torch 缓存或默认 Conda 目录下载大文件。程序启动前必须进行路径预检，发现运行时路径落在 C 盘立即报错退出。
5. 第一阶段只实现 FaceFusion 适配器和质量检测。不得顺手下载 DreamID-V、Wan 2.1 等大模型。
6. 所有外部命令必须用参数数组执行，禁止 `shell=True`，禁止拼接未转义的用户文件名。
7. 未通过授权和模型许可检查时，不得运行任务；商业任务不得默认使用 Non-Commercial、ResearchRAIL 或 Unknown 模型。
8. 不要用“把原视频嘴巴再贴回来”的方式修复口型。该做法非常容易形成双嘴、边缘贴纸感和位置漂移。
9. 每完成一个阶段都要运行对应测试；不得只做到“命令能启动”。

---

## 1. 产品目标与边界

### 1.1 输入

- 一段目标视频：视频中的人负责原有身体、头部姿态、说话节奏、口型、表情、动作、背景和音频。
- 一至五张同一人的来源照片：提供要替换成的身份外观。
- 明确的任务授权信息：来源人物、目标视频人物均已同意，且用途合法。

### 1.2 输出

- 只替换目标视频中的人脸身份。
- 保留目标视频的头部运动、眼神、表情、说话语气、口型时序、身体、衣服、手、证件、画板、身份证、背景和原音频。
- 嘴唇、牙齿和下颌边缘清晰，连续帧不闪烁、不漂移、不出现“两张嘴”。
- 分辨率、帧率、时长和音轨与目标视频一致，除一次必要编码外不做重复压制。

### 1.3 不在本阶段范围内

- 不克隆声音，不根据文字重新生成口型。
- 不更换发型、耳朵、完整头型、身体或服装。
- 不处理多人自动换脸；MVP 一次只允许目标视频中有一张主要人脸。
- 不承诺仅凭一张低清、严重美颜或大角度照片还原全部侧脸细节。
- 不把系统用于身份认证绕过、诈骗、虚假代言、伪造证据、色情换脸或未经授权的公众人物内容。

### 1.4 与 MuseTalk 的根本区别

| 项目 | MuseTalk | 本文换脸流程 |
|---|---|---|
| 要解决的问题 | 用新音频驱动嘴型 | 把 A 的身份换到 B 的表演视频上 |
| 身份来源 | 仍是目标视频中的人 | 来源照片中的人 |
| 动作/表情来源 | 目标视频 + 新音频口型 | 完全跟随目标视频 |
| 主要改动区域 | 嘴部及附近 | 眼、鼻、嘴、面颊等内脸区域 |
| 适用场景 | 数字人念新台词 | 客户给视频和照片，只要求脸像 |

结论：**换脸任务不能用 MuseTalk 代替**。若以后客户同时要求“换脸 + 改台词”，应先分别跑换脸与口型实验，再决定顺序；不能在本 MVP 中混做。

---

## 2. 技术路线结论

### 2.1 第一阶段：FaceFusion 3.8.2 本地方案

固定版本：

- 仓库：`https://github.com/facefusion/facefusion`
- Git tag：`3.8.2`
- commit：`4b1dedb853e4838ca7f3cf70b572be241aee2497`
- 运行方式：原生 Conda + ONNX Runtime CUDA 12

选择理由：

- RTX 4070 12 GB 可以本地推理；无需按次调用商业 API。
- 它对目标视频逐帧定位并替换内脸，不重新生成整幅画面，因此身体、证件、衣服和背景不会被生成模型擅自改写。
- 支持 `face_swapper`、`expression_restorer` 和低强度 `face_enhancer` 组合。
- 支持遮挡掩膜、区域掩膜和多张来源图片，适合做稳定工程化基线。

注意：传统换脸通常主要替换内脸，并不可靠地改变发型、耳朵和完整头骨轮廓。若来源人物和目标演员脸宽、下颌或头型差异非常大，即使五官相似，仍可能有“像，但不是完全同一个头”的感觉。应通过选演员、拍摄角度和多参考图降低差异，而不是无限提高融合强度。

### 2.2 第二阶段：DreamID-V 云端高质量适配器

官方仓库：`https://github.com/bytedance/DreamID-V`

DreamID-V 是面向高身份保真的扩散式视频换脸方案，官方建议裁剪后的 `512x512` 来源人脸，并建议高质量结果使用 `1280x720`。它适合作为后续高端档位，但不作为 4070 12 GB 的第一阶段依赖：

- 社区的低显存路径仍以约 16 GB 显存为常见起点；12 GB 本地部署余量不足。
- 扩散式视频生成耗时和显存显著高于 ONNX 换脸。
- 长视频通常要分段，必须解决段间身份漂移、色彩跳变和首尾衔接。
- 即使代码为 Apache-2.0，仍要单独复核权重、Wan 基座和实际商业用途限制。

第二阶段只定义统一接口，待有 4090 云服务器和第一阶段真实样本数据后再开发。不得现在下载相关权重。

### 2.3 不推荐作为主线的方案

- **InsightFace `inswapper_128` 单独脚本**：容易做出演示，但原始分辨率低，许可为 Non-Commercial，不适合作为默认商业交付链路。
- **DeepFaceLab**：需要按人物训练，制作周期长，工程自动化和一张照片的即时任务不匹配。
- **Roop/早期换脸壳**：许多仍依赖 `inswapper_128`，许可和清晰度问题没有消失。
- **整段图生视频/AI 复活类方案**：会重绘头部、衣物、手和证件，不符合“除人脸外尽量不变”。
- **商业 API**：可作为人工兜底，但涉及隐私上传、按量付费、平台水印和数据留存；MVP 不依赖 API。

---

## 3. 拍摄与素材规范

高保真不是只靠模型。下面规则应进入上传页提示和预检。

### 3.1 来源照片

推荐提供 3～5 张同一人的照片：

1. 高清正脸、自然表情；
2. 左转约 30°；
3. 右转约 30°；
4. 自然微笑；
5. 若目标视频明显露齿，再提供一张自然露齿照。

最低要求：

- 图片中恰好一张脸；
- 人脸区域建议至少 `512x512` 像素；
- 无口罩、墨镜、刘海严重遮挡；
- 避免重度美颜、过曝、极端滤镜和截图二次压缩；
- 多张照片必须是同一个人，程序应做相似度一致性检查。

只有一张照片时允许试跑，但预检报告必须标记 `single_reference_risk=true`，提示侧脸、牙齿和大表情保真度下降。

### 3.2 目标视频

- MVP 只接受单人、单主要人脸。
- 建议 1080p 或 720p；脸宽最好大于 300 像素，最低不得低于 180 像素。
- 建议头部偏航角不超过约 ±35°；快速转头、频繁出画、手遮脸会提高失败率。
- 光照稳定，避免强背光、频闪灯和严重运动模糊。
- 说话自然，不必为了模型刻意少张嘴；嘴和牙齿必须在原视频里足够清楚。
- 客户手持证件、画板等资料时可以正常拍摄，因为本流程不重绘身体区域。但不要让证件长时间遮挡脸。

---

## 4. 磁盘、环境和缓存规范

### 4.1 唯一运行时目录

```text
F:\duikouxing-runtime\faceswap\
├─ envs\
│  └─ facefusion-3.8.2\          # 独立 Python/Conda 环境
├─ conda-pkgs\                  # Conda 包缓存
├─ repos\
│  └─ facefusion\              # 固定 tag，模型位于其 .assets\models
├─ cache\
│  ├─ pip\
│  ├─ huggingface\
│  ├─ torch\
│  ├─ xdg\
│  ├─ cuda\
│  └─ pycache\
├─ temp\                        # 解帧、临时视频
├─ jobs\                        # 每个任务的输入副本、清单和中间产物
├─ outputs\                     # 最终结果
├─ samples\                     # 本地验收样本，禁止提交 Git
├─ logs\
└─ licenses\                    # 许可快照与人工批准文件
```

FaceFusion 自动下载的权重默认位于它自己的 `.assets\models`。因为整个 FaceFusion 仓库放在 F 盘，所以模型仍满足“不放 C 盘”的要求。不要私自改 FaceFusion 内部模型相对路径，避免升级时产生隐蔽错误。

### 4.2 每次安装和运行前必须设置的变量

```powershell
$RuntimeRoot = 'F:\duikouxing-runtime\faceswap'

$env:FACE_SWAP_RUNTIME_ROOT = $RuntimeRoot
$env:CONDA_ENVS_PATH         = "$RuntimeRoot\envs"
$env:CONDA_PKGS_DIRS         = "$RuntimeRoot\conda-pkgs"
$env:PIP_CACHE_DIR           = "$RuntimeRoot\cache\pip"
$env:HF_HOME                 = "$RuntimeRoot\cache\huggingface"
$env:HF_HUB_CACHE            = "$RuntimeRoot\cache\huggingface\hub"
$env:TORCH_HOME              = "$RuntimeRoot\cache\torch"
$env:XDG_CACHE_HOME          = "$RuntimeRoot\cache\xdg"
$env:CUDA_CACHE_PATH         = "$RuntimeRoot\cache\cuda"
$env:PYTHONPYCACHEPREFIX     = "$RuntimeRoot\cache\pycache"
$env:TEMP                    = "$RuntimeRoot\temp"
$env:TMP                     = "$RuntimeRoot\temp"

$Directories = @(
  $RuntimeRoot,
  "$RuntimeRoot\envs", "$RuntimeRoot\conda-pkgs", "$RuntimeRoot\repos",
  "$RuntimeRoot\cache\pip", "$RuntimeRoot\cache\huggingface",
  "$RuntimeRoot\cache\torch", "$RuntimeRoot\cache\xdg",
  "$RuntimeRoot\cache\cuda", "$RuntimeRoot\cache\pycache",
  "$RuntimeRoot\temp", "$RuntimeRoot\jobs", "$RuntimeRoot\outputs",
  "$RuntimeRoot\samples", "$RuntimeRoot\logs", "$RuntimeRoot\licenses"
)
$Directories | ForEach-Object { New-Item -ItemType Directory -Force -Path $_ | Out-Null }
```

### 4.3 C 盘零落盘预检

“大文件不放 C 盘”不能只靠文档约定，代码必须阻止错误配置：

```python
from __future__ import annotations

from pathlib import Path


class UnsafeRuntimePath(ValueError):
    pass


def require_under_runtime(path: Path, runtime_root: Path) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    root = runtime_root.expanduser().resolve(strict=False)
    if resolved.drive.casefold() == "c:":
        raise UnsafeRuntimePath(f"运行时路径禁止位于 C 盘: {resolved}")
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise UnsafeRuntimePath(
            f"运行时路径必须位于 {root} 之下，当前为 {resolved}"
        ) from exc
    return resolved
```

以下路径都必须调用该函数：环境目录、FaceFusion 仓库、模型目录、缓存、临时目录、任务目录、输出目录和日志目录。用户提供的原始输入可以来自其他盘，但进入处理前复制到对应的 F 盘任务目录；源代码目录 `G:\duikouxing` 不受此限制。

---

## 5. Git 分支与 worktree

当前 `G:\duikouxing` 工作区有未提交的 InfinityTalk 等实验内容，不直接切分支。以已验证的 `codex/latentsync-1.6-cloud` 为基线新建独立 worktree：

```powershell
git -C G:\duikouxing worktree add `
  -b codex/faceswap-facefusion-local `
  G:\duikouxing-faceswap `
  codex/latentsync-1.6-cloud
```

若分支已存在：

```powershell
git -C G:\duikouxing worktree add `
  G:\duikouxing-faceswap `
  codex/faceswap-facefusion-local
```

后续所有换脸代码改动在 `G:\duikouxing-faceswap` 中完成。不得运行 `git reset --hard`、`git clean` 或覆盖用户现有文件。

---

## 6. 安装方案

### 6.1 前提

- NVIDIA 驱动正常，`nvidia-smi` 能看到 RTX 4070 12 GB。
- Git 和 FFmpeg 可用。
- Conda/Miniforge 本体也建议安装到 F 盘，例如 `F:\duikouxing-runtime\miniforge3`。
- 不复用项目主环境，FaceFusion 使用独立前缀环境。

### 6.2 固定源码版本

```powershell
$RuntimeRoot = 'F:\duikouxing-runtime\faceswap'
$FaceFusionRepo = "$RuntimeRoot\repos\facefusion"
$FaceFusionEnv = "$RuntimeRoot\envs\facefusion-3.8.2"

git clone https://github.com/facefusion/facefusion.git $FaceFusionRepo
git -C $FaceFusionRepo checkout --detach 3.8.2

$ActualCommit = git -C $FaceFusionRepo rev-parse HEAD
if ($ActualCommit -ne '4b1dedb853e4838ca7f3cf70b572be241aee2497') {
    throw "FaceFusion commit 不匹配: $ActualCommit"
}
```

### 6.3 创建环境并安装 CUDA 12 运行时

```powershell
conda create --prefix $FaceFusionEnv -y python=3.12 pip
conda run --prefix $FaceFusionEnv python `
  "$FaceFusionRepo\install.py" cuda@12
```

FaceFusion 3.8.2 的安装器中，`cuda@12` 对应固定的 `onnxruntime-gpu` CUDA 12 版本。不要把安装选项和运行选项混淆：安装时用 `cuda@12`，运行 CLI 时使用 `--execution-providers cuda`。

### 6.4 安装后的 GPU 医生检查

```powershell
conda run --prefix $FaceFusionEnv python -c `
  "import onnxruntime as o; print(o.__version__); print(o.get_available_providers())"
```

必须包含 `CUDAExecutionProvider`。如果只有 CPU，不允许静默继续跑正式任务。

然后执行一次模型下载/短片预热。预热前再次确认 FaceFusion 仓库、`TEMP`、`TMP` 和缓存变量均在 F 盘。下载失败应保留日志，不得自动回退到来历不明的网盘权重。

---

## 7. 项目代码结构

GLM-5 应新增而不是重写现有口型流水线：

```text
src/digital_human/
├─ face_swap_config.py
├─ face_swap_preflight.py
├─ face_swap_pipeline.py
├─ face_swap_quality.py
└─ adapters/
   ├─ __init__.py
   ├─ face_swap.py               # 协议/抽象接口
   ├─ facefusion.py              # 第一阶段实现
   └─ dreamid_v.py               # 仅接口占位，不下载模型

config/
├─ face-swap.local.home.yaml
└─ face-swap.job.example.yaml

scripts/
├─ setup_faceswap_home.ps1
├─ faceswap_doctor.ps1
└─ run_faceswap.ps1

tests/
├─ test_face_swap_config.py
├─ test_face_swap_paths.py
├─ test_face_swap_license_gate.py
├─ test_facefusion_adapter.py
└─ test_face_swap_quality.py
```

建议 CLI：

```text
digital-human face-swap-doctor --config ...
digital-human face-swap-preview --job ... --seconds 8
digital-human face-swap-run --job ...
digital-human face-swap-quality --job-dir ...
```

其中 `preview` 先选取包含张嘴、闭嘴、转头和眨眼的 8～15 秒片段。客户确认后才跑整段，节省返工时间。

---

## 8. 配置文件设计

### 8.1 本机配置 `config/face-swap.local.home.yaml`

```yaml
version: 1

runtime:
  root: 'F:\duikouxing-runtime\faceswap'
  facefusion_repo: 'F:\duikouxing-runtime\faceswap\repos\facefusion'
  facefusion_python: 'F:\duikouxing-runtime\faceswap\envs\facefusion-3.8.2\python.exe'
  temp_dir: 'F:\duikouxing-runtime\faceswap\temp'
  jobs_dir: 'F:\duikouxing-runtime\faceswap\jobs'
  outputs_dir: 'F:\duikouxing-runtime\faceswap\outputs'
  logs_dir: 'F:\duikouxing-runtime\faceswap\logs'

engine:
  name: facefusion
  version: '3.8.2'
  commit: '4b1dedb853e4838ca7f3cf70b572be241aee2497'
  execution_providers: [cuda]
  execution_thread_count: 4

policy:
  require_consent: true
  label_ai_generated: true
  commercial_model_allowlist: []
  license_override_dir: 'F:\duikouxing-runtime\faceswap\licenses'
  reject_c_drive_runtime: true
```

### 8.2 任务配置 `config/face-swap.job.example.yaml`

```yaml
version: 1
job_id: 'fs-20260826-001'
usage: research                 # research | commercial

consent:
  source_identity_confirmed: true
  target_performer_confirmed: true
  intended_use: '客户授权的宣传视频'

input:
  source_images:
    - 'F:\duikouxing-runtime\faceswap\jobs\fs-20260826-001\input\front.jpg'
    - 'F:\duikouxing-runtime\faceswap\jobs\fs-20260826-001\input\left30.jpg'
    - 'F:\duikouxing-runtime\faceswap\jobs\fs-20260826-001\input\right30.jpg'
  target_video: 'F:\duikouxing-runtime\faceswap\jobs\fs-20260826-001\input\target.mp4'

output:
  video: 'F:\duikouxing-runtime\faceswap\outputs\fs-20260826-001.mp4'
  manifest: 'F:\duikouxing-runtime\faceswap\outputs\fs-20260826-001.manifest.json'

profile:
  name: ghost_balanced
  face_swapper_model: ghost_2_256
  pixel_boost: '512x512'
  swapper_weight: 0.85
  expression_factor: 80
  expression_areas: [upper-face, lower-face]
  mask_types: [box, occlusion, region]
  mask_regions:
    - skin
    - left-eyebrow
    - right-eyebrow
    - left-eye
    - right-eye
    - nose
    - mouth
    - upper-lip
    - lower-lip
  mask_blur: 0.30
  enhancer_enabled: true
  enhancer_model: gfpgan_1.4
  enhancer_blend: 25
  enhancer_weight: 0.50
  output_video_encoder: libx264
  output_video_quality: 95
  output_video_preset: slow

quality:
  min_face_detection_coverage: 0.99
  min_mouth_sharpness_ratio: 0.75
  min_mouth_motion_correlation: 0.90
  require_manual_review: true
```

初始参数只是 A/B 起点，不是所有人的永久最优值。尤其 `swapper_weight` 在 FaceFusion 中不是简单的“不透明度”，必须通过真实样本验证。

---

## 9. 模型配置与许可闸门

### 9.1 第一轮 A/B 模型

| 模型 | 用途 | FaceFusion 3.8.2 元数据许可 | 商业任务默认策略 |
|---|---|---|---|
| `ghost_2_256` | 本地默认，身份与速度平衡 | Apache-2.0 | 仍需完成全依赖许可审计后才能加入 allowlist |
| `hyperswap_1c_256` | 研究对比，常用于观察身份相似度 | ResearchRAIL | 阻止 |
| `inswapper_128_fp16` | 兼容性/基线对比 | Non-Commercial | 阻止 |

FaceFusion 主项目许可证和检测器、特征提取器、转换器、增强器等下游模型也必须审计。**单个换脸权重标注 Apache-2.0，不等于整条商业链路自动合规。** 在法务/人工审查完成前，商业模式必须 fail closed。

### 9.2 许可闸门核心代码

```python
from dataclasses import dataclass
from pathlib import Path


MODEL_LICENSES = {
    "ghost_2_256": "Apache-2.0",
    "hyperswap_1c_256": "ResearchRAIL",
    "inswapper_128_fp16": "Non-Commercial",
}

BLOCKED_FOR_COMMERCIAL = {"ResearchRAIL", "Non-Commercial", "Unknown"}


@dataclass(frozen=True)
class LicenseDecision:
    model: str
    declared_license: str
    allowed: bool
    reason: str


def authorize_model(
    model: str,
    usage: str,
    commercial_allowlist: set[str],
    approval_file: Path | None = None,
) -> LicenseDecision:
    declared = MODEL_LICENSES.get(model, "Unknown")
    if usage not in {"research", "commercial"}:
        raise ValueError(f"未知用途: {usage}")

    if usage == "research":
        return LicenseDecision(model, declared, True, "研究/内部测试")

    approved = (
        model in commercial_allowlist
        and approval_file is not None
        and approval_file.is_file()
    )
    if declared in BLOCKED_FOR_COMMERCIAL or not approved:
        return LicenseDecision(
            model,
            declared,
            False,
            "商业任务缺少已审核许可快照和显式 allowlist",
        )
    return LicenseDecision(model, declared, True, "商业许可已人工审核")
```

批准文件至少记录：模型名、权重 SHA-256、来源 URL、许可证全文快照、审查日期、审查人、适用业务和到期/复核日期。程序不得允许 `--force` 跳过。

---

## 10. FaceFusion 适配器核心代码

以下代码是 GLM-5 实现时应遵循的主体结构。可根据项目现有异常类和日志工具调整命名，但不得改变安全边界。

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import subprocess


@dataclass(frozen=True)
class FaceFusionRuntime:
    python: Path
    repo: Path
    temp_dir: Path
    jobs_dir: Path
    runtime_root: Path
    execution_providers: tuple[str, ...] = ("cuda",)


@dataclass(frozen=True)
class FaceSwapProfile:
    model: str = "ghost_2_256"
    pixel_boost: str = "512x512"
    swapper_weight: float = 0.85
    expression_factor: int = 80
    expression_areas: tuple[str, ...] = ("upper-face", "lower-face")
    mask_types: tuple[str, ...] = ("box", "occlusion", "region")
    mask_regions: tuple[str, ...] = (
        "skin", "left-eyebrow", "right-eyebrow", "left-eye",
        "right-eye", "nose", "mouth", "upper-lip", "lower-lip",
    )
    mask_blur: float = 0.30
    enhancer_enabled: bool = True
    enhancer_model: str = "gfpgan_1.4"
    enhancer_blend: int = 25
    enhancer_weight: float = 0.50


class FaceFusionAdapter:
    EXPECTED_COMMIT = "4b1dedb853e4838ca7f3cf70b572be241aee2497"

    def __init__(self, runtime: FaceFusionRuntime) -> None:
        self.runtime = runtime

    def doctor(self) -> None:
        require_under_runtime(self.runtime.python, self.runtime.runtime_root)
        require_under_runtime(self.runtime.repo, self.runtime.runtime_root)
        require_under_runtime(self.runtime.temp_dir, self.runtime.runtime_root)
        require_under_runtime(self.runtime.jobs_dir, self.runtime.runtime_root)

        entry = self.runtime.repo / "facefusion.py"
        if not self.runtime.python.is_file():
            raise FileNotFoundError(self.runtime.python)
        if not entry.is_file():
            raise FileNotFoundError(entry)

        commit = subprocess.run(
            ["git", "-C", str(self.runtime.repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if commit != self.EXPECTED_COMMIT:
            raise RuntimeError(f"FaceFusion 版本漂移: {commit}")

        providers = subprocess.run(
            [
                str(self.runtime.python),
                "-c",
                "import onnxruntime as o; print('\\n'.join(o.get_available_providers()))",
            ],
            check=True,
            capture_output=True,
            text=True,
            cwd=self.runtime.repo,
            env=self._environment(),
        ).stdout
        if "CUDAExecutionProvider" not in providers:
            raise RuntimeError("未检测到 CUDAExecutionProvider，禁止正式运行")

    def build_command(
        self,
        source_images: list[Path],
        target_video: Path,
        output_video: Path,
        profile: FaceSwapProfile,
    ) -> list[str]:
        if not source_images:
            raise ValueError("至少需要一张来源人脸图片")
        for path in [*source_images, target_video]:
            if not path.is_file():
                raise FileNotFoundError(path)

        require_under_runtime(output_video, self.runtime.runtime_root)
        output_video.parent.mkdir(parents=True, exist_ok=True)

        processors = ["face_swapper", "expression_restorer"]
        if profile.enhancer_enabled:
            processors.append("face_enhancer")

        command = [
            str(self.runtime.python),
            str(self.runtime.repo / "facefusion.py"),
            "headless-run",
            "--source-paths", *map(str, source_images),
            "--target-path", str(target_video),
            "--output-path", str(output_video),
            "--temp-path", str(self.runtime.temp_dir),
            "--jobs-path", str(self.runtime.jobs_dir),
            "--processors", *processors,
            "--face-selector-mode", "one",
            "--face-swapper-model", profile.model,
            "--face-swapper-pixel-boost", profile.pixel_boost,
            "--face-swapper-weight", str(profile.swapper_weight),
            "--expression-restorer-model", "live_portrait",
            "--expression-restorer-factor", str(profile.expression_factor),
            "--expression-restorer-areas", *profile.expression_areas,
            "--face-mask-types", *profile.mask_types,
            "--face-occluder-model", "xseg_2",
            "--face-parser-model", "bisenet_resnet_34",
            "--face-mask-regions", *profile.mask_regions,
            "--face-mask-blur", str(profile.mask_blur),
            "--execution-providers", *self.runtime.execution_providers,
            "--output-video-encoder", "libx264",
            "--output-video-quality", "95",
            "--output-video-preset", "slow",
        ]
        if profile.enhancer_enabled:
            command.extend([
                "--face-enhancer-model", profile.enhancer_model,
                "--face-enhancer-blend", str(profile.enhancer_blend),
                "--face-enhancer-weight", str(profile.enhancer_weight),
            ])
        return command

    def run(
        self,
        source_images: list[Path],
        target_video: Path,
        output_video: Path,
        profile: FaceSwapProfile,
    ) -> Path:
        self.doctor()
        command = self.build_command(
            source_images, target_video, output_video, profile
        )
        subprocess.run(
            command,
            check=True,
            cwd=self.runtime.repo,
            env=self._environment(),
        )
        if not output_video.is_file() or output_video.stat().st_size == 0:
            raise RuntimeError("FaceFusion 未产生有效输出")
        return output_video

    def _environment(self) -> dict[str, str]:
        root = self.runtime.runtime_root
        env = os.environ.copy()
        env.update({
            "FACE_SWAP_RUNTIME_ROOT": str(root),
            "TEMP": str(root / "temp"),
            "TMP": str(root / "temp"),
            "PIP_CACHE_DIR": str(root / "cache" / "pip"),
            "HF_HOME": str(root / "cache" / "huggingface"),
            "HF_HUB_CACHE": str(root / "cache" / "huggingface" / "hub"),
            "TORCH_HOME": str(root / "cache" / "torch"),
            "XDG_CACHE_HOME": str(root / "cache" / "xdg"),
            "CUDA_CACHE_PATH": str(root / "cache" / "cuda"),
            "PYTHONPYCACHEPREFIX": str(root / "cache" / "pycache"),
        })
        return env
```

实现注意：

- `--face-selector-mode one` 只适用于单主要人脸。预检发现多人时直接拒绝，不允许“猜一个”。
- 多张来源图交给 FaceFusion 聚合身份信息；预检先确认它们是同一人。
- `expression_restorer` 的目标是恢复目标视频原有表情，尤其嘴部运动，而不是生成新口型。
- 增强器必须低强度起步。强增强虽然单帧锐利，但容易造成牙齿形状每帧变化、皮肤塑料感和闪烁。
- 输出只编码一次。禁止输出后再无必要地解码、贴脸和二次高压缩。

---

## 11. 流水线顺序

```text
授权检查
  → 复制输入到 F 盘任务目录并计算 SHA-256
  → 来源照片预检（单脸、清晰度、同一身份）
  → 目标视频预检（单脸、覆盖率、脸尺寸、角度、音视频信息）
  → 生成 8～15 秒代表性预览片段
  → FaceFusion A/B（ghost 参数组至少两套）
  → 自动质量报告 + 人工查看嘴/牙/下颌/转头
  → 选定参数跑全片
  → 自动质量验收
  → 生成结果视频、对比图、manifest 和 AI 标识信息
```

不要把原视频嘴部再贴回输出。若嘴部不自然，按以下顺序调参：

1. 先确认目标原视频嘴部是否清晰；
2. 提高或降低 `expression_restorer_factor`，A/B 60、80、100；
3. 检查 `expression_restorer_areas` 是否包含 `lower-face`；
4. 降低增强器 blend，甚至关闭增强器作对照；
5. A/B `pixel_boost` 为 `256x256` 和 `512x512`；
6. 再调整 swapper weight；
7. 若脸型差异过大，更换目标演员或补多角度来源照片。

---

## 12. 自动质量检测

自动指标用于拦截明显坏片，不能替代人工确认。

### 12.1 必须保持的媒体属性

通过 `ffprobe` 比较目标和输出：

- 宽高相同；
- 帧率相同；
- 帧数差不超过 1 帧；
- 时长误差不超过 50 ms 或一帧时长的较大者；
- 输出存在音轨；
- 音频时长和目标一致；
- 不允许意外旋转、镜像或色彩范围严重变化。

### 12.2 人脸覆盖率

对输出抽帧或全帧检测：

```text
face_detection_coverage = 检测到主脸的帧数 / 总帧数
```

建议阈值 `>= 0.99`。连续丢脸超过 2 帧直接失败；快速出画的输入应在预检阶段标记例外区间，而不是事后放宽全局阈值。

### 12.3 嘴部清晰度

在相同时间戳，使用目标视频的嘴部关键点构造 ROI，比较拉普拉斯方差：

```python
import cv2
import numpy as np


def masked_laplacian_variance(gray: np.ndarray, mask: np.ndarray) -> float:
    lap = cv2.Laplacian(gray, cv2.CV_32F)
    values = lap[mask > 0]
    return float(values.var()) if values.size else 0.0


def mouth_sharpness_ratio(
    target_bgr: np.ndarray,
    output_bgr: np.ndarray,
    mouth_mask: np.ndarray,
) -> float:
    target_gray = cv2.cvtColor(target_bgr, cv2.COLOR_BGR2GRAY)
    output_gray = cv2.cvtColor(output_bgr, cv2.COLOR_BGR2GRAY)
    target_score = masked_laplacian_variance(target_gray, mouth_mask)
    output_score = masked_laplacian_variance(output_gray, mouth_mask)
    return output_score / max(target_score, 1e-6)
```

先以抽样帧中位数 `>= 0.75` 为预警阈值。该指标会受纹理和身份差异影响，所以只能判定“明显变糊”，不能当作最终审美分数。

### 12.4 嘴部运动一致性

使用独立的人脸关键点检测器，计算每帧嘴部开合比 `MAR`，再比较目标和输出序列的相关系数。建议：

```text
corr(MAR_target, MAR_output) >= 0.90
```

同时检查峰值时间偏移不得超过 1 帧。这里不是音频口型评分，而是确认换脸没有改变目标演员原来的开闭嘴时序。

### 12.5 身份相似度

- 用独立于换脸模型的身份特征模型比较来源照片和输出抽帧。
- 不要用同一个特征模型既生成又评分，否则分数可能虚高。
- 指标先用于同一批 A/B 排序，不在没有内部标定数据时写死一个“行业通用”绝对阈值。
- 正脸、左右转头、张嘴和闭嘴分别统计，不要只看最佳正脸帧。

### 12.6 非人脸区域保护

FaceFusion 不生成整幅画面，但视频编码会使全帧像素发生轻微变化，因此不能要求脸外像素逐字节相同。应：

- 使用扩张后的人脸掩膜排除编辑区；
- 对脸外区域计算结构相似度和差分热图；
- 检查证件、姓名、号码、画板文字等人工指定 ROI；
- 发现脸外结构变化时失败，不通过“再贴一遍原图”掩盖问题。

### 12.7 人工必看帧

报告输出接触表或对比页面，至少包含：

- 第一帧、中间帧、最后一帧；
- 最大张嘴、闭嘴、露齿、眨眼；
- 左右最大转头；
- 手或物体最接近脸的帧；
- 自动指标最差的 10 帧。

人工重点检查嘴唇边缘、牙齿是否闪变、鼻翼、下颌线、发际线、眼镜、耳朵、遮挡恢复和连续播放时的身份漂移。

---

## 13. 参数 A/B 计划

第一轮不要一次改变很多参数。对同一 10 秒预览做以下最小矩阵：

| 组 | 模型 | pixel boost | expression factor | enhancer blend | 目的 |
|---|---|---:|---:|---:|---|
| A | ghost_2_256 | 256x256 | 80 | 0（关闭） | 观察原始换脸稳定性 |
| B | ghost_2_256 | 512x512 | 80 | 25 | 默认候选 |
| C | ghost_2_256 | 512x512 | 100 | 15 | 最大限度保留原表情 |
| D | hyperswap_1c_256 | 512x512 | 80 | 15 | 仅研究身份相似度对比 |
| E | inswapper_128_fp16 | 512x512 | 80 | 25 | 仅非商业基线 |

先从 A/B/C 中选择；D/E 只进入内部研究报告，不可混入商业输出。若 B 比 A 更锐但牙齿闪烁，则优先降低增强器，不要直接叠加第二个修复模型。

---

## 14. 任务清单与隐私

每个任务目录：

```text
jobs\<job_id>\
├─ input\
├─ preview\
├─ working\
├─ quality\
├─ logs\
├─ consent.json
└─ manifest.json
```

`manifest.json` 至少记录：

- 任务 ID 和创建时间；
- 所有输入文件 SHA-256；
- 引擎 tag、commit 和模型权重 SHA-256；
- 完整参数，但不记录无关的用户隐私；
- GPU、驱动、ONNX Runtime、FFmpeg 版本；
- 许可决策；
- 自动质量指标和人工审核状态；
- 输出 SHA-256；
- `ai_generated=true`。

日志不得输出身份证号码、电话号码等可读正文；文件名要规范化。原始素材和结果不进入 Git。增加 `.gitignore` 覆盖任务、样本、权重、缓存、临时帧和日志。

建议支持任务到期清理，但删除必须由用户或明确保留策略触发，并记录删除清单；开发阶段不得自动清理用户原始素材。

---

## 15. 测试要求

### 15.1 单元测试

1. 所有运行时路径在 F 根目录下时通过。
2. 任一运行时路径落在 C 盘或逃逸根目录时失败。
3. 未授权任务失败。
4. commercial + Non-Commercial/ResearchRAIL/Unknown 模型失败。
5. commercial + 未在 allowlist 或无批准文件时失败。
6. FaceFusion commit 不一致时 doctor 失败。
7. CUDA provider 缺失时 doctor 失败。
8. 命令数组包含固定的输入、输出、temp、jobs、processor 和模型参数。
9. 文件名含空格、中文、`&`、括号时仍作为单个参数，不被 shell 解释。
10. 输出不存在或为空时任务失败。

### 15.2 集成测试

- 5 秒 720p 单人测试片能在 4070 上完成。
- 输出有音频，时长、FPS、分辨率符合要求。
- GPU 实际被使用，运行日志不能显示纯 CPU 回退。
- 断点失败后保留任务状态和日志，可安全重试。
- 测试前后扫描 `C:\Users\<用户>\.cache`、默认 temp、默认 Conda 缓存的体积变化；若出现本任务新增的大文件，验收失败。

### 15.3 真实样本验收集

至少准备 6 类经授权样本：

1. 正脸平稳说话；
2. 明显张嘴和露齿；
3. 左右轻转头；
4. 眼镜；
5. 手靠近脸但不长时间遮挡；
6. 手持带文字/号码的证件或画板。

每类保留参数、指标和人工评分。没有这套回归集，不允许仅凭一个成功视频宣布项目完成。

---

## 16. 分阶段开发清单

### 阶段 A：基础设施

- [ ] 新分支和独立 worktree。
- [ ] `setup_faceswap_home.ps1` 创建 F 盘统一目录并设置变量。
- [ ] 安装固定 FaceFusion 3.8.2 和 CUDA 12 ONNX Runtime。
- [ ] doctor 检查 commit、GPU、FFmpeg、目录和磁盘余量。
- [ ] C 盘零落盘测试。

### 阶段 B：MVP 适配器

- [ ] 独立 face-swap 配置模型。
- [ ] 授权和许可闸门。
- [ ] 来源图/目标视频预检。
- [ ] FaceFusion 命令构建和安全执行。
- [ ] 预览、正式运行、状态和 manifest。
- [ ] 不修改现有 MuseTalk/LatentSync/InfinityTalk 默认行为。

### 阶段 C：质量系统

- [ ] 媒体属性比对。
- [ ] 人脸覆盖率。
- [ ] 嘴部清晰度与运动一致性。
- [ ] 脸外区域和敏感文字 ROI 对比。
- [ ] 对比接触表与人工审核状态。
- [ ] A/B 报告。

### 阶段 D：稳定与交付

- [ ] 6 类真实授权样本回归。
- [ ] 异常恢复和任务重试。
- [ ] 隐私日志审计。
- [ ] 安装文档、操作文档和卸载/迁移文档。
- [ ] 模型和依赖许可审计完成前，商业模式保持关闭。

### 阶段 E：可选 DreamID-V 云端档位

- [ ] 只有在第一阶段样本证明 FaceFusion 身份保真不足时启动。
- [ ] 使用 4090 级云 GPU，独立环境和权重目录。
- [ ] 实现与 `FaceSwapAdapter` 相同的输入输出接口。
- [ ] 解决长视频分段、重叠窗口、身份漂移和颜色连续性。
- [ ] 独立完成权重、基座和商业许可审计。
- [ ] 与 FaceFusion 盲测后再决定是否成为付费高质量档位。

---

## 17. 完成定义

只有同时满足以下条件才算第一阶段完成：

1. 在指定 Windows 11 + RTX 4070 12 GB 机器上可从零安装并运行。
2. 环境、权重、缓存、temp、jobs、日志和输出均在 `F:\duikouxing-runtime\faceswap`，C 盘没有本任务产生的大文件。
3. 目标视频中的身体、手、衣服、背景、证件和文字无结构性变化。
4. 输出身份明显接近来源人物，口型和表情跟随目标视频。
5. 连续播放时嘴部不糊、无双嘴、无明显漂移、牙齿不持续闪变。
6. 分辨率、FPS、时长和音频符合验收标准。
7. 自动质量报告和人工审核完整。
8. 商业任务没有绕过授权及许可闸门。
9. 单元测试、集成测试和 6 类真实样本回归通过。
10. 未破坏项目现有口型、语音和云端流水线。

---

## 18. 官方核准来源

- FaceFusion 仓库与 3.8.2 源码：`https://github.com/facefusion/facefusion/tree/3.8.2`
- FaceFusion 3.8.2 release：`https://github.com/facefusion/facefusion/releases/tag/3.8.2`
- FaceFusion 路径参数：`https://docs.facefusion.io/usage/cli-arguments/paths`
- Face swapper 参数：`https://docs.facefusion.io/usage/cli-arguments/processors/face-swapper`
- Expression restorer 参数：`https://docs.facefusion.io/usage/cli-arguments/processors/expression-restorer`
- Face masker 参数：`https://docs.facefusion.io/usage/cli-arguments/face-masker`
- DreamID-V 官方仓库：`https://github.com/bytedance/DreamID-V`

实现时必须以固定 tag 中的源码参数为准，不以博客、短视频教程或第三方整合包为准。若将来升级 FaceFusion，先新增兼容测试和许可复核，不得把 `main` 分支直接覆盖到生产环境。
