param()

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$ComposeFile = Join-Path $ProjectRoot "deploy\heygem\docker-compose.yml"

docker compose -f $ComposeFile up -d
if ($LASTEXITCODE -ne 0) { throw "docker compose up 失败" }

Write-Host "==> 等待服务监听 127.0.0.1:8383"
$ready = $false
for ($i = 0; $i -lt 24; $i++) {
    Start-Sleep -Seconds 5
    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:8383/easy/query?code=startup-probe" -TimeoutSec 3
        $ready = $true
        break
    } catch { }
}
if (-not $ready) { throw "服务 60~120 秒内未就绪，请查看: docker logs heygem-gen-video" }

Write-Host "==> 健康检查"
docker inspect -f 'container={{.State.Status}} restarts={{.RestartCount}}' heygem-gen-video
docker exec heygem-gen-video nvidia-smi --query-gpu=name --format=csv,noheader
Write-Host "API 就绪。停止服务: .\scripts\stop_heygem_home.ps1"
