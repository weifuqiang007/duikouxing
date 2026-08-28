"""FaceFusion 3.8.2 换脸适配器 — 阶段一实现。"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from digital_human.adapters.face_swap import FaceSwapAdapter, FaceSwapResult
from digital_human.face_swap_path_guard import UnsafeRuntimePath, require_under_runtime


@dataclass(frozen=True)
class FaceFusionRuntime:
    python: Path
    repo: Path
    temp_dir: Path
    jobs_dir: Path
    runtime_root: Path
    execution_providers: tuple[str, ...] = ("cuda",)


@dataclass(frozen=True)
class FaceSwapProfile:
    model: str = "ghost_2_256"
    pixel_boost: str = "512x512"
    swapper_weight: float = 0.85
    expression_factor: int = 80
    expression_areas: tuple[str, ...] = ("upper-face", "lower-face")
    mask_types: tuple[str, ...] = ("box", "occlusion", "region")
    mask_regions: tuple[str, ...] = (
        "skin", "left-eyebrow", "right-eyebrow", "left-eye",
        "right-eye", "nose", "mouth", "upper-lip", "lower-lip",
    )
    mask_blur: float = 0.30
    enhancer_enabled: bool = True
    enhancer_model: str = "gfpgan_1.4"
    enhancer_blend: int = 25
    enhancer_weight: float = 0.50


class FaceFusionAdapter(FaceSwapAdapter):
    EXPECTED_COMMIT = "4b1dedb853e4838ca7f3cf70b572be241aee2497"

    def __init__(self, runtime: FaceFusionRuntime) -> None:
        self.runtime = runtime

    def doctor(self) -> None:
        require_under_runtime(self.runtime.python, self.runtime.runtime_root)
        require_under_runtime(self.runtime.repo, self.runtime.runtime_root)
        require_under_runtime(self.runtime.temp_dir, self.runtime.runtime_root)
        require_under_runtime(self.runtime.jobs_dir, self.runtime.runtime_root)

        entry = self.runtime.repo / "facefusion.py"
        if not self.runtime.python.is_file():
            raise FileNotFoundError(self.runtime.python)
        if not entry.is_file():
            raise FileNotFoundError(entry)

        commit = subprocess.run(
            ["git", "-C", str(self.runtime.repo), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        if commit != self.EXPECTED_COMMIT:
            raise RuntimeError(f"FaceFusion 版本漂移: {commit}")

        providers = subprocess.run(
            [
                str(self.runtime.python), "-c",
                "import onnxruntime as o; print('\n'.join(o.get_available_providers()))",
            ],
            check=True, capture_output=True, text=True,
            cwd=self.runtime.repo, env=self._environment(),
        ).stdout
        if "CUDAExecutionProvider" not in providers:
            raise RuntimeError("未检测到 CUDAExecutionProvider，禁止正式运行")

    def build_command(
        self,
        source_images: list[Path],
        target_video: Path,
        output_video: Path,
        profile: FaceSwapProfile,
    ) -> list[str]:
        if not source_images:
            raise ValueError("至少需要一张来源人脸图片")
        for path in [*source_images, target_video]:
            if not path.is_file():
                raise FileNotFoundError(path)

        require_under_runtime(output_video, self.runtime.runtime_root)
        output_video.parent.mkdir(parents=True, exist_ok=True)

        processors = ["face_swapper", "expression_restorer"]
        if profile.enhancer_enabled:
            processors.append("face_enhancer")

        command = [
            str(self.runtime.python),
            str(self.runtime.repo / "facefusion.py"),
            "headless-run",
            "--source-paths", *map(str, source_images),
            "--target-path", str(target_video),
            "--output-path", str(output_video),
            "--temp-path", str(self.runtime.temp_dir),
            "--jobs-path", str(self.runtime.jobs_dir),
            "--processors", *processors,
            "--face-selector-mode", "one",
            "--face-swapper-model", profile.model,
            "--face-swapper-pixel-boost", profile.pixel_boost,
            "--face-swapper-weight", str(profile.swapper_weight),
            "--expression-restorer-model", "live_portrait",
            "--expression-restorer-factor", str(profile.expression_factor),
            "--expression-restorer-areas", *profile.expression_areas,
            "--face-mask-types", *profile.mask_types,
            "--face-occluder-model", "xseg_2",
            "--face-parser-model", "bisenet_resnet_34",
            "--face-mask-regions", *profile.mask_regions,
            "--face-mask-blur", str(profile.mask_blur),
            "--execution-providers", *self.runtime.execution_providers,
            "--output-video-encoder", "libx264",
            "--output-video-quality", "95",
            "--output-video-preset", "slow",
        ]
        if profile.enhancer_enabled:
            command.extend([
                "--face-enhancer-model", profile.enhancer_model,
                "--face-enhancer-blend", str(profile.enhancer_blend),
                "--face-enhancer-weight", str(profile.enhancer_weight),
            ])
        return command

    def run(
        self,
        source_images: list[Path],
        target_video: Path,
        output_video: Path,
        profile: dict,
    ) -> FaceSwapResult:
        self.doctor()
        prof = FaceSwapProfile(**profile)
        command = self.build_command(source_images, target_video, output_video, prof)
        subprocess.run(
            command, check=True,
            cwd=self.runtime.repo, env=self._environment(),
        )
        if not output_video.is_file() or output_video.stat().st_size == 0:
            raise RuntimeError("FaceFusion 未产生有效输出")
        return FaceSwapResult(
            output_video=output_video,
            manifest=Path(str(output_video) + ".manifest.json"),
            frames_processed=0,
            gpu_used="unknown",
        )

    def _environment(self) -> dict[str, str]:
        root = self.runtime.runtime_root
        env = os.environ.copy()
        env.update({
            "FACE_SWAP_RUNTIME_ROOT": str(root),
            "TEMP": str(root / "temp"),
            "TMP": str(root / "temp"),
            "PIP_CACHE_DIR": str(root / "cache" / "pip"),
            "HF_HOME": str(root / "cache" / "huggingface"),
            "HF_HUB_CACHE": str(root / "cache" / "huggingface" / "hub"),
            "TORCH_HOME": str(root / "cache" / "torch"),
            "XDG_CACHE_HOME": str(root / "cache" / "xdg"),
            "CUDA_CACHE_PATH": str(root / "cache" / "cuda"),
            "PYTHONPYCACHEPREFIX": str(root / "cache" / "pycache"),
        })
        return env
