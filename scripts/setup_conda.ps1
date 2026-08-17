param()

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$EnvRoot = Join-Path $ProjectRoot ".conda-envs"
$CacheRoot = Join-Path $ProjectRoot ".cache"
$TempRoot = Join-Path $ProjectRoot ".tmp"
$ExternalRoot = Join-Path $ProjectRoot "external"
$MuseTalkRoot = Join-Path $ExternalRoot "MuseTalk"
$MuseTalkCommit = "0a89dec45a0192b824e3cf4daf96c239440c5ed8"

if ($ProjectRoot -ne "G:\duikouxing") {
    throw "为避免环境落到错误磁盘，本脚本要求项目根目录为 G:\duikouxing；实际为 $ProjectRoot"
}

New-Item -ItemType Directory -Force -Path $EnvRoot, $CacheRoot, $TempRoot, $ExternalRoot | Out-Null
$env:HF_HOME = Join-Path $CacheRoot "huggingface"
$env:HF_HUB_CACHE = Join-Path $env:HF_HOME "hub"
$env:TORCH_HOME = Join-Path $CacheRoot "torch"
$env:PIP_CACHE_DIR = Join-Path $CacheRoot "pip"
$env:XDG_CACHE_HOME = $CacheRoot
$env:TEMP = $TempRoot
$env:TMP = $TempRoot

$OrchestratorPrefix = Join-Path $EnvRoot "digital-human"
$DotsPrefix = Join-Path $EnvRoot "dots-tts"
$MuseTalkPrefix = Join-Path $EnvRoot "musetalk"

if (-not (Test-Path -LiteralPath $OrchestratorPrefix)) {
    conda env create -p $OrchestratorPrefix -f (Join-Path $ProjectRoot "environments\orchestrator.yml")
}
if (-not (Test-Path -LiteralPath $DotsPrefix)) {
    conda env create -p $DotsPrefix -f (Join-Path $ProjectRoot "environments\dots-tts.yml")
}
if (-not (Test-Path -LiteralPath $MuseTalkPrefix)) {
    conda env create -p $MuseTalkPrefix -f (Join-Path $ProjectRoot "environments\musetalk.yml")
}

conda run -p $OrchestratorPrefix python -m pip install --upgrade pip
conda run -p $OrchestratorPrefix pip install -e "$ProjectRoot[dev]"

conda run -p $DotsPrefix python -m pip install --upgrade pip
conda run -p $DotsPrefix pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
conda run -p $DotsPrefix pip install "dots.tts==0.3.1" -c (Join-Path $ProjectRoot "constraints\dots-tts-recommended.txt")

if (-not (Test-Path -LiteralPath $MuseTalkRoot)) {
    git clone https://github.com/TMElyralab/MuseTalk.git $MuseTalkRoot
}
$ActualRemote = git -C $MuseTalkRoot remote get-url origin
if ($ActualRemote -notmatch "TMElyralab/MuseTalk") {
    throw "external/MuseTalk 指向非官方仓库：$ActualRemote"
}
git -C $MuseTalkRoot fetch origin
git -C $MuseTalkRoot checkout $MuseTalkCommit

conda run -p $MuseTalkPrefix pip install torch==2.0.1 torchvision==0.15.2 torchaudio==2.0.2 --index-url https://download.pytorch.org/whl/cu118
conda run -p $MuseTalkPrefix pip install -r (Join-Path $MuseTalkRoot "requirements.txt")
conda run -p $MuseTalkPrefix pip install --no-cache-dir -U openmim
conda run -p $MuseTalkPrefix mim install mmengine
conda run -p $MuseTalkPrefix mim install "mmcv==2.0.1"
conda run -p $MuseTalkPrefix mim install "mmdet==3.1.0"
conda run -p $MuseTalkPrefix mim install "mmpose==1.1.0"

Write-Host "Conda 环境已安装到 $EnvRoot"
Write-Host "下一步运行 scripts\download_models.ps1"
