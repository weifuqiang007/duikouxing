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
$env:DIGITAL_HUMAN_PROFILE = $Profile
$env:HF_HOME = Join-Path $ProjectRoot ".cache\huggingface"
$env:HF_HUB_CACHE = Join-Path $env:HF_HOME "hub"
$env:TORCH_HOME = Join-Path $ProjectRoot ".cache\torch"
$env:PIP_CACHE_DIR = Join-Path $ProjectRoot ".cache\pip"
$env:TEMP = Join-Path $ProjectRoot ".tmp"
$env:TMP = $env:TEMP

$Arguments = @(
    "run", "-p", $OrchestratorPrefix,
    "python", "-m", "digital_human.cli",
    "prepare-driving", "--profile", $Profile,
    "--job", ([System.IO.Path]::GetFullPath($Job))
)
if ($Force) { $Arguments += "--force" }
& conda @Arguments
if ($LASTEXITCODE -ne 0) { throw "导读生成失败，退出码 $LASTEXITCODE" }
