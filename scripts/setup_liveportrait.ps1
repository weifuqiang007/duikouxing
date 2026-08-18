param()

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$EnvRoot = Join-Path $ProjectRoot ".conda-envs"
$LivePortraitPrefix = Join-Path $EnvRoot "liveportrait"
$LivePortraitRoot = Join-Path $ProjectRoot "external\LivePortrait"
$CacheRoot = Join-Path $ProjectRoot ".cache"
$TempRoot = Join-Path $ProjectRoot ".tmp"
$LivePortraitCommit = "9b294b3d0536135442ea73cb01e6cb3ca7029dd3"

if ($ProjectRoot -match "^C:\\") {
    throw "为避免环境落到系统盘，本脚本要求项目根目录不在 C 盘；实际为 $ProjectRoot"
}

New-Item -ItemType Directory -Force -Path $EnvRoot, $CacheRoot, $TempRoot, (Join-Path $ProjectRoot "external") | Out-Null
$env:HF_HOME = Join-Path $CacheRoot "huggingface"
$env:HF_HUB_CACHE = Join-Path $env:HF_HOME "hub"
$env:TORCH_HOME = Join-Path $CacheRoot "torch"
$env:PIP_CACHE_DIR = Join-Path $CacheRoot "pip"
$env:XDG_CACHE_HOME = $CacheRoot
$env:TEMP = $TempRoot
$env:TMP = $TempRoot

function Invoke-Step {
    param([string]$Description, [scriptblock]$Action)
    Write-Host "==> $Description"
    & $Action
    if ($LASTEXITCODE -ne 0) { throw "$Description 失败，退出码 $LASTEXITCODE" }
}

if (-not (Test-Path -LiteralPath $LivePortraitPrefix)) {
    Invoke-Step "创建 LivePortrait Conda 环境 (Python 3.10.13)" {
        conda create -y -p $LivePortraitPrefix python=3.10.13 pip=24.2
    }
}

if (-not (Test-Path -LiteralPath $LivePortraitRoot)) {
    Invoke-Step "克隆 LivePortrait 官方仓库" {
        git clone https://github.com/KlingAIResearch/LivePortrait.git $LivePortraitRoot
    }
}
$Remote = git -C $LivePortraitRoot remote get-url origin
if ($Remote -notmatch "(KlingAIResearch|KwaiVGI|KlingTeam)/LivePortrait") {
    throw "external/LivePortrait 指向非官方仓库：$Remote"
}
Invoke-Step "同步并固定 LivePortrait 提交 $LivePortraitCommit" {
    git -C $LivePortraitRoot fetch origin
    git -C $LivePortraitRoot checkout $LivePortraitCommit
}
Invoke-Step "安装 PyTorch 2.3.0 CUDA 12.1" {
    conda run -p $LivePortraitPrefix pip install torch==2.3.0 torchvision==0.18.0 torchaudio==2.3.0 --index-url https://download.pytorch.org/whl/cu121
}
Invoke-Step "安装 LivePortrait 官方依赖" {
    conda run -p $LivePortraitPrefix pip install -r (Join-Path $LivePortraitRoot "requirements.txt")
}
Invoke-Step "安装权重下载工具" {
    conda run -p $LivePortraitPrefix pip install "huggingface-hub[cli]==0.36.0"
}

Write-Host "LivePortrait 环境已安装：$LivePortraitPrefix"
Write-Host "下一步运行 scripts\download_liveportrait_models.ps1"
