param()

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$EnvRoot = Join-Path $ProjectRoot ".conda-envs"
$CacheRoot = Join-Path $ProjectRoot ".cache"
$TempRoot = Join-Path $ProjectRoot ".tmp"
$ExternalRoot = Join-Path $ProjectRoot "external"
$MuseTalkRoot = Join-Path $ExternalRoot "MuseTalk"
$MuseTalkCommit = "0a89dec45a0192b824e3cf4daf96c239440c5ed8"

if ($ProjectRoot -match "^C:\\") {
    throw "为避免环境落到系统盘，本脚本要求项目根目录不在 C 盘；实际为 $ProjectRoot"
}

# 使用项目内 .condarc（国内镜像 + 项目内包缓存），不依赖也不修改用户全局 conda 配置。
$ProjectCondarc = Join-Path $ProjectRoot ".condarc"
$PkgsDir = Join-Path $ProjectRoot ".conda-pkgs"
if (-not (Test-Path -LiteralPath $ProjectCondarc)) {
    $CondarcContent = @"
channels:
  - defaults
default_channels:
  - https://mirror.nju.edu.cn/anaconda/pkgs/main
  - https://mirror.nju.edu.cn/anaconda/pkgs/r
show_channel_urls: true
pkgs_dirs:
  - $($PkgsDir.Replace('\', '/'))
"@
    Set-Content -LiteralPath $ProjectCondarc -Value $CondarcContent -Encoding ASCII
}
$env:CONDARC = $ProjectCondarc

New-Item -ItemType Directory -Force -Path $EnvRoot, $CacheRoot, $TempRoot, $ExternalRoot, $PkgsDir | Out-Null
$env:HF_HOME = Join-Path $CacheRoot "huggingface"
$env:HF_HUB_CACHE = Join-Path $env:HF_HOME "hub"
$env:TORCH_HOME = Join-Path $CacheRoot "torch"
$env:PIP_CACHE_DIR = Join-Path $CacheRoot "pip"
$env:XDG_CACHE_HOME = $CacheRoot
$env:TEMP = $TempRoot
$env:TMP = $TempRoot

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)][string]$Description,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )
    Write-Host "==> $Description"
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "$Description 失败，退出码 $LASTEXITCODE"
    }
}

# 频道显式指定并 --override-channels，避免用户全局 .condarc 中失效镜像污染解析；
# Python/pip 版本仍以 environments/*.yml 为唯一事实来源。
$CondaChannel = "https://mirror.nju.edu.cn/anaconda/pkgs/main"
$CondaForgeChannel = "https://mirror.nju.edu.cn/anaconda/cloud/conda-forge"

function Get-SpecVersion {
    param(
        [Parameter(Mandatory = $true)][string]$YmlPath,
        [Parameter(Mandatory = $true)][string]$Package
    )
    $line = Select-String -LiteralPath $YmlPath -Pattern ("^\s*-\s*" + $Package.Replace(".", "\.") + "=(\S+)") |
        Select-Object -First 1
    if (-not $line) { throw "在 $YmlPath 中找不到 $Package 的固定版本" }
    return $line.Matches[0].Groups[1].Value
}

function New-ProjectEnv {
    param(
        [Parameter(Mandatory = $true)][string]$Prefix,
        [Parameter(Mandatory = $true)][string]$YmlName
    )
    $yml = Join-Path $ProjectRoot "environments\$YmlName"
    $pythonVersion = Get-SpecVersion -YmlPath $yml -Package "python"
    $pipVersion = Get-SpecVersion -YmlPath $yml -Package "pip"
    Invoke-Step "创建环境 $Prefix (Python $pythonVersion)" {
        conda create -y -p $Prefix --override-channels -c $CondaChannel "python=$pythonVersion" "pip=$pipVersion"
    }
}

$OrchestratorPrefix = Join-Path $EnvRoot "digital-human"
$DotsPrefix = Join-Path $EnvRoot "dots-tts"
$MuseTalkPrefix = Join-Path $EnvRoot "musetalk"

if (-not (Test-Path -LiteralPath $OrchestratorPrefix)) {
    New-ProjectEnv -Prefix $OrchestratorPrefix -YmlName "orchestrator.yml"
}
if (-not (Test-Path -LiteralPath $DotsPrefix)) {
    New-ProjectEnv -Prefix $DotsPrefix -YmlName "dots-tts.yml"
}
if (-not (Test-Path -LiteralPath $MuseTalkPrefix)) {
    New-ProjectEnv -Prefix $MuseTalkPrefix -YmlName "musetalk.yml"
}

