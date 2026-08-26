<#
.SYNOPSIS
    FaceFusion 换脸环境体检脚本。
.DESCRIPTION
    检查 FaceFusion 版本、CUDA 执行提供者、FFmpeg、
    目录结构、磁盘余量。所有检查通过后才允许跑正式任务。
.NOTES
    需先执行 setup_faceswap_home.ps1 设置环境变量。
#>

[CmdletBinding()]
param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot '..\config\face-swap.local.home.yaml')
)

$ErrorActionPreference = 'Stop'

# ── 加载配置 ──────────────────────────────────────────────

if (-not (Test-Path $ConfigPath)) {
    Write-Error "配置文件不存在: $ConfigPath"
    Write-Error "请先创建配置文件，参考 config/face-swap.job.example.yaml"
    exit 1
}

$Yaml = Get-Content $ConfigPath -Raw
# 简易 YAML 提取（不引入额外依赖）
function Extract-YamlValue($Pattern, $Content) {
    if ($Content -match "\s*$Pattern[:\s]+['\"]?([^'\"\n]+)['\"]?") {
        return $Matches[1].Trim()
    }
    return $null
}

$RepoPath      = Extract-YamlValue 'facefusion_repo' $Yaml
$PythonPath    = Extract-YamlValue 'facefusion_python' $Yaml
$ExpectedCommit = '4b1dedb853e4838ca7f3cf70b572be241aee2497'

$Checks = @()

# ── 1. 目录与文件 ──────────────────────────────────────────

Write-Host "[1/6] 检查目录与文件..."

$RootDir = Extract-YamlValue 'root' $Yaml
if (-not $RootDir -or -not (Test-Path $RootDir)) {
    $Checks += [PSCustomObject]@{ Name='运行时根目录'; Status='FAIL'; Detail="不存在: $RootDir" }
} else {
    $Checks += [PSCustomObject]@{ Name='运行时根目录'; Status='OK'; Detail=$RootDir }
}

if (-not $RepoPath -or -not (Test-Path (Join-Path $RepoPath 'facefusion.py'))) {
    $Checks += [PSCustomObject]@{ Name='FaceFusion 仓库'; Status='FAIL'; Detail="facefusion.py 不存在于 $RepoPath" }
} else {
    $Checks += [PSCustomObject]@{ Name='FaceFusion 仓库'; Status='OK'; Detail=$RepoPath }
}

if (-not $PythonPath -or -not (Test-Path $PythonPath)) {
    $Checks += [PSCustomObject]@{ Name='Python 解释器'; Status='FAIL'; Detail="不存在: $PythonPath" }
} else {
    $Checks += [PSCustomObject]@{ Name='Python 解释器'; Status='OK'; Detail=$PythonPath }
}

# ── 2. Git 版本 ────────────────────────────────────────────

Write-Host "[2/6] 检查 FaceFusion 版本..."

if (Test-Path (Join-Path $RepoPath '.git')) {
    $Commit = & git -C $RepoPath rev-parse HEAD 2>&1
    $Tag = & git -C $RepoPath describe --tags --exact-match 2>&1
    if ($Commit -eq $ExpectedCommit) {
        $Checks += [PSCustomObject]@{ Name='FaceFusion Commit'; Status='OK'; Detail="$Commit ($Tag)" }
    } else {
        $Checks += [PSCustomObject]@{ Name='FaceFusion Commit'; Status='FAIL'; Detail="期望 $ExpectedCommit，实际 $Commit" }
    }
} else {
    $Checks += [PSCustomObject]@{ Name='FaceFusion Commit'; Status='SKIP'; Detail='不是 Git 仓库' }
}

# ── 3. CUDA 执行提供者 ──────────────────────────────────────

Write-Host "[3/6] 检查 ONNX Runtime CUDA..."

