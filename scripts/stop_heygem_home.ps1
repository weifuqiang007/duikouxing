param()

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$ComposeFile = Join-Path $ProjectRoot "deploy\heygem\docker-compose.yml"

docker compose -f $ComposeFile down
if ($LASTEXITCODE -ne 0) { throw "docker compose down 失败" }
Write-Host "HeyGem 服务已停止（共享目录 runtime\heygem 未改动）"
