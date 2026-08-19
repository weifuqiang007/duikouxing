"""把 pip 安装的 NVIDIA CUDA/cuDNN DLL 目录注册进本进程的 DLL 搜索路径。

onnxruntime-gpu 的 CUDA 后端用 LOAD_LIBRARY_SEARCH_DEFAULT_DIRS 加载
CUDA 依赖，不搜索 PATH；必须在进程内 os.add_dll_directory 才能找到
nvidia-cudnn-cu12 / nvidia-cublas-cu12 / torch 自带的 CUDA 库。
由 setup_liveportrait.ps1 复制为 site-packages 下的 zz_cuda_dll_dirs.py，
配合同目录 zz_cuda_dll_dirs.pth（单行 import 语句）在解释器启动时自动执行。
"""

import os
import sys


def _register() -> None:
    base = os.path.join(sys.prefix, "Lib", "site-packages")
    candidates = []
    nvidia_root = os.path.join(base, "nvidia")
    if os.path.isdir(nvidia_root):
        for name in sorted(os.listdir(nvidia_root)):
            candidates.append(os.path.join(nvidia_root, name, "bin"))
    candidates.append(os.path.join(base, "torch", "lib"))
    for path in candidates:
        if os.path.isdir(path):
            try:
                os.add_dll_directory(path)
            except (OSError, FileNotFoundError):
                pass


try:
    _register()
except Exception:  # 启动钩子绝不能让解释器挂掉
    pass
