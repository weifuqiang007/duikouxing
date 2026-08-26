"""C 盘零落盘预检 — 确保所有运行时路径在指定根目录下且不在 C 盘。"""

from __future__ import annotations

from pathlib import Path


class UnsafeRuntimePath(ValueError):
    """运行时路径落在 C 盘或逃逸根目录时抛出。"""


def require_under_runtime(path: Path, runtime_root: Path) -> Path:
    """验证 *path* 解析后位于 *runtime_root* 下且不在 C 盘。

    Returns:
        解析后的绝对路径。

    Raises:
        UnsafeRuntimePath: 路径在 C 盘或不在 runtime_root 下。
    """
    resolved = path.expanduser().resolve(strict=False)
    root = runtime_root.expanduser().resolve(strict=False)

    if resolved.drive.casefold() == "c:":
        raise UnsafeRuntimePath(f"运行时路径禁止位于 C 盘: {resolved}")

    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise UnsafeRuntimePath(
            f"运行时路径必须位于 {root} 之下，当前为 {resolved}"
        ) from exc

    return resolved
