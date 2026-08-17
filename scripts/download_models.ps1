param()

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$DotsPrefix = Join-Path $ProjectRoot ".conda-envs\dots-tts"
$MuseTalkPrefix = Join-Path $ProjectRoot ".conda-envs\musetalk"
$MuseTalkRoot = Join-Path $ProjectRoot "external\MuseTalk"
$CacheRoot = Join-Path $ProjectRoot ".cache"
$TempRoot = Join-Path $ProjectRoot ".tmp"

if ($ProjectRoot -ne "G:\duikouxing") {
    throw "本脚本要求项目根目录为 G:\duikouxing；实际为 $ProjectRoot"
}
if (-not (Test-Path -LiteralPath $DotsPrefix)) { throw "缺少 dots.tts 环境，请先运行 setup_conda.ps1" }
if (-not (Test-Path -LiteralPath $MuseTalkPrefix)) { throw "缺少 MuseTalk 环境，请先运行 setup_conda.ps1" }
if (-not (Test-Path -LiteralPath $MuseTalkRoot)) { throw "缺少官方 MuseTalk 仓库" }

New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "models"), $CacheRoot, $TempRoot | Out-Null
$env:HF_HOME = Join-Path $CacheRoot "huggingface"
$env:HF_HUB_CACHE = Join-Path $env:HF_HOME "hub"
$env:TORCH_HOME = Join-Path $CacheRoot "torch"
$env:PIP_CACHE_DIR = Join-Path $CacheRoot "pip"
$env:XDG_CACHE_HOME = $CacheRoot
$env:TEMP = $TempRoot
$env:TMP = $TempRoot

conda run -p $DotsPrefix hf download dots-studio/dots.tts-soar --local-dir (Join-Path $ProjectRoot "models\dots.tts-soar")
conda run -p $DotsPrefix hf download dots-studio/dots.tts-mf --local-dir (Join-Path $ProjectRoot "models\dots.tts-mf")

$MuseTalkModels = Join-Path $MuseTalkRoot "models"
conda run -p $DotsPrefix hf download TMElyralab/MuseTalk --local-dir $MuseTalkModels
conda run -p $DotsPrefix hf download stabilityai/sd-vae-ft-mse `
    --local-dir (Join-Path $MuseTalkModels "sd-vae") `
    --include "config.json" "diffusion_pytorch_model.bin"
conda run -p $DotsPrefix hf download openai/whisper-tiny `
    --local-dir (Join-Path $MuseTalkModels "whisper") `
    --include "config.json" "pytorch_model.bin" "preprocessor_config.json"
conda run -p $DotsPrefix hf download yzd-v/DWPose `
    --local-dir (Join-Path $MuseTalkModels "dwpose") `
    --include "dw-ll_ucoco_384.pth"
conda run -p $DotsPrefix hf download ByteDance/LatentSync `
    --local-dir (Join-Path $MuseTalkModels "syncnet") `
    --include "latentsync_syncnet.pt"
conda run -p $DotsPrefix hf download ManyOtherFunctions/face-parse-bisent `
    --local-dir (Join-Path $MuseTalkModels "face-parse-bisent") `
    --include "79999_iter.pth" "resnet18-5c106cde.pth"

& (Join-Path $ProjectRoot "scripts\hash_model_files.ps1") `
    -MuseTalkRoot (Join-Path $MuseTalkRoot "models") `
    -DotsRoot (Join-Path $ProjectRoot "models")

Write-Host "模型下载完成。所有权重和缓存均位于 $ProjectRoot 下。"
