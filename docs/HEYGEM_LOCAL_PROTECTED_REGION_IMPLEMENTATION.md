# HeyGem 本地口型引擎与证件保护区实施规格

状态：待 GLM5 实施  
目标机器：Windows 11、NVIDIA RTX 4070 12GB、内存 32GB  
代码仓库：`G:\duikouxing`  
统一运行时目录：`F:\duikouxing-runtime`

> 本文是开发任务书，不是概念讨论。实现时以本文的约束、接口、验收标准和失败策略为准。
> 不得覆盖仓库中与本任务无关的未提交修改；开始编码前先检查 `git status`。

## 1. 最终结论与技术路线

本项目新增 `heygem_local` 口型引擎，使用 HeyGem/Duix.Avatar 的本地 Docker
视频合成服务。继续使用现有 `dots.tts` 生成克隆声音，不启用 HeyGem 自带的 TTS、ASR
服务，因此只部署 `duix-avatar-gen-video` Lite 容器。

禁止采用以下路线：

- 禁止从 InfiniteTalk 或 HeyGem 的结果中裁剪一个小嘴，再贴回原脸。
- 禁止让生成式模型重绘证件、身份证、证书、画板、手、衣服或背景。
- 禁止在输入输出帧无法一一对应时强行做保护区合成。
- 禁止为了“看起来跑通”而静默缩放、丢帧、补帧或截断视频。

采用以下路线：

```text
客户授权视频
  -> 25fps 标准化
  -> 按目标音频长度生成 base_duration_matched.mp4
  -> dots.tts 生成 target_normalized.wav
  -> HeyGem 本地服务生成 heygem_result.mp4
  -> 校验 HeyGem 与 base 的尺寸、FPS、帧数和时长
  -> 以 HeyGem 完整帧为画面，不做嘴部二次贴片
  -> 从 base 同帧恢复证件/画板等 protected_regions
  -> FFV1 无损中间视频
  -> 一次 H.264 高质量编码并封装目标声音
  -> 保护区差异检测 + 人工看片
```

该路线解决两个不同问题：

1. HeyGem 自身负责脸部和口型的时序一致性，避免“小嘴贴回”造成双嘴、尺寸不匹配和漂移。
2. 证件等区域不依赖模型自觉保护，而是从与 HeyGem 输入完全相同的基准视频逐帧恢复。

## 2. 业务边界

### 2.1 支持的素材

- 单人、固定镜头或仅轻微机位抖动。
- 正脸或轻微侧脸，脸部持续清晰可见。
- 人物身体动作较小。
- 证件位于胸前并明显低于下巴。
- 手、证件、画板不得经过或遮挡嘴部、下巴和脸颊。
- 证件尽量保持固定；首版只支持固定矩形或固定多边形保护区。
- 原视频最好不短于目标音频；短于目标音频时允许现有 `pingpong`，但必须人工检查循环点。

### 2.2 首版不支持

- 多人视频、多人同时说话。
- 手或证件遮嘴。
- 大幅转头、低头、跳舞、快速走动。
- 证件在画面中大范围移动、旋转、严重透视变化。
- 将真实身份证视频用于身份核验、开户、贷款、签约或冒充本人。
- 未取得肖像、声音以及证件处理授权的任务。

不满足条件时必须拒绝任务或转人工，不得用生成结果冒险交付。

## 3. 存储布局：C 盘零大文件

源代码继续保留在 `G:\duikouxing`。所有环境、权重、缓存、Docker 数据、任务中间文件和
输出统一放在：

```text
F:\duikouxing-runtime\
├── envs\
│   ├── digital-human\
│   ├── dots-tts\
│   ├── musetalk\
│   └── latentsync\
├── conda-pkgs\
├── repos\
│   ├── MuseTalk\
│   └── LatentSync\
├── models\
│   ├── dots.tts-soar\
│   ├── dots.tts-mf\
│   ├── musetalk\
│   └── latentsync\
├── cache\
│   ├── huggingface\
│   ├── torch\
│   ├── pip\
│   ├── modelscope\
│   └── cuda\
├── temp\
├── jobs-home\
├── heygem\
│   └── data\
│       └── face2face\
│           └── temp\
└── docker-desktop\
```

