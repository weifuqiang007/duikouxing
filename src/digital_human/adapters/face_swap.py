"""换脸适配器统一协议。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FaceSwapResult:
    """一次换脸任务的输出摘要。"""
    output_video: Path
    manifest: Path
    frames_processed: int
    gpu_used: str


class FaceSwapAdapter:
    """所有换脸引擎适配器的抽象基类。

    Phase 1 实现：FaceFusionAdapter。
    Phase 2 占位：DreamIDVAdapter（不下载模型）。
    """

    def doctor(self) -> None:
        """运行时健康检查。失败时抛异常。"""
        raise NotImplementedError

    def run(
        self,
        source_images: list[Path],
        target_video: Path,
        output_video: Path,
        profile: dict,
    ) -> FaceSwapResult:
        """执行换脸任务，返回输出路径和摘要。"""
        raise NotImplementedError
