from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..config import JobConfig, LocalConfig
from ..process import conda_run, run_command


class LivePortraitAdapter:
    """调用固定版本的 LivePortrait 官方 CLI，执行 source-video + driving-video。"""

    def __init__(self, config: LocalConfig) -> None:
        self.config = config

    def _gpu_env(self) -> dict[str, str]:
        """把环境内 pip 安装的 NVIDIA DLL 目录（nvidia-cudnn-cu12 等）和 torch
        自带的 CUDA 库目录前置到 PATH。

        onnxruntime 的 CUDA 后端主要靠 site-packages 里的 zz_cuda_dll_dirs.pth
        启动钩子（os.add_dll_directory）找到 cuDNN 9，这里是对 torch 等经典
        加载路径的兜底。
        """
        env = dict(os.environ)
        site_packages = self.config.liveportrait_env / "Lib" / "site-packages"
        candidates = [
            site_packages / "nvidia" / "cudnn" / "bin",
            site_packages / "nvidia" / "cublas" / "bin",
            site_packages / "torch" / "lib",
        ]
        extra = [str(path) for path in candidates if path.is_dir()]
        if extra:
            env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
        return env

    def generate(
        self,
        *,
        source: Path,
        driving: Path,
        output: Path,
        job: JobConfig,
        work_dir: Path,
        log_file: Path,
    ) -> None:
        repo = self.config.liveportrait_repo
        if not (repo / "inference.py").is_file():
            raise RuntimeError(f"LivePortrait 官方仓库未安装: {repo}")

        settings = job.performance_drive
        result_dir = work_dir / "liveportrait_results"
        result_dir.mkdir(parents=True, exist_ok=True)
        # 经包装脚本启动：monkeypatch imageio 强制 x264 单线程编码，
        # 规避本机多线程 libx264 的间歇性段错误（详见脚本内注释）。
        runner = repo.parents[1] / "scripts" / "liveportrait_runner.py"
        if not runner.is_file():
            raise RuntimeError(f"缺少 LivePortrait 启动包装脚本: {runner}")
        command: list[str | Path] = conda_run(
            self.config.conda,
            self.config.liveportrait_env,
            [
                "python",
                runner,
                repo / "inference.py",
                "--source",
                source,
                "--driving",
                driving,
                "--output_dir",
                result_dir,
                "--device_id",
                str(self.config.gpu_id),
                "--animation_region",
                str(settings.get("animation_region", "exp")),
                "--driving_multiplier",
                str(float(settings.get("driving_multiplier", 0.85))),
                "--driving_smooth_observation_variance",
                str(float(settings.get("smooth_variance", 3e-7))),
                "--source_max_dim",
                str(int(settings.get("source_max_dim", 1280))),
                "--scale",
                str(float(settings.get("source_crop_scale", 2.3))),
                "--vx_ratio",
                str(float(settings.get("source_crop_vx", 0.0))),
                "--vy_ratio",
                str(float(settings.get("source_crop_vy", -0.125))),
                "--scale_crop_driving_video",
                str(float(settings.get("driving_crop_scale", 2.2))),
                "--vx_ratio_crop_driving_video",
                str(float(settings.get("driving_crop_vx", 0.0))),
                "--vy_ratio_crop_driving_video",
                str(float(settings.get("driving_crop_vy", -0.1))),
                "--audio_priority",
                "source",
            ],
        )
        # 这些是官方 tyro 布尔开关。显式保留相对运动、stitching 和 paste-back。
        command.extend(
            [
                "--flag_relative_motion",
                "--flag_stitching",
                "--flag_pasteback",
                "--flag_do_crop",
                "--flag_crop_driving_video",
            ]
        )
        if bool(settings.get("use_half_precision", self.config.use_float16)):
            command.append("--flag_use_half_precision")
        else:
            command.append("--no_flag_use_half_precision")

        run_command(command, cwd=repo, log_file=log_file, env=self._gpu_env())
        produced = result_dir / f"{source.stem}--{driving.stem}.mp4"
        if not produced.is_file():
            candidates = sorted(
                path for path in result_dir.glob("*.mp4") if not path.stem.endswith("_concat")
            )
            if len(candidates) != 1:
                raise RuntimeError(
                    f"LivePortrait 未生成唯一成片，预期 {produced}，实际 {candidates}"
                )
            produced = candidates[0]
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(produced, output)
