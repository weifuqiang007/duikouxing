"""LivePortrait 官方 inference.py 的启动包装（仅标准库，运行在 liveportrait 环境）。

用法：python liveportrait_runner.py <inference.py 路径> [原样传递的参数...]

本机多线程 libx264 编码存在间歇性段错误（imageio 自带 ffmpeg 4.2.2 与系统
ffmpeg 7.0.2 均会触发，20 核心 40 线程 CPU），LivePortrait 通过 imageio 写
mp4 时无法指定线程数，这里在 imageio.get_writer 里强制注入 -threads 1。
不打补丁到 external/LivePortrait 仓库本身，避免固定提交的 checkout 覆盖。
"""

import runpy
import sys
from pathlib import Path


def _force_single_thread_encode() -> None:
    import imageio

    def patched(get_writer):
        def wrapper(*args, **kwargs):
            params = list(kwargs.get("ffmpeg_params") or [])
            if "-threads" not in params:
                params += ["-threads", "1"]
            kwargs["ffmpeg_params"] = params
            return get_writer(*args, **kwargs)

        return wrapper

    for namespace in (imageio, getattr(imageio, "v2", None), getattr(imageio, "v3", None)):
        if namespace is not None and hasattr(namespace, "get_writer"):
            namespace.get_writer = patched(namespace.get_writer)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("用法: python liveportrait_runner.py <inference.py> [参数...]")
    _force_single_thread_encode()
    script = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(script.parent))  # inference.py 以 `from src...` 相对仓库导入
    sys.argv = [str(script), *sys.argv[2:]]
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