强制要求：

- 不在 `C:\Users\...\.cache`、`AppData` 或系统临时目录下载模型。
- 不在项目目录的 `.conda-envs`、`models`、`.cache` 中继续新增大文件。
- Docker Desktop 的磁盘镜像位置必须在设置中迁移到
  `F:\duikouxing-runtime\docker-desktop`；仅修改 Compose 卷映射并不能阻止 Docker
  镜像写入 C 盘。
- Docker 数据迁移属于用户机器级操作，安装脚本只能检查和提示，不得自动删除或迁移已有
  Docker/WSL 数据。
- 不得创建指向 C 盘缓存目录的符号链接或目录联接。

启动任何下载或推理进程前统一设置：

```powershell
$RuntimeRoot = 'F:\duikouxing-runtime'
$env:DIGITAL_HUMAN_RUNTIME_ROOT = $RuntimeRoot
$env:CONDA_ENVS_PATH = "$RuntimeRoot\envs"
$env:CONDA_PKGS_DIRS = "$RuntimeRoot\conda-pkgs"
$env:HF_HOME = "$RuntimeRoot\cache\huggingface"
$env:HF_HUB_CACHE = "$RuntimeRoot\cache\huggingface\hub"
$env:TORCH_HOME = "$RuntimeRoot\cache\torch"
$env:PIP_CACHE_DIR = "$RuntimeRoot\cache\pip"
$env:MODELSCOPE_CACHE = "$RuntimeRoot\cache\modelscope"
$env:XDG_CACHE_HOME = "$RuntimeRoot\cache"
$env:CUDA_CACHE_PATH = "$RuntimeRoot\cache\cuda"
$env:TEMP = "$RuntimeRoot\temp"
$env:TMP = "$RuntimeRoot\temp"
```

这些变量必须由统一的 PowerShell 启动脚本设置，不能要求用户每次手工输入。

## 4. HeyGem 部署方式

新增：

```text
deploy/heygem/docker-compose.yml
scripts/setup_heygem_home.ps1
scripts/start_heygem_home.ps1
scripts/stop_heygem_home.ps1
```

Compose 只启动视频服务：

```yaml
name: duikouxing-heygem

services:
  heygem-gen-video:
    image: guiji2025/duix.avatar
    container_name: duikouxing-heygem-gen-video
    restart: unless-stopped
    runtime: nvidia
    privileged: true
    environment:
      NVIDIA_VISIBLE_DEVICES: "0"
      NVIDIA_DRIVER_CAPABILITIES: "compute,graphics,utility,video,display"
      PYTORCH_CUDA_ALLOC_CONF: "max_split_size_mb:512"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    shm_size: "8gb"
    ports:
      - "127.0.0.1:8383:8383"
    volumes:
      - "F:/duikouxing-runtime/heygem/data/face2face:/code/data"
    command: python /code/app_local.py
```

注意：

- 端口只绑定 `127.0.0.1`，不得暴露到局域网。
- 首次验证通过后，记录镜像 ID 和 digest；生产环境改为固定 digest，禁止长期漂移使用
  不固定的 `latest`。
- 项目不启动官方 Electron 客户端，因此避开其 Windows `D:` 路径硬编码。
- 项目已有 dots.tts，不部署 `fish-speech-ziming` 和 `fun-asr` 两个容器。
- 服务仅接受位于共享目录内的媒体文件，不能向 API 传任意本机路径。

健康检查至少验证：

1. `docker inspect` 显示容器为 running。
2. 容器能看到 NVIDIA GPU。
3. `127.0.0.1:8383` 可连接。
4. 发送不存在的测试任务码到 `/easy/query` 能返回 JSON，而不是连接错误。

## 5. 配置改造

### 5.1 LocalConfig

现有 `config.py` 把环境、模型和任务目录强制限制在项目根目录，这是迁移到 F 盘的阻塞点。
修改为显式的 `storage_root`，所有运行时路径必须位于该根目录；代码仓库路径允许位于
`G:\duikouxing`。

新增字段：

