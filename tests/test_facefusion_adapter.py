"""FaceFusion 适配器单元测试 — 不实际运行 FaceFusion。"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from digital_human.adapters.facefusion import (
    FaceFusionAdapter,
    FaceFusionRuntime,
    FaceSwapProfile,
)
from digital_human.face_swap_path_guard import UnsafeRuntimePath


_F = Path(r"F:\duikouxing-runtime\faceswap")
_F_PYTHON = _F / "envs" / "facefusion-3.8.2" / "python.exe"
_F_REPO = _F / "repos" / "facefusion"


def _make_runtime() -> FaceFusionRuntime:
    return FaceFusionRuntime(
        python=_F_PYTHON,
        repo=_F_REPO,
        temp_dir=_F / "temp",
        jobs_dir=_F / "jobs",
        runtime_root=_F,
    )


class TestFaceFusionAdapterDoctor:
    def test_c_drive_runtime_rejected(self) -> None:
        runtime = FaceFusionRuntime(
            python=Path(r"C:\envs\python.exe"),
            repo=Path(r"C:\repos\facefusion"),
            temp_dir=Path(r"C:\temp"),
            jobs_dir=Path(r"C:\jobs"),
            runtime_root=Path(r"C:\root"),
        )
        adapter = FaceFusionAdapter(runtime)
        with pytest.raises(UnsafeRuntimePath, match="C "):
            adapter.doctor()


class TestBuildCommand:
    """build_command() 命令构造测试。路径守卫已有独立测试，此处 mock 掉。"""

    @patch("digital_human.adapters.facefusion.require_under_runtime", side_effect=lambda p, r: p)
    @patch.object(Path, "is_file", return_value=True)
    @patch.object(Path, "mkdir")
    def test_command_is_list_of_strings(
        self, mock_mkdir: MagicMock, mock_isfile: MagicMock, mock_guard: MagicMock
    ) -> None:
        adapter = FaceFusionAdapter(_make_runtime())
        cmd = adapter.build_command(
            source_images=[_F / "jobs" / "front.jpg"],
            target_video=_F / "jobs" / "target.mp4",
            output_video=_F / "outputs" / "result.mp4",
            profile=FaceSwapProfile(),
        )
        assert isinstance(cmd, list)
        assert all(isinstance(a, str) for a in cmd)

    @patch("digital_human.adapters.facefusion.require_under_runtime", side_effect=lambda p, r: p)
    @patch.object(Path, "is_file", return_value=True)
    @patch.object(Path, "mkdir")
    def test_command_contains_headless_run(
        self, mock_mkdir: MagicMock, mock_isfile: MagicMock, mock_guard: MagicMock
    ) -> None:
        cmd = FaceFusionAdapter(_make_runtime()).build_command(
            source_images=[_F / "jobs" / "front.jpg"],
            target_video=_F / "jobs" / "target.mp4",
            output_video=_F / "outputs" / "result.mp4",
            profile=FaceSwapProfile(),
        )
        assert "headless-run" in cmd

    @patch("digital_human.adapters.facefusion.require_under_runtime", side_effect=lambda p, r: p)
    @patch.object(Path, "is_file", return_value=True)
    @patch.object(Path, "mkdir")
    def test_command_contains_processors(
        self, mock_mkdir: MagicMock, mock_isfile: MagicMock, mock_guard: MagicMock
    ) -> None:
        cmd = FaceFusionAdapter(_make_runtime()).build_command(
            source_images=[_F / "jobs" / "front.jpg"],
            target_video=_F / "jobs" / "target.mp4",
            output_video=_F / "outputs" / "result.mp4",
            profile=FaceSwapProfile(),
        )
        assert "face_swapper" in cmd
        assert "expression_restorer" in cmd
        assert "face_enhancer" in cmd

    @patch("digital_human.adapters.facefusion.require_under_runtime", side_effect=lambda p, r: p)
    @patch.object(Path, "is_file", return_value=True)
    @patch.object(Path, "mkdir")
    def test_enhancer_disabled_omits_enhancer(
        self, mock_mkdir: MagicMock, mock_isfile: MagicMock, mock_guard: MagicMock
    ) -> None:
        cmd = FaceFusionAdapter(_make_runtime()).build_command(
            source_images=[_F / "jobs" / "front.jpg"],
            target_video=_F / "jobs" / "target.mp4",
            output_video=_F / "outputs" / "result.mp4",
            profile=FaceSwapProfile(enhancer_enabled=False),
        )
        assert "face_enhancer" not in cmd
        assert "face-enhancer-model" not in cmd

    def test_empty_source_images_raises(self) -> None:
        adapter = FaceFusionAdapter(_make_runtime())
        with pytest.raises(ValueError, match="\u81f3\u5c11\u9700\u8981"):
            adapter.build_command(
                source_images=[],
                target_video=_F / "t.mp4",
                output_video=_F / "o.mp4",
                profile=FaceSwapProfile(),
            )

    @patch.object(Path, "is_file", return_value=False)
    def test_missing_source_raises(self, mock_isfile: MagicMock) -> None:
        adapter = FaceFusionAdapter(_make_runtime())
        with pytest.raises(FileNotFoundError):
            adapter.build_command(
                source_images=[Path(r"F:\nonexistent.jpg")],
                target_video=_F / "t.mp4",
                output_video=_F / "o.mp4",
                profile=FaceSwapProfile(),
            )

    @patch("digital_human.adapters.facefusion.require_under_runtime", side_effect=lambda p, r: p)
    @patch.object(Path, "is_file", return_value=True)
    @patch.object(Path, "mkdir")
    def test_chinese_filename_preserved_as_single_arg(
        self, mock_mkdir: MagicMock, mock_isfile: MagicMock, mock_guard: MagicMock
    ) -> None:
        """文件名含中文/空格时仍为单个参数，不被 shell 解释。"""
        src = Path(r"F:\jobs\张三 正脸.jpg")
        cmd = FaceFusionAdapter(_make_runtime()).build_command(
            source_images=[src],
            target_video=_F / "t.mp4",
            output_video=_F / "o.mp4",
            profile=FaceSwapProfile(),
        )
        # chinese+space filename not split by shell
        source_args = [a for a in cmd if ".jpg" in a]
        assert len(source_args) == 1
        assert " " in source_args[0]