if (Test-Path $PythonPath) {
    $PyCheck = & $PythonPath -c "import onnxruntime as o; print('\n'.join(o.get_available_providers()))" 2>&1
    if ($LASTEXITCODE -ne 0) {
        $Checks += [PSCustomObject]@{ Name='ONNX Runtime CUDA'; Status='FAIL'; Detail="导入失败: $PyCheck" }
    } elseif ($PyCheck -match 'CUDAExecutionProvider') {
        $Checks += [PSCustomObject]@{ Name='ONNX Runtime CUDA'; Status='OK'; Detail=$PyCheck.Trim() }
    } else {
        $Checks += [PSCustomObject]@{ Name='ONNX Runtime CUDA'; Status='FAIL'; Detail="未检测到 CUDAExecutionProvider。可用: $PyCheck" }
    }
} else {
    $Checks += [PSCustomObject]@{ Name='ONNX Runtime CUDA'; Status='SKIP'; Detail='Python 不存在' }
}

# ── 4. FFmpeg ──────────────────────────────────────────────

Write-Host "[4/6] 检查 FFmpeg..."

$Ffprobe = Get-Command ffprobe -ErrorAction SilentlyContinue
if ($Ffprobe) {
    $FfVersion = & ffprobe -version 2>&1 | Select-Object -First 1
    $Checks += [PSCustomObject]@{ Name='FFmpeg'; Status='OK'; Detail=$FfVersion }
} else {
    $Checks += [PSCustomObject]@{ Name='FFmpeg'; Status='FAIL'; Detail='ffprobe 不在 PATH 中' }
}

# ── 5. GPU ──────────────────────────────────────────────────

Write-Host "[5/6] 检查 GPU..."

$Smi = nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>&1
if ($LASTEXITCODE -eq 0 -and $Smi) {
    $Checks += [PSCustomObject]@{ Name='GPU'; Status='OK'; Detail=$Smi.Trim() }
} else {
    $Checks += [PSCustomObject]@{ Name='GPU'; Status='FAIL'; Detail='nvidia-smi 不可用' }
}

# ── 6. 磁盘余量 ─────────────────────────────────────────────

Write-Host "[6/6] 检查磁盘..."

if ($RootDir) {
    $DriveLetter = ($RootDir -creplace '^([A-Z]):.*', '$1')
    $Drive = Get-PSDrive -Name $DriveLetter -ErrorAction SilentlyContinue
    if ($Drive) {
        $FreeGB = [math]::Round($Drive.Free / 1GB, 1)
        if ($FreeGB -ge 50) {
            $Checks += [PSCustomObject]@{ Name='磁盘余量'; Status='OK'; Detail="${DriveLetter}: ${FreeGB} GB 可用" }
        } else {
            $Checks += [PSCustomObject]@{ Name='磁盘余量'; Status='WARN'; Detail="${DriveLetter}: 仅 ${FreeGB} GB 可用，建议 >= 50 GB" }
        }
    }
}

# ── 报告 ────────────────────────────────────────────────────

Write-Host ""
Write-Host "$('=' * 60)"
Write-Host "  FaceFusion 环境体检报告"
Write-Host "$('=' * 60)"

foreach ($C in $Checks) {
    $Icon = switch ($C.Status) {
        'OK'   { '[PASS]' }
        'WARN' { '[WARN]' }
        'FAIL' { '[FAIL]' }
        'SKIP' { '[SKIP]' }
    }
    $Color = switch ($C.Status) {
        'OK'   { 'Green' }
        'WARN' { 'Yellow' }
        'FAIL' { 'Red' }
        default { 'Gray' }
    }
    Write-Host "  $Icon $($C.Name)" -ForegroundColor $Color
    Write-Host "         $($C.Detail)" -ForegroundColor Gray
}

$FailCount = ($Checks | Where-Object { $_.Status -eq 'FAIL' }).Count
Write-Host ""
if ($FailCount -gt 0) {
    Write-Host "[RESULT] $FailCount 项检查失败，禁止运行正式任务。" -ForegroundColor Red
    exit 1
} else {
    Write-Host "[RESULT] 全部通过，环境就绪。" -ForegroundColor Green
    exit 0
}