```python
@dataclass(frozen=True)
class LocalConfig:
    # 保留现有字段
    storage_root: Path
    heygem_base_url: str
    heygem_shared_root: Path
    heygem_timeout_seconds: int
    heygem_poll_interval_seconds: float
```

`config/local.home.yaml` 目标形态：

```yaml
profile: "home"

storage:
  root: "F:/duikouxing-runtime"
  cache_root: "F:/duikouxing-runtime/cache"
  temp_root: "F:/duikouxing-runtime/temp"

environments:
  orchestrator_prefix: "F:/duikouxing-runtime/envs/digital-human"
  dots_tts_prefix: "F:/duikouxing-runtime/envs/dots-tts"
  musetalk_prefix: "F:/duikouxing-runtime/envs/musetalk"
  latentsync_prefix: "F:/duikouxing-runtime/envs/latentsync"

models:
  dots_quality: "F:/duikouxing-runtime/models/dots.tts-soar"
  dots_fast: "F:/duikouxing-runtime/models/dots.tts-mf"
  latentsync_1_6: "F:/duikouxing-runtime/models/latentsync/latentsync_unet.pt"

paths:
  musetalk_repo: "F:/duikouxing-runtime/repos/MuseTalk"
  latentsync_repo: "F:/duikouxing-runtime/repos/LatentSync"
  jobs_root: "F:/duikouxing-runtime/jobs-home"

runtime:
  expected_gpu: "RTX 4070"
  gpu_id: 0
  musetalk_batch_size: 4
  use_float16: true
  tts_profile: "quality"
  primary_lipsync_engine: "heygem_local"

heygem:
  base_url: "http://127.0.0.1:8383/easy"
  shared_root: "F:/duikouxing-runtime/heygem/data/face2face"
  timeout_seconds: 7200
  poll_interval_seconds: 2.0
```

路径校验规则：

```python
def require_under(path: Path, root: Path, label: str) -> None:
    resolved = path.resolve()
    resolved_root = root.resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ConfigurationError(f"{label} 必须位于 {resolved_root} 下: {resolved}")
```

所有环境、模型、缓存、临时目录、任务目录、HeyGem 共享目录都调用该检查。不得用简单字符串
前缀判断路径。

### 5.2 JobConfig

`lipsync.engine` 增加 `heygem_local`。

增加保护区域：

```yaml
lipsync:
  engine: "heygem_local"
  chaofen: 0
  watermark_switch: 0
  pn: 1

composite:
  mode: "restore_protected_regions"
  boundary_feather_pixels: 2
  require_exact_frame_count: true

protected_regions:
  - name: "certificate"
    type: "polygon"
    # 归一化坐标，按顺时针或逆时针填写。
    points:
      - [0.24, 0.55]
      - [0.76, 0.55]
      - [0.76, 0.94]
      - [0.24, 0.94]
    margin_pixels: 12
```

要求：

- `protected_regions` 名称唯一。
- 点坐标必须位于 `[0, 1]`。
- 多边形至少三个点且面积大于零。
- 保护区域应包含证件、证件边缘、手指以及周围 10～20px 安全边界。
- 首版只支持固定区域；如证件移动超过边界，素材验收失败。
- 配置和日志不得记录由程序 OCR 得到的完整身份证号码。

## 6. HeyGem 适配器核心实现

新增 `src/digital_human/adapters/heygem.py`。下面代码是实现骨架，GLM5 应补齐测试和项目内
异常类型，但不要随意更改 API 字段。

