param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("office", "home")]
    [string]$Profile,

    [Parameter(Mandatory = $true)]
    [string]$Job,

    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$OrchestratorPrefix = Join-Path $ProjectRoot ".conda-envs\digital-human"
$CacheRoot = Join-Path $ProjectRoot ".cache"
$TempRoot = Join-Path $ProjectRoot ".tmp"

if (-not (Test-Path -LiteralPath $OrchestratorPrefix)) {
    throw "缺少编排环境，请先运行 scripts\setup_conda.ps1"
}

$env:DIGITAL_HUMAN_PROFILE = $Profile
$env:HF_HOME = Join-Path $CacheRoot "huggingface"
$env:HF_HUB_CACHE = Join-Path $env:HF_HOME "hub"
$env:TORCH_HOME = Join-Path $CacheRoot "torch"
$env:PIP_CACHE_DIR = Join-Path $CacheRoot "pip"
$env:XDG_CACHE_HOME = $CacheRoot
$env:TEMP = $TempRoot
$env:TMP = $TempRoot

$Arguments = @(
    "run", "-p", $OrchestratorPrefix,
    "python", "-m", "digital_human.cli",
    "run", "--profile", $Profile,
    "--job", ([System.IO.Path]::GetFullPath($Job))
)
if ($Force) { $Arguments += "--force" }

& conda @Arguments
if ($LASTEXITCODE -ne 0) { throw "任务运行失败，退出码 $LASTEXITCODE" }

