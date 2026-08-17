param()

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$DotsPrefix = Join-Path $ProjectRoot ".conda-envs\dots-tts"
$MuseTalkPrefix = Join-Path $ProjectRoot ".conda-envs\musetalk"
$MuseTalkRoot = Join-Path $ProjectRoot "external\MuseTalk"
$CacheRoot = Join-Path $ProjectRoot ".cache"
$TempRoot = Join-Path $ProjectRoot ".tmp"

if ($ProjectRoot -match "^C:\\") {
    throw "为避免模型落到系统盘，本脚本要求项目根目录不在 C 盘；实际为 $ProjectRoot"
}
if (-not (Test-Path -LiteralPath $DotsPrefix)) { throw "缺少 dots.tts 环境，请先运行 setup_conda.ps1" }
if (-not (Test-Path -LiteralPath $MuseTalkPrefix)) { throw "缺少 MuseTalk 环境，请先运行 setup_conda.ps1" }
if (-not (Test-Path -LiteralPath $MuseTalkRoot)) { throw "缺少官方 MuseTalk 仓库" }

# huggingface.co 在本机网络不可达，默认使用 hf-mirror.com 镜像；如需换源可预设 HF_ENDPOINT。
if (-not $env:HF_ENDPOINT) { $env:HF_ENDPOINT = "https://hf-mirror.com" }
Write-Host "使用 Hugging Face 端点: $env:HF_ENDPOINT"
# 本机系统代理会把 hf-mirror.com 308 重写回被墙的 huggingface.co，导致下载失败；
# 对镜像域名绕过系统代理直连。
if ($env:HF_ENDPOINT -match "hf-mirror") {
    $env:NO_PROXY = (@($env:NO_PROXY, "hf-mirror.com") | Where-Object { $_ }) -join ","
    $env:no_proxy = $env:NO_PROXY
    Write-Host "镜像域名绕过系统代理: NO_PROXY=$env:NO_PROXY"
}

New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "models"), $CacheRoot, $TempRoot | Out-Null
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

Invoke-Step "下载 dots.tts-soar (约 4.8GB)" {
    conda run -p $DotsPrefix hf download dots-studio/dots.tts-soar --local-dir (Join-Path $ProjectRoot "models\dots.tts-soar")
}
Invoke-Step "下载 dots.tts-mf (约 4.8GB)" {
    conda run -p $DotsPrefix hf download dots-studio/dots.tts-mf --local-dir (Join-Path $ProjectRoot "models\dots.tts-mf")
}

$MuseTalkModels = Join-Path $MuseTalkRoot "models"
Invoke-Step "下载 MuseTalk 1.5 主权重 (约 6.3GB)" {
    conda run -p $DotsPrefix hf download TMElyralab/MuseTalk --local-dir $MuseTalkModels
}
Invoke-Step "下载 SD VAE" {
    conda run -p $DotsPrefix hf download stabilityai/sd-vae-ft-mse `
        --local-dir (Join-Path $MuseTalkModels "sd-vae") `
        --include "config.json" "diffusion_pytorch_model.bin"
}
Invoke-Step "下载 Whisper Tiny" {
    conda run -p $DotsPrefix hf download openai/whisper-tiny `
        --local-dir (Join-Path $MuseTalkModels "whisper") `
        --include "config.json" "pytorch_model.bin" "preprocessor_config.json"
}
Invoke-Step "下载 DWPose" {
    conda run -p $DotsPrefix hf download yzd-v/DWPose `
        --local-dir (Join-Path $MuseTalkModels "dwpose") `
        --include "dw-ll_ucoco_384.pth"
}
Invoke-Step "下载 SyncNet" {
    conda run -p $DotsPrefix hf download ByteDance/LatentSync `
        --local-dir (Join-Path $MuseTalkModels "syncnet") `
        --include "latentsync_syncnet.pt"
}
Invoke-Step "下载 Face Parse BiSeNT" {
    conda run -p $DotsPrefix hf download ManyOtherFunctions/face-parse-bisent `
        --local-dir (Join-Path $MuseTalkModels "face-parse-bisent") `
        --include "79999_iter.pth" "resnet18-5c106cde.pth"
}

& (Join-Path $ProjectRoot "scripts\hash_model_files.ps1") `
    -MuseTalkRoot (Join-Path $MuseTalkRoot "models") `
    -DotsRoot (Join-Path $ProjectRoot "models")

Write-Host "模型下载完成。所有权重和缓存均位于 $ProjectRoot 下。"