```python
from __future__ import annotations

import json
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from ..config import JobConfig, LocalConfig


class HeyGemError(RuntimeError):
    pass


def _json_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HeyGemError(f"HeyGem 请求失败: {url}: {exc}") from exc
    if not isinstance(result, dict):
        raise HeyGemError(f"HeyGem 返回值不是 JSON 对象: {url}")
    return result


def _safe_result_path(shared_root: Path, relative: str) -> Path:
    # API 返回 Linux 风格相对路径；拒绝绝对路径和目录穿越。
    pure = PurePosixPath(relative.replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts:
        raise HeyGemError(f"HeyGem 返回了不安全的结果路径: {relative!r}")
    result = shared_root.joinpath(*pure.parts).resolve()
    root = shared_root.resolve()
    if not result.is_relative_to(root):
        raise HeyGemError(f"HeyGem 结果越过共享目录: {result}")
    return result


class HeyGemAdapter:
    def __init__(self, config: LocalConfig) -> None:
        self.config = config

    def generate(
        self,
        *,
        video: Path,
        audio: Path,
        output: Path,
        job: JobConfig,
        work_dir: Path,
        log_file: Path,
    ) -> None:
        shared_root = self.config.heygem_shared_root.resolve()
        task_code = uuid.uuid4().hex
        stage_dir = shared_root / "temp" / task_code
        stage_dir.mkdir(parents=True, exist_ok=False)

        staged_video = stage_dir / "base.mp4"
        staged_audio = stage_dir / "target.wav"
        shutil.copy2(video, staged_video)
        shutil.copy2(audio, staged_audio)

        # 容器把 shared_root 映射为 /code/data，API 使用相对 /code/data 的路径。
        video_ref = staged_video.relative_to(shared_root).as_posix()
        audio_ref = staged_audio.relative_to(shared_root).as_posix()
        payload = {
            "audio_url": audio_ref,
            "video_url": video_ref,
            "code": task_code,
            "chaofen": int(job.lipsync.get("chaofen", 0)),
            "watermark_switch": int(job.lipsync.get("watermark_switch", 0)),
            "pn": int(job.lipsync.get("pn", 1)),
        }

        log_file.parent.mkdir(parents=True, exist_ok=True)
        # 日志只写任务码和状态，不写话术、证件号或完整媒体路径。
        log_file.write_text(f"submit code={task_code}\n", encoding="utf-8")

        submit = _json_request(
            f"{self.config.heygem_base_url.rstrip('/')}/submit",
            method="POST",
            payload=payload,
            timeout=60,
        )
        if int(submit.get("code", -1)) != 10000:
            raise HeyGemError(f"HeyGem 拒绝任务: code={submit.get('code')} msg={submit.get('msg')}")

        deadline = time.monotonic() + self.config.heygem_timeout_seconds
        query_url = (
            f"{self.config.heygem_base_url.rstrip('/')}/query?"
            + urllib.parse.urlencode({"code": task_code})
        )
        result_ref: str | None = None
        while time.monotonic() < deadline:
            status = _json_request(query_url, timeout=30)
            if int(status.get("code", -1)) != 10000:
                code = int(status.get("code", -1))
                if code in {9999, 10002, 10003}:
                    raise HeyGemError(
                        f"HeyGem 任务失败: code={code} msg={status.get('msg')}"
                    )
                time.sleep(self.config.heygem_poll_interval_seconds)
                continue

            data = status.get("data") or {}
            state = int(data.get("status", 0))
            with log_file.open("a", encoding="utf-8") as handle:
                handle.write(f"status={state} progress={data.get('progress', '')}\n")
            if state == 2:
                result_ref = str(data.get("result", ""))
                break
            if state == 3:
                raise HeyGemError(f"HeyGem 生成失败: {data.get('msg', '')}")
            time.sleep(self.config.heygem_poll_interval_seconds)

        if not result_ref:
            raise HeyGemError(f"HeyGem 任务超时: {task_code}")

        produced = _safe_result_path(shared_root, result_ref)
        if not produced.is_file() or produced.stat().st_size == 0:
            raise HeyGemError(f"HeyGem 未生成有效视频: {result_ref}")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(produced, output)
```

实施注意：

- 首次人工调用 API 时确认 `audio_url`、`video_url` 和 `data.result` 的相对路径语义；如果当前
  镜像版本不同，只允许在适配器内部兼容，不能把容器路径散落到业务代码中。
- 成功后是否清理 `stage_dir` 做成配置项；开发阶段默认保留，验收稳定后再开启清理。
- 清理时只能删除已经解析确认位于 `heygem_shared_root/temp/<task_code>` 下的目录。
- 不得把用户话术或证件号码写入 HeyGem 日志。

## 7. Pipeline 接入

在 `pipeline.py` 中使用显式三分支，禁止把未知引擎落入 MuseTalk：