Invoke-Step "升级编排环境 pip" {
    conda run -p $OrchestratorPrefix python -m pip install --upgrade pip
}
Invoke-Step "安装本项目到编排环境" {
    conda run -p $OrchestratorPrefix pip install -e "$ProjectRoot[dev]"
}

Invoke-Step "升级 dots.tts 环境 pip" {
    conda run -p $DotsPrefix python -m pip install --upgrade pip
}
Invoke-Step "安装 dots.tts PyTorch 2.8.0 cu128" {
    conda run -p $DotsPrefix pip install torch==2.8.0 torchaudio==2.8.0 --timeout 120 --retries 10 --index-url https://download.pytorch.org/whl/cu128
}
# pynini（WeTextProcessing 的依赖）在 PyPI 上没有 Windows wheel，从 conda-forge 安装。
Invoke-Step "安装 pynini (conda-forge)" {
    conda install -y -p $DotsPrefix --override-channels -c $CondaForgeChannel "python=3.11.9" pynini
}
# gradio>=6.17 在 PyPI 上不存在（最高 6.9.0），本项目也不使用 dots.tts 的网页界面，
# 因此 dots.tts 本体用 --no-deps 安装，运行依赖按下表手动补齐（跳过 gradio）。
Invoke-Step "安装 dots.tts==0.3.1 (--no-deps)" {
    conda run -p $DotsPrefix pip install "dots.tts==0.3.1" --no-deps
}
# qwen-asr 是 dots.tts 的可选扩展（edit-playground ASR），其 transformers==4.57.6
# 固定与官方推荐 4.57.0 冲突，且代码只在可选功能中懒加载，本项目不使用，跳过。
Invoke-Step "安装 dots.tts 运行依赖" {
    conda run -p $DotsPrefix pip install transformers huggingface-hub loguru "langcodes[data]" einops librosa soundfile numpy pydantic PyYAML safetensors torchdiffeq tqdm lingua-language-detector WeTextProcessing accelerate tensorboard -c (Join-Path $ProjectRoot "constraints\dots-tts-recommended.txt")
}

if (-not (Test-Path -LiteralPath $MuseTalkRoot)) {
    Invoke-Step "克隆 MuseTalk 官方仓库" {
        git clone https://github.com/TMElyralab/MuseTalk.git $MuseTalkRoot
    }
}
$ActualRemote = git -C $MuseTalkRoot remote get-url origin
if ($ActualRemote -notmatch "TMElyralab/MuseTalk") {
    throw "external/MuseTalk 指向非官方仓库：$ActualRemote"
}
Invoke-Step "同步 MuseTalk 远端" {
    git -C $MuseTalkRoot fetch origin
}
Invoke-Step "固定 MuseTalk 提交 $MuseTalkCommit" {
    git -C $MuseTalkRoot checkout $MuseTalkCommit
}

Invoke-Step "安装 MuseTalk PyTorch 2.0.1 cu118" {
    conda run -p $MuseTalkPrefix pip install torch==2.0.1 torchvision==0.15.2 torchaudio==2.0.2 --timeout 120 --retries 10 --index-url https://download.pytorch.org/whl/cu118
}
Invoke-Step "安装 MuseTalk requirements.txt" {
    conda run -p $MuseTalkPrefix pip install -r (Join-Path $MuseTalkRoot "requirements.txt")
}
Invoke-Step "安装 openmim" {
    conda run -p $MuseTalkPrefix pip install --no-cache-dir -U openmim
}
Invoke-Step "安装 mmengine" {
    conda run -p $MuseTalkPrefix mim install mmengine
}
Invoke-Step "安装 mmcv==2.0.1" {
    conda run -p $MuseTalkPrefix mim install "mmcv==2.0.1"
}
Invoke-Step "安装 mmdet==3.1.0" {
    conda run -p $MuseTalkPrefix mim install "mmdet==3.1.0"
}
Invoke-Step "安装 mmpose==1.1.0" {
    conda run -p $MuseTalkPrefix mim install "mmpose==1.1.0"
}

Write-Host "Conda 环境已安装到 $EnvRoot"
Write-Host "下一步运行 scripts\download_models.ps1"
