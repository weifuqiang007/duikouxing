# 本地口型数字人 Demo

项目位置为本仓库所在目录（本机为 `E:\duikouxing`），环境、模型和缓存全部放在项目目录内，不写入 C 盘。客户提供单人、近似固定机位的视频，人物可能手持广告牌、证书或身份证；系统在本地克隆人物声音，只修改嘴部及少量下颌区域，背景、身体、手持物和敏感信息保持原视频内容。

技术栈：

- 声音克隆：`dots.tts-soar`；`dots.tts-mf` 为快速模式。
- 口型同步：MuseTalk 1.5。
- 视频处理：FFmpeg + OpenCV 局部 ROI 合成。
- 编排：本仓库 Python CLI。

## 两台电脑的使用方式

两台电脑不在同一局域网，本项目不做跨机器调度。每台电脑独立安装环境和模型：

| 地点 | 显卡 | 配置 | 默认 MuseTalk batch |
|---|---|---|---:|
| 公司 | RTX 3060 12GB | `config/local.office.yaml` | 2 |
| 家里 | RTX 4070 12GB | `config/local.home.yaml` | 4 |

切换只需改变 `-Profile`：

```powershell
# 公司电脑
.\scripts\run_job.ps1 -Profile office -Job .\config\job.local.yaml

# 家里电脑
.\scripts\run_job.ps1 -Profile home -Job .\config\job.local.yaml
```

## 安装

所有 Conda 环境、模型、Hugging Face/Torch/pip 缓存和临时文件都放在项目目录下，不写入 C 盘的用户模型缓存：

```text
E:\duikouxing\
├── .conda-envs\       # 三个固定 Python 版本的 Conda 环境
├── .cache\            # Hugging Face、Torch、pip 缓存
├── .tmp\              # 推理和下载临时目录
├── models\            # dots.tts 权重
└── external\MuseTalk\models\  # MuseTalk 官方权重结构
```

执行：

```powershell
Set-Location E:\duikouxing
.\scripts\setup_conda.ps1
.\scripts\download_models.ps1
```

详细步骤见[安装部署手册](docs/INSTALLATION.md)。

## 使用

复制并编辑任务配置：

```powershell
Copy-Item .\config\job.example.yaml .\config\job.local.yaml
```

将 `consent_confirmed` 改为 `true` 前，必须确认已取得人物肖像和声音授权。

环境检查：

```powershell
$Python = 'E:\duikouxing\.conda-envs\digital-human\python.exe'
# 公司电脑只运行：
& $Python -m digital_human.cli doctor --profile office
# 家庭电脑只运行：
& $Python -m digital_human.cli doctor --profile home
```

ROI 预览：

```powershell
& $Python -m digital_human.cli preview-roi --job .\config\job.local.yaml --output .\roi-preview.jpg
```

运行任务：

```powershell
.\scripts\run_job.ps1 -Profile office -Job .\config\job.local.yaml
```

公司输出位于 `jobs-office/<job_id>/output/final.mp4`，家庭输出位于 `jobs-home/<job_id>/output/final.mp4`。

## 文档

- [项目架构书](docs/PROJECT_ARCHITECTURE.md)
- [安装部署手册](docs/INSTALLATION.md)
- [模型、权重与许可证](docs/MODELS_AND_LICENSES.md)
- [验收标准](docs/ACCEPTANCE.md)
- [开发清单](docs/DEVELOPMENT_CHECKLIST.md)

## 安全边界

- `consent_confirmed` 不为 `true` 时拒绝运行。
- 不包含云端上传代码。
- 真实身份证素材不得用于身份核验、开户、贷款、签约或冒充本人。
- 对外发布必须添加适用的 AI 生成显式/隐式标识。
