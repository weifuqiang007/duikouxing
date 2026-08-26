from pathlib import Path
from unittest.mock import patch

import pytest

from digital_human.face_swap_path_guard import UnsafeRuntimePath, require_under_runtime


# tmp_path 总在 C: 盘，而 require_under_runtime 会拒绝 C:。
# 所以“应该通过”的测试用显式 F: 盘路径；C: 盘拒绝测试直接构造 C: 路径。

_F_ROOT = Path(r"F:\duikouxing-runtime\faceswap")
_F_ENV = Path(r"F:\duikouxing-runtime\faceswap\envs\facefusion-3.8.2")
_F_CACHE = Path(r"F:\duikouxing-runtime\faceswap\cache\pip")
_F_DOTDOT = Path(r"F:\duikouxing-runtime\faceswap\subdir\..\other")
_F_ESCAPE = Path(r"F:\duikouxing-runtime\other_project\cache")


def _f_resolve(self: Path, strict: bool = False) -> Path:
    """Mock resolve：F: 盘不存在的路径直接返回自身（模拟 strict=False）。"""
    return Path(str(self).replace("/", "\\"))


class TestRequireUnderRuntime:
    """C 盘零落盘预检单元测试。"""

    @patch.object(Path, "resolve", _f_resolve)
    def test_path_under_root_passes(self) -> None:
        result = require_under_runtime(_F_ENV, _F_ROOT)
        assert "envs" in str(result)
        assert "facefusion-3.8.2" in str(result)

    def test_c_drive_path_rejected(self) -> None:
        with pytest.raises(UnsafeRuntimePath, match="C "):
            require_under_runtime(Path(r"C:\Users\someone\cache\huggingface"), _F_ROOT)

    def test_c_drive_temp_rejected(self) -> None:
        with pytest.raises(UnsafeRuntimePath, match="C "):
            require_under_runtime(Path(r"C:\temp\somefile"), _F_ROOT)

    @patch.object(Path, "resolve", _f_resolve)
    def test_escape_root_rejected(self) -> None:
        with pytest.raises(UnsafeRuntimePath, match="必须位于"):
            require_under_runtime(_F_ESCAPE, _F_ROOT)

    def test_user_home_expanded_and_rejected(self) -> None:
        """~ 路径展开后若落在 C: 盘也拒绝。"""
        with pytest.raises(UnsafeRuntimePath, match="C "):
            require_under_runtime(Path("~/.cache/huggingface"), _F_ROOT)

    @patch.object(Path, "resolve", _f_resolve)
    def test_dotdot_within_root_passes(self) -> None:
        result = require_under_runtime(_F_DOTDOT, _F_ROOT)
        assert "other" in str(result)

    @patch.object(Path, "resolve", _f_resolve)
    def test_returns_resolved_path(self) -> None:
        result = require_under_runtime(_F_CACHE, _F_ROOT)
        assert "cache" in str(result)
        assert "pip" in str(result)

    @patch.object(Path, "resolve", _f_resolve)
    def test_root_itself_passes(self) -> None:
        result = require_under_runtime(_F_ROOT, _F_ROOT)
        assert str(result).endswith("faceswap")