```python
if lipsync_engine == "latentsync_1_6":
    # 保留现有实现
    ...
elif lipsync_engine == "musetalk_1_5":
    # 保留现有实现
    ...
elif lipsync_engine == "heygem_local":
    heygem_video = self.work_dir / "heygem_result.mp4"
    if self._should_run(heygem_video):
        HeyGemAdapter(self.local).generate(
            video=base_video,
            audio=target_audio,
            output=heygem_video,
            job=self.job,
            work_dir=self.work_dir,
            log_file=self.log_dir / "heygem.log",
        )

    visual_result = self.work_dir / "heygem_protected.mkv"
    if self._should_run(visual_result):
        restore_protected_regions(
            base_video=base_video,
            generated_video=heygem_video,
            output=visual_result,
            fps=fps,
            regions=self.job.protected_regions,
            options=self.job.composite,
        )
    copy_final_video = False
else:
    raise RuntimeError(f"不支持的口型引擎: {lipsync_engine}")
```

HeyGem 分支不得调用现有 `dynamic_texture` 或 `fixed_roi`。那两种模式用于 MuseTalk，直接复用
会再次引入嘴部贴片感。

Manifest 增加：

```json
{
  "lipsync_engine": "heygem_local",
  "heygem_image_id": "部署时采集",
  "heygem_image_digest": "部署时采集",
  "protected_regions": ["certificate"],
  "frame_invariants_passed": true,
  "protected_region_validation_passed": true
}
```

Manifest 只保存保护区名称和坐标，不保存 OCR 得到的证件文字。

## 8. 证件保护区合成核心代码

新增 `src/digital_human/protection.py`。首版不贴嘴，只恢复证件区域。

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .composite import _video_writer


class ProtectionError(RuntimeError):
    pass


def _polygon_mask(
    width: int,
    height: int,
    regions: list[dict[str, Any]],
    feather_pixels: int,
) -> np.ndarray:
    mask = np.zeros((height, width), dtype=np.uint8)
    for region in regions:
        points = region.get("points") or []
        polygon = np.asarray(
            [
                [
                    round(float(x) * (width - 1)),
                    round(float(y) * (height - 1)),
                ]
                for x, y in points
            ],
            dtype=np.int32,
        )
        if len(polygon) < 3:
            raise ProtectionError(f"保护区点数不足: {region.get('name')}")
        cv2.fillPoly(mask, [polygon], 255)
        margin = max(0, int(region.get("margin_pixels", 0)))
        if margin:
            kernel = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (margin * 2 + 1, margin * 2 + 1)
            )
            mask = cv2.dilate(mask, kernel)
    if feather_pixels:
        kernel_size = feather_pixels * 2 + 1
        mask = cv2.GaussianBlur(mask, (kernel_size, kernel_size), 0)
    return mask.astype(np.float32)[:, :, None] / 255.0


