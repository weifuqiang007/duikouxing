# 换脸功能 — 家里电脑首次搭建指南

> 电脑要求：Windows 11、NVIDIA RTX 4070 12GB、F 盘 ≥ 500GB 可用
> 预计耗时：30-60 分钟（取决于网速和模型下载）

---

## 第一步：拉代码

```powershell
# 打开 PowerShell
G:
cd G:\duikouxing

# 新建 worktree（不影响现有工作区）
git worktree add -b codex/faceswap-facefusion-local G:\duikouxing-faceswap codex/faceswap-facefusion-local

# 如果分支已存在：
# git worktree add G:\duikouxing-faceswap codex/faceswap-facefusion-local
```

## 第二步：创建 F 盘目录

```powershell
# 创建全部运行时目录
F:\duikouxing-runtime\faceswap\scripts\setup_faceswap_home.ps1
```

如果家里电脑还没有这个脚本（worktree 刚拉下来），手动创建：

```powershell
$dirs = @(
  'F:\duikouxing-runtime\faceswap',
  'F:\duikouxing-runtime\faceswap\envs',
  'F:\duikouxing-runtime\faceswap\conda-pkgs',
  'F:\duikouxing-runtime\faceswap\repos',
  'F:\duikouxing-runtime\faceswap\cache\pip',
  'F:\duikouxing-runtime\faceswap\cache\huggingface',
  'F:\duikouxing-runtime\faceswap\cache\torch',
  'F:\duikouxing-runtime\faceswap\cache\xdg',
  'F:\duikouxing-runtime\faceswap\cache\cuda',
  'F:\duikouxing-runtime\faceswap\cache\pycache',
  'F:\duikouxing-runtime\faceswap\temp',
  'F:\duikouxing-runtime\faceswap\jobs',
  'F:\duikouxing-runtime\faceswap\outputs',
  'F:\duikouxing-runtime\faceswap\samples',
  'F:\duikouxing-runtime\faceswap\logs',
  'F:\duikouxing-runtime\faceswap\licenses'
)
$dirs | ForEach-Object { New-Item -ItemType Directory -Force -Path $_ | Out-Null; Write-Host "Created: $_" }
```

## 第三步：设置环境变量

```powershell
$RuntimeRoot = 'F:\duikouxing-runtime\faceswap'

$env:FACE_SWAP_RUNTIME_ROOT = $RuntimeRoot
$env:CONDA_ENVS_PATH         = "$RuntimeRoot\envs"
$env:CONDA_PKGS_DIRS         = "$RuntimeRoot\conda-pkgs"
$env:PIP_CACHE_DIR           = "$RuntimeRoot\cache\pip"
$env:HF_HOME                 = "$RuntimeRoot\cache\huggingface"
$env:HF_HUB_CACHE            = "$RuntimeRoot\cache\huggingface\hub"
$env:TORCH_HOME              = "$RuntimeRoot\cache\torch"
$env:XDG_CACHE_HOME          = "$RuntimeRoot\cache\xdg"
$env:CUDA_CACHE_PATH         = "$RuntimeRoot\cache\cuda"
$env:PYTHONPYCACHEPREFIX     = "$RuntimeRoot\cache\pycache"
$env:TEMP                    = "$RuntimeRoot\temp"
$env:TMP                     = "$RuntimeRoot\temp"
```

> ⚠️ 每次打开新 PowerShell 窗口都要重新执行这步，或者把上面的内容存成 `.ps1` 脚本每次运行。

## 第四步：安装 FaceFusion

```powershell
# 4a. 克隆 FaceFusion 源码（固定 3.8.2 版本）
git clone --branch 3.8.2 --single-branch --depth 1 https://github.com/facefusion/facefusion.git F:\duikouxing-runtime\faceswap\repos\facefusion

# 4b. 校验 commit（必须输出 4b1dedb853e4838ca7f3cf70b572be241aee2497）
git -C F:\duikouxing-runtime\faceswap\repos\facefusion rev-parse HEAD

# 4c. 创建 Python 环境
conda create --prefix F:\duikouxing-runtime\faceswap\envs\facefusion-3.8.2 -y python=3.12 pip

# 4d. 安装 CUDA 12 依赖（这一步耗时较长）
conda run --prefix F:\duikouxing-runtime\faceswap\envs\facefusion-3.8.2 --cwd F:\duikouxing-runtime\faceswap\repos\facefusion python install.py cuda@12

# 4e. 验证 CUDA（必须包含 CUDAExecutionProvider）
conda run --prefix F:\duikouxing-runtime\faceswap\envs\facefusion-3.8.2 python -c "import onnxruntime as o; print(o.get_available_providers())"
```

## 第五步：跑换脸测试

```powershell
# 准备素材：把 11.png 和 wlh.mp4 放到某个位置，比如 G:\duikouxing\samples\

# 一键运行（第一次会自动下载模型权重，约 1-2 GB）
cd G:\duikouxing-faceswap
.\scripts\run_faceswap.ps1 `
  -SourceImage G:\duikouxing\samples\11.png `
  -TargetVideo G:\duikouxing\samples\wlh.mp4
```

输出视频在 `F:\duikouxing-runtime\faceswap\outputs\` 下。

## 第六步：验证结果

```powershell
# 检查输出文件
ls F:\duikouxing-runtime\faceswap\outputs\*.mp4

# 用系统播放器打开看效果
start F:\duikouxing-runtime\faceswap\outputs\fs-*.mp4
```

重点检查：
- [ ] 人脸是否变成了 11.png 里的那个人
- [ ] 嘴巴运动是否和原视频一致
- [ ] 身体、衣服、背景有没有被改变
- [ ] 有没有音频
- [ ] 有没有"双嘴"或边缘贴纸感

## 跑单元测试（可选）

```powershell
cd G:\duikouxing-faceswap
python -m pytest tests/test_face_swap_paths.py tests/test_face_swap_license_gate.py tests/test_facefusion_adapter.py -v
```

应该 24 passed。

## 出问题了怎么办

| 症状 | 可能原因 | 解决 |
|---|---|---|
| `conda: command not found` | 没装 Conda | 安装 Miniforge 或 Anaconda |
| `CUDAExecutionProvider` 不在列表 | CUDA 驱动太旧 | 更新 NVIDIA 驱动 |
| `headless-run: error: --processors: invalid choice` | 没有从仓库根目录运行 | `run_faceswap.ps1` 已自动处理 |
| 模型下载很慢 | 公司网络限速 | 换家里网络或手机热点 |
| `face not detected` | 照片太模糊或多脸 | 换一张清晰正脸照 |

## 公司网络补充：代理设置

如果家里或公司网络直连 GitHub/HuggingFace 很慢，加 `-Proxy` 参数：

```powershell
.\scripts\run_faceswap.ps1 `
  -SourceImage G:\duikouxing\samples\11.png `
  -TargetVideo G:\duikouxing\samples\wlh.mp4 `
  -Proxy http://127.0.0.1:7890
```

代理地址看你用的工具（Clash 默认 7890，V2Ray 可能不同）。
