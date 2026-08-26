<#
.SYNOPSIS
    FaceFusion 换脸流水线 — F 盘运行时目录与环境变量初始化。
.DESCRIPTION
    创建 F:\duikouxing-runtime\faceswap 全部子目录，
    设置 Conda/HuggingFace/Torch/PIP/Temp 等环境变量指向 F 盘，
    确保运行时产物不落入 C 盘。
.NOTES
    每次打开新终端后、运行换脸任务前都应执行此脚本。
    代码仓库位于 G 盘，不受此脚本管理。
#>

[CmdletBinding()]
param(
    [string]$RuntimeRoot = 'F:\duikouxing-runtime\faceswap'
)

$ErrorActionPreference = 'Stop'

# ── 1. 目录创建 ──────────────────────────────────────────────

$Directories = @(
    $RuntimeRoot,
    "$RuntimeRoot\envs",
    "$RuntimeRoot\conda-pkgs",
    "$RuntimeRoot\repos",
    "$RuntimeRoot\cache\pip",
    "$RuntimeRoot\cache\huggingface",
    "$RuntimeRoot\cache\torch",
    "$RuntimeRoot\cache\xdg",
    "$RuntimeRoot\cache\cuda",
    "$RuntimeRoot\cache\pycache",
    "$RuntimeRoot\temp",
    "$RuntimeRoot\jobs",
    "$RuntimeRoot\outputs",
    "$RuntimeRoot\samples",
    "$RuntimeRoot\logs",
    "$RuntimeRoot\licenses"
)

foreach ($Dir in $Directories) {
    $created = New-Item -ItemType Directory -Force -Path $Dir
    Write-Host "[OK] $($created.FullName)"
}

# ── 2. 环境变量 ──────────────────────────────────────────────

$EnvVars = @{
    'FACE_SWAP_RUNTIME_ROOT' = $RuntimeRoot
    'CONDA_ENVS_PATH'         = "$RuntimeRoot\envs"
    'CONDA_PKGS_DIRS'         = "$RuntimeRoot\conda-pkgs"
    'PIP_CACHE_DIR'           = "$RuntimeRoot\cache\pip"
    'HF_HOME'                 = "$RuntimeRoot\cache\huggingface"
    'HF_HUB_CACHE'            = "$RuntimeRoot\cache\huggingface\hub"
    'TORCH_HOME'              = "$RuntimeRoot\cache\torch"
    'XDG_CACHE_HOME'          = "$RuntimeRoot\cache\xdg"
    'CUDA_CACHE_PATH'         = "$RuntimeRoot\cache\cuda"
    'PYTHONPYCACHEPREFIX'     = "$RuntimeRoot\cache\pycache"
    'TEMP'                    = "$RuntimeRoot\temp"
    'TMP'                     = "$RuntimeRoot\temp"
}

foreach ($Name in $EnvVars.Keys) {
    Set-Item -Path "Env:$Name" -Value $EnvVars[$Name]
    Write-Host "[ENV] $Name = $($EnvVars[$Name])"
}

# ── 3. 磁盘余量检查 ──────────────────────────────────────────

$DriveInfo = Get-PSDrive -Name ($RuntimeRoot -creplace '^([A-Z]):.*', '$1')
$FreeGB = [math]::Round($DriveInfo.Free / 1GB, 1)
Write-Host ""
Write-Host "[INFO] $($DriveInfo.Name): 盘剩余 ${FreeGB} GB"

if ($FreeGB -lt 50) {
    Write-Warning "磁盘剩余不足 50 GB，模型下载和视频处理可能失败。"
}

# ── 4. C 盘缓存警告 ──────────────────────────────────────────

$CCachePaths = @(
    "$env:USERPROFILE\.cache\huggingface",
    "$env:USERPROFILE\.cache\torch",
    "$env:LOCALAPPDATA\Temp"
)

Write-Host ""
Write-Host "[INFO] 检查 C 盘缓存目录体积..."
foreach ($P in $CCachePaths) {
    if (Test-Path $P) {
        $size = (Get-ChildItem -Recurse -File -ErrorAction SilentlyContinue $P | Measure-Object -Property Length -Sum).Sum
        $sizeGB = [math]::Round($size / 1GB, 2)
        if ($sizeGB -gt 0.1) {
            Write-Warning "C 盘缓存 $P 占用 ${sizeGB} GB — 换脸任务不会使用此路径，但磁盘空间可能紧张。"
        }
    }
}

Write-Host ""
Write-Host "[DONE] FaceFusion 运行时环境就绪。"
