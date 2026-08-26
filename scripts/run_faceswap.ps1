<#
.SYNOPSIS
    一键运行 FaceFusion 换脸任务。
.DESCRIPTION
    设置 F 盘环境变量，从仓库根目录调用 FaceFusion headless-run。
    用法: .\scripts\run_faceswap.ps1 -SourceImage 11.png -TargetVideo wlh.mp4
.PARAMETER SourceImage
    来源人脸照片路径。
.PARAMETER TargetVideo
    目标视频路径。
.PARAMETER OutputVideo
    输出视频路径（默认 F:\duikouxing-runtime\faceswap\outputs\<job_id>.mp4）。
.PARAMETER JobId
    任务 ID（默认 fs-<时间戳>）。
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$SourceImage,
    [Parameter(Mandatory)]
    [string]$TargetVideo,
    [string]$OutputVideo,
    [string]$JobId = ("fs-" + (Get-Date -Format 'yyyyMMdd-HHmmss')),
    [string]$RuntimeRoot = 'F:\duikouxing-runtime\faceswap'
)

$ErrorActionPreference = 'Stop'

# ── 环境变量 ──
$env:PIP_CACHE_DIR       = "$RuntimeRoot\cache\pip"
$env:HF_HOME             = "$RuntimeRoot\cache\huggingface"
$env:HF_HUB_CACHE        = "$RuntimeRoot\cache\huggingface\hub"
$env:TORCH_HOME          = "$RuntimeRoot\cache\torch"
$env:XDG_CACHE_HOME      = "$RuntimeRoot\cache\xdg"
$env:CUDA_CACHE_PATH     = "$RuntimeRoot\cache\cuda"
$env:PYTHONPYCACHEPREFIX = "$RuntimeRoot\cache\pycache"
$env:TEMP                = "$RuntimeRoot\temp"
$env:TMP                 = "$RuntimeRoot\temp"

$RepoDir   = "$RuntimeRoot\repos\facefusion"
$Python    = "$RuntimeRoot\envs\facefusion-3.8.2\python.exe"
$JobsDir  = "$RuntimeRoot\jobs"
$TempDir  = "$RuntimeRoot\temp"

# ── 准备任务目录 ──
$JobInputDir = "$JobsDir\$JobId\input"
$JobInputDir | ForEach-Object { New-Item -ItemType Directory -Force -Path $_ | Out-Null }

Copy-Item -Path $SourceImage -Destination "$JobInputDir\$(Split-Path $SourceImage -Leaf)"
Copy-Item -Path $TargetVideo -Destination "$JobInputDir\$(Split-Path $TargetVideo -Leaf)"

if (-not $OutputVideo) {
    $OutputVideo = "$RuntimeRoot\outputs\$JobId.mp4"
}

$SrcName   = Split-Path $SourceImage -Leaf
$TgtName   = Split-Path $TargetVideo -Leaf
$SrcInJob  = "$JobInputDir\$SrcName"
$TgtInJob  = "$JobInputDir\$TgtName"

Write-Host "[INFO] Job: $JobId"
Write-Host "[INFO] Source: $SrcInJob"
Write-Host "[INFO] Target: $TgtInJob"
Write-Host "[INFO] Output: $OutputVideo"
Write-Host ""

# ── 执行 FaceFusion ──
$Args = @(
    "$RepoDir\facefusion.py",
    'headless-run',
    '--source-paths', $SrcInJob,
    '--target-path', $TgtInJob,
    '--output-path', $OutputVideo,
    '--temp-path', $TempDir,
    '--jobs-path', $JobsDir,
    '--processors', 'face_swapper', 'expression_restorer',
    '--face-selector-mode', 'one',
    '--face-swapper-model', 'ghost_2_256',
    '--face-swapper-pixel-boost', '512x512',
    '--face-swapper-weight', '0.85',
    '--expression-restorer-model', 'live_portrait',
    '--expression-restorer-factor', '80',
    '--expression-restorer-areas', 'upper-face', 'lower-face',
    '--face-mask-types', 'box', 'occlusion', 'region',
    '--face-occluder-model', 'xseg_2',
    '--face-parser-model', 'bisenet_resnet_34',
    '--face-mask-regions', 'skin', 'left-eyebrow', 'right-eyebrow',
        'left-eye', 'right-eye', 'nose', 'mouth', 'upper-lip', 'lower-lip',
    '--face-mask-blur', '0.30',
    '--execution-providers', 'cuda',
    '--output-video-encoder', 'libx264',
    '--output-video-quality', '95',
    '--output-video-preset', 'slow',
    '--log-level', 'info'
)

Push-Location $RepoDir
try {
    & $Python @Args
    if ($LASTEXITCODE -ne 0) {
        Write-Error "FaceFusion 退出码: $LASTEXITCODE"
        exit $LASTEXITCODE
    }
    if (Test-Path $OutputVideo) {
        $Size = [math]::Round((Get-Item $OutputVideo).Length / 1MB, 1)
        Write-Host ""
        Write-Host "[DONE] 输出: $OutputVideo ($Size MB)" -ForegroundColor Green
    } else {
        Write-Error "输出文件未生成: $OutputVideo"
        exit 1
    }
} finally {
    Pop-Location
}
