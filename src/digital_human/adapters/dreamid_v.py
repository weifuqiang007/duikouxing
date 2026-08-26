"""DreamID-V 云端适配器 — 阶段二占位，不下载模型。"""

from __future__ import annotations

from pathlib import Path

from digital_human.adapters.face_swap import FaceSwapAdapter, FaceSwapResult


class DreamIDVAdapter(FaceSwapAdapter):
    """第二阶段云端高质量换脸适配器。

    待有 4090 云服务器和第一阶段真实样本数据后再开发。
    当前仅实现接口占位。
    """

    def doctor(self) -> None:
        raise NotImplementedError(
            "DreamID-V 适配器尚未实现。"
            "需要 4090 级云 GPU 和第一阶段样本数据。"
        )

    def run(
        self,
        source_images: list[Path],
        target_video: Path,
        output_video: Path,
        profile: dict,
    ) -> FaceSwapResult:
        raise NotImplementedError("DreamID-V 适配器尚未实现。")
