param()

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$SharedRoot = Join-Path $ProjectRoot "runtime\heygem\data\face2face"

Write-Host "==> 创建 HeyGem 共享目录 $SharedRoot"
New-Item -ItemType Directory -Force -Path (Join-Path $SharedRoot "temp") | Out-Null

Write-Host "==> 检查 Docker daemon"
docker info --format '{{.ServerVersion}}' | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Docker daemon 未运行；请先启动 Docker Desktop（本机位于 D:\tools\docker\docker\Docker Desktop.exe）"
}

Write-Host "==> 检查 HeyGem 镜像"
$imageId = docker images guiji2025/duix.avatar --format '{{.ID}}'
if (-not $imageId) {
    Write-Warning "缺少镜像 guiji2025/duix.avatar。本机网络无法直接 docker pull，"
    Write-Warning "请按 docs/HEYGEM_LOCAL_DEPLOYMENT.md §5.5 的分段下载 + docker load 流程获取。"
    exit 1
}
Write-Host "镜像 ID: $imageId"

Write-Host "==> 完成。下一步：.\scripts\start_heygem_home.ps1"
