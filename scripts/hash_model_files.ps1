param(
    [Parameter(Mandatory = $false)]
    [string]$MuseTalkRoot = "external/MuseTalk/models",

    [Parameter(Mandatory = $false)]
    [string]$DotsRoot = "models"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

$targets = @()
if (Test-Path -LiteralPath $MuseTalkRoot) {
    $targets += Get-ChildItem -LiteralPath $MuseTalkRoot -File -Recurse
}
if (Test-Path -LiteralPath $DotsRoot) {
    $targets += Get-ChildItem -LiteralPath $DotsRoot -File -Recurse
}

if ($targets.Count -eq 0) {
    throw "没有找到模型文件。请先下载权重，或传入正确的 -MuseTalkRoot/-DotsRoot。"
}

$targets |
    Sort-Object FullName |
    ForEach-Object {
        $hash = Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256
        [PSCustomObject]@{
            Path = $_.FullName
            SHA256 = $hash.Hash.ToLowerInvariant()
            Bytes = $_.Length
        }
    } |
    ConvertTo-Json -Depth 3 |
    Set-Content -LiteralPath (Join-Path $ProjectRoot "model-checksums.json") -Encoding UTF8

Write-Host "已写入 $ProjectRoot\model-checksums.json"