def restore_protected_regions(
    *,
    base_video: Path,
    generated_video: Path,
    output: Path,
    fps: int,
    regions: list[dict[str, Any]],
    options: dict[str, Any],
) -> None:
    base = cv2.VideoCapture(str(base_video))
    generated = cv2.VideoCapture(str(generated_video))
    try:
        width = int(base.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(base.get(cv2.CAP_PROP_FRAME_HEIGHT))
        base_frames = int(base.get(cv2.CAP_PROP_FRAME_COUNT))
        base_fps = float(base.get(cv2.CAP_PROP_FPS))
        gen_size = (
            int(generated.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(generated.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        )
        gen_frames = int(generated.get(cv2.CAP_PROP_FRAME_COUNT))
        gen_fps = float(generated.get(cv2.CAP_PROP_FPS))

        if (width, height) != gen_size:
            raise ProtectionError(
                f"HeyGem 改变了画面尺寸: {(width, height)} != {gen_size}"
            )
        if abs(base_fps - gen_fps) > 0.01:
            raise ProtectionError(f"HeyGem 改变了 FPS: {base_fps} != {gen_fps}")
        if bool(options.get("require_exact_frame_count", True)) and base_frames != gen_frames:
            raise ProtectionError(
                f"HeyGem 改变了帧数: {base_frames} != {gen_frames}"
            )

        alpha = _polygon_mask(
            width,
            height,
            regions,
            int(options.get("boundary_feather_pixels", 2)),
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        writer = _video_writer(output, fps, (width, height))
        written = 0
        try:
            while True:
                base_ok, base_frame = base.read()
                gen_ok, gen_frame = generated.read()
                if not base_ok and not gen_ok:
                    break
                if base_ok != gen_ok:
                    raise ProtectionError("基准视频与 HeyGem 视频没有逐帧对应")
                # HeyGem 为主体；证件保护区恢复 base 同帧像素。
                result = (
                    gen_frame.astype(np.float32) * (1.0 - alpha)
                    + base_frame.astype(np.float32) * alpha
                )
                writer.write(np.clip(result, 0, 255).astype(np.uint8))
                written += 1
        finally:
            writer.release()
        if written == 0:
            raise ProtectionError("保护区合成未产生任何帧")
        if bool(options.get("require_exact_frame_count", True)) and written != base_frames:
            raise ProtectionError(f"保护区合成帧数错误: {written} != {base_frames}")
    finally:
        base.release()
        generated.release()
```

说明：边界羽化只作用于证件外围，不应覆盖或模糊证件内部文字。由于 base 与 HeyGem 理论上
共享同一身体画面，2px 羽化通常足够。若证件边缘仍出现接缝，优先检查帧对应关系，禁止盲目
把羽化提高到十几或几十像素。

## 9. 保护区域标注

新增 CLI：

```text
digital-human annotate-protected-region --job <job.yaml> --name certificate --at-seconds 0
digital-human preview-protected-regions --job <job.yaml> --output protected-preview.jpg
```

交互行为：

1. 展示原视频指定时间帧。
2. 用户用鼠标依次点击证件四角或拖出矩形。
3. 自动增加 `margin_pixels`，把手指和证件边缘包含在内。
4. 写回归一化坐标。
5. 输出带边框的预览图，必须人工确认后才能运行。

不要自动识别或记录证件号码。首版不引入 OCR 参与标注。

## 10. 差异检测与验收

### 10.1 自动验收

新增 `src/digital_human/quality.py`，至少生成以下指标：

- 输入基准视频与 HeyGem 输出的分辨率、FPS、帧数完全一致。
- 保护恢复前：输出保护区变化热力图，用于观察 HeyGem 是否动过证件。
- 保护恢复后、最终编码前：保护区核心区域必须与 base 同帧逐像素相同。
- 最终 H.264 编码后：保护区 SSIM 建议不低于 `0.995`；低于阈值拒绝交付。
- 统计保护区外、脸部外的变化比例；如发生大片非压缩噪声级变化，拒绝该 HeyGem 镜像版本。
- 生成第 1 帧、中间帧、末帧以及变化最大帧的对比图。

核心区域与羽化边缘必须分开测量。逐像素相同只检查未经羽化的保护区核心；不能用模糊后的
边缘降低结果。

### 10.2 人工验收

至少逐项检查：

- 嘴是否上下或左右漂移。
- 是否出现两套嘴唇、嘴角重影、牙齿闪烁。
- 脸型、下巴宽度、鼻唇沟是否忽大忽小。
- 头部动作是否与原片一致。
- 手和证件位置是否一致。
- 姓名、数字、证件照片、印章、二维码是否保持原内容。
- 视频循环点是否明显。
- 声音是否为授权人物，数字、人名、公司名是否读对。

任何一个证件字符、照片、二维码或印章发生内容变化，都判定失败，不得靠“肉眼不明显”放行。

## 11. 测试要求

新增：

```text
tests/test_heygem.py
tests/test_protection.py
tests/test_storage_layout.py
```

必须覆盖：

### HeyGem 适配器

- submit 请求字段正确。
- pending -> success 的轮询流程。
- 服务返回失败状态。
- 超时。
- 非 JSON 返回。
- `data.result` 包含 `..`、绝对路径或越界路径时拒绝。
- 输出文件不存在或大小为零时拒绝。
- 日志中不含话术和输入文件的敏感文本。

测试不得真的启动 GPU 服务；使用本地假 HTTP 服务或 monkeypatch。

### 保护区合成

- 保护区内部来自 base。
- 保护区外部来自 generated。
- 多个保护区域同时生效。
- 非法坐标和退化多边形被拒绝。
- 尺寸、FPS 或帧数不一致时拒绝。
- 生成视频提前结束时拒绝。
- FFV1 中间输出可读且帧数正确。

### 存储

- 所有运行时路径必须位于 `F:\duikouxing-runtime`。
- `C:` 路径和目录穿越被拒绝。
- `G:\duikouxing` 只允许源码、配置和文档路径，不允许作为新环境或权重目录。

## 12. 分阶段实施顺序

### 阶段 A：只验证 HeyGem 原生效果

1. 将 Docker Desktop 磁盘镜像迁移到 F 盘。
2. 启动 Lite 视频容器。
3. 手工把一段 5～10 秒、不含真实证件的测试视频和 WAV 放进共享目录。
4. 手工调用 `/easy/submit` 和 `/easy/query`。
5. 确认 API 路径格式、结果路径、FPS、帧数和分辨率。
6. 与原片做差异热力图，确认变化是否集中在人脸。

阶段 A 未通过，不进入代码集成。

### 阶段 B：适配器接入

1. 实现配置扩展。
2. 实现 `HeyGemAdapter`。
3. 接入 Pipeline 明确分支。
4. 完成 fake server 单元测试。
5. 生成 `heygem_result.mp4`，暂不做任何嘴部或脸部二次合成。

### 阶段 C：证件保护

1. 实现固定多边形标注。
2. 实现 `restore_protected_regions`。
3. 实现帧级不变量检查和差异报告。
4. 先用虚构证件测试。
5. 通过后再使用已授权真实素材，且不得提交到 Git。

### 阶段 D：A/B 决策

同一视频、同一目标音频输出：

1. MuseTalk 当前最佳参数。
2. LatentSync 当前最佳参数。
3. HeyGem 原生输出。
4. HeyGem + 证件保护输出。

如果 HeyGem 存在明显脸型漂移、双嘴、牙齿闪烁，或者无法保证逐帧对应，则保留为实验引擎，
不得设为生产默认值。

## 13. GLM5 完成定义

只有同时满足以下条件才算完成：

- `pytest` 全部通过。
- `ruff check` 通过。
- `doctor --profile home` 能检查 HeyGem 容器、端口、GPU 和 F 盘目录。
- C 盘没有新增 Conda 环境、模型缓存或 Docker 大文件。
- 5～10 秒虚构证件素材端到端生成成功。
- HeyGem 输出与基准视频逐帧对应。
- 证件保护区在无损中间结果中逐像素等于 base 核心区域。
- 最终编码后保护区 SSIM 达标。
- 人工检查未发现嘴漂移、双嘴和明显贴片边缘。
- Manifest 记录镜像 digest、配置摘要和验收结果，但不记录完整证件号。
- README、安装文档和示例配置同步更新。

## 14. 隐私与仓库清理要求

当前仓库的部分 `config/job.cloud*.yaml` 看起来包含完整姓名和身份证号码。开始开发前必须由
项目负责人判断是否为真实信息：

- 如果是真实信息，先停止继续同步和分享仓库。
- 从工作区配置、测试素材和日志中替换为虚构数据。
- 检查 Git 历史；只删除当前文件不能清除历史提交。
- 已经推送到远端时，需要按敏感信息泄露流程重写历史并轮换访问权限。
- 真实客户素材只能放在 F 盘任务目录，必须被 `.gitignore` 排除。

不得把真实身份证、声音或人脸上传到公共模型 Demo、Colab、Hugging Face Space 或第三方
测试网站。

## 15. 许可证注意事项

HeyGem/Duix.Avatar 使用自定义 DUIX.COM Community License，不是 MIT/Apache 类宽松许可证。
正式商用、对外分发或达到许可证约定的用户规模前，必须复核项目当时版本的 LICENSE 并取得
必要授权。代码中保存实际使用的仓库提交、Docker 镜像 ID/digest 和许可证副本，不能只引用
可能变化的在线 README。

