param()

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$EnvRoot = Join-Path $ProjectRoot ".conda-envs"
$LivePortraitPrefix = Join-Path $EnvRoot "liveportrait"
$LivePortraitRoot = Join-Path $ProjectRoot "external\LivePortrait"
$CacheRoot = Join-Path $ProjectRoot ".cache"
$TempRoot = Join-Path $ProjectRoot ".tmp"
$LivePortraitCommit = "9b294b3d0536135442ea73cb01e6cb3ca7029dd3"

if ($ProjectRoot -match "^C:\\") {
    throw "为避免环境落到系统盘，本脚本要求项目根目录不在 C 盘；实际为 $ProjectRoot"
}

# 使用项目内 .condarc（国内镜像 + 项目内包缓存），不依赖也不修改用户全局 conda 配置。
$ProjectCondarc = Join-Path $ProjectRoot ".condarc"
$PkgsDir = Join-Path $ProjectRoot ".conda-pkgs"
if (-not (Test-Path -LiteralPath $ProjectCondarc)) {
    $CondarcContent = @"
channels:
  - defaults
default_channels:
  - https://mirror.nju.edu.cn/anaconda/pkgs/main
  - https://mirror.nju.edu.cn/anaconda/pkgs/r
show_channel_urls: true
pkgs_dirs:
  - $($PkgsDir.Replace('\', '/'))
"@
    Set-Content -LiteralPath $ProjectCondarc -Value $CondarcContent -Encoding ASCII
}
$env:CONDARC = $ProjectCondarc
# 频道显式指定并 --override-channels，避免用户全局 .condarc 中失效镜像污染解析。
$CondaChannel = "https://mirror.nju.edu.cn/anaconda/pkgs/main"

New-Item -ItemType Directory -Force -Path $EnvRoot, $CacheRoot, $TempRoot, $PkgsDir, (Join-Path $ProjectRoot "external") | Out-Null
$env:HF_HOME = Join-Path $CacheRoot "huggingface"
$env:HF_HUB_CACHE = Join-Path $env:HF_HOME "hub"
$env:TORCH_HOME = Join-Path $CacheRoot "torch"
$env:PIP_CACHE_DIR = Join-Path $CacheRoot "pip"
$env:XDG_CACHE_HOME = $CacheRoot
$env:TEMP = $TempRoot
$env:TMP = $TempRoot

function Invoke-Step {
    param([string]$Description, [scriptblock]$Action)
    Write-Host "==> $Description"
    & $Action
    if ($LASTEXITCODE -ne 0) { throw "$Description 失败，退出码 $LASTEXITCODE" }
}

if (-not (Test-Path -LiteralPath $LivePortraitPrefix)) {
    Invoke-Step "创建 LivePortrait Conda 环境 (Python 3.10.13)" {
        conda create -y -p $LivePortraitPrefix --override-channels -c $CondaChannel python=3.10.13 pip=24.2
    }
}

if (-not (Test-Path -LiteralPath $LivePortraitRoot)) {
    Invoke-Step "克隆 LivePortrait 官方仓库" {
        git clone https://github.com/KlingAIResearch/LivePortrait.git $LivePortraitRoot
    }
}
$Remote = git -C $LivePortraitRoot remote get-url origin
if ($Remote -notmatch "(KlingAIResearch|KwaiVGI|KlingTeam)/LivePortrait") {
    throw "external/LivePortrait 指向非官方仓库：$Remote"
}
Invoke-Step "同步并固定 LivePortrait 提交 $LivePortraitCommit" {
    git -C $LivePortraitRoot fetch origin
    git -C $LivePortraitRoot checkout $LivePortraitCommit
}
Invoke-Step "安装 PyTorch 2.3.0 CUDA 12.1" {
    conda run -p $LivePortraitPrefix pip install torch==2.3.0 torchvision==0.18.0 torchaudio==2.3.0 --index-url https://download.pytorch.org/whl/cu121
}
Invoke-Step "安装 LivePortrait 官方依赖" {
    conda run -p $LivePortraitPrefix pip install -r (Join-Path $LivePortraitRoot "requirements.txt")
}
Invoke-Step "安装权重下载工具" {
    conda run -p $LivePortraitPrefix pip install "huggingface-hub[cli]==0.36.0"
}
# 官方 requirements 钉的是 onnxruntime-gpu 1.18.0（CUDA 11.8 + cuDNN 8 构建），
# 与本机 CUDA 12 运行时不匹配；升级为 CUDA 12 + cuDNN 9 构建的 1.19.2，
# cuDNN 9 由 pip 包提供（不写入系统，卸载环境即清理）。
Invoke-Step "升级 onnxruntime-gpu 至 CUDA 12 构建并补齐 cuDNN 9" {
    conda run -p $LivePortraitPrefix pip install onnxruntime-gpu==1.19.2 nvidia-cudnn-cu12==9.1.0.70
}
# onnxruntime 加载 CUDA DLL 时不搜索 PATH，需要进程内 add_dll_directory；
# 借 site-packages 的 .pth 启动钩子注册（pip 不会管理这两个文件，须手动补装）。
Invoke-Step "安装 CUDA DLL 目录启动钩子 (.pth)" {
    $SitePackages = Join-Path $LivePortraitPrefix "Lib\site-packages"
    Copy-Item (Join-Path $PSScriptRoot "zz_cuda_dll_dirs.py") -Destination $SitePackages -Force
    Set-Content -LiteralPath (Join-Path $SitePackages "zz_cuda_dll_dirs.pth") -Value "import zz_cuda_dll_dirs" -Encoding ASCII
}

Write-Host "LivePortrait 环境已安装：$LivePortraitPrefix"
Write-Host "下一步运行 scripts\download_liveportrait_models.ps1"
