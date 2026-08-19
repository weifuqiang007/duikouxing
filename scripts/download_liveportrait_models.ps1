param()

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$LivePortraitPrefix = Join-Path $ProjectRoot ".conda-envs\liveportrait"
$LivePortraitRoot = Join-Path $ProjectRoot "external\LivePortrait"
$WeightsRoot = Join-Path $LivePortraitRoot "pretrained_weights"
$CacheRoot = Join-Path $ProjectRoot ".cache"
$TempRoot = Join-Path $ProjectRoot ".tmp"

if ($ProjectRoot -match "^C:\\") { throw "项目不能位于 C 盘：$ProjectRoot" }
if (-not (Test-Path -LiteralPath $LivePortraitPrefix)) { throw "请先运行 setup_liveportrait.ps1" }
if (-not (Test-Path -LiteralPath $LivePortraitRoot)) { throw "缺少 external\LivePortrait" }

if (-not $env:HF_ENDPOINT) { $env:HF_ENDPOINT = "https://hf-mirror.com" }
if ($env:HF_ENDPOINT -match "hf-mirror") {
    $env:NO_PROXY = (@($env:NO_PROXY, "hf-mirror.com") | Where-Object { $_ }) -join ","
    $env:no_proxy = $env:NO_PROXY
}
$env:HF_HOME = Join-Path $CacheRoot "huggingface"
$env:HF_HUB_CACHE = Join-Path $env:HF_HOME "hub"
$env:TORCH_HOME = Join-Path $CacheRoot "torch"
$env:PIP_CACHE_DIR = Join-Path $CacheRoot "pip"
$env:XDG_CACHE_HOME = $CacheRoot
$env:TEMP = $TempRoot
$env:TMP = $TempRoot
New-Item -ItemType Directory -Force -Path $WeightsRoot, $CacheRoot, $TempRoot | Out-Null

Write-Host "==> 下载 KlingTeam/LivePortrait 官方权重到 $WeightsRoot"
conda run -p $LivePortraitPrefix hf download KlingTeam/LivePortrait `
    --local-dir $WeightsRoot `
    --exclude "*.git*" "README.md" "docs"
if ($LASTEXITCODE -ne 0) { throw "LivePortrait 权重下载失败，退出码 $LASTEXITCODE" }

$Required = @(
    "liveportrait\base_models\appearance_feature_extractor.pth",
    "liveportrait\base_models\motion_extractor.pth",
    "liveportrait\base_models\spade_generator.pth",
    "liveportrait\base_models\warping_module.pth",
    "liveportrait\retargeting_models\stitching_retargeting_module.pth",
    "liveportrait\landmark.onnx",
    "insightface\models\buffalo_l\det_10g.onnx"
)
foreach ($Relative in $Required) {
    $Path = Join-Path $WeightsRoot $Relative
    if (-not (Test-Path -LiteralPath $Path)) { throw "下载完成但缺少必要权重：$Path" }
}
Write-Host "LivePortrait 权重下载并校验完成。"
