param()

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$DotsPrefix = Join-Path $ProjectRoot ".conda-envs\dots-tts"
$ModelRoot = Join-Path $ProjectRoot "models\dots.tts-soar"
$CacheRoot = Join-Path $ProjectRoot ".cache"
if (-not (Test-Path -LiteralPath $DotsPrefix)) { throw "请先运行 scripts\setup_conda.ps1" }
if (-not $env:HF_ENDPOINT) { $env:HF_ENDPOINT = "https://hf-mirror.com" }
if ($env:HF_ENDPOINT -match "hf-mirror") {
    $env:NO_PROXY = (@($env:NO_PROXY, "hf-mirror.com") | Where-Object { $_ }) -join ","
    $env:no_proxy = $env:NO_PROXY
}
$env:HF_HOME = Join-Path $CacheRoot "huggingface"
$env:HF_HUB_CACHE = Join-Path $env:HF_HOME "hub"
$env:TORCH_HOME = Join-Path $CacheRoot "torch"
$env:PIP_CACHE_DIR = Join-Path $CacheRoot "pip"
$env:TEMP = Join-Path $ProjectRoot ".tmp"
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $ModelRoot, $CacheRoot, $env:TEMP | Out-Null
conda run -p $DotsPrefix hf download dots-studio/dots.tts-soar --local-dir $ModelRoot
if ($LASTEXITCODE -ne 0) { throw "dots.tts-soar 下载失败，退出码 $LASTEXITCODE" }
Write-Host "客户音色模型下载完成：$ModelRoot"
