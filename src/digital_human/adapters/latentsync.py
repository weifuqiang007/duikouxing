from __future__ import annotations

from pathlib import Path

from ..config import JobConfig, LocalConfig
from ..process import conda_run, run_command


LATENTSYNC_COMMIT = "a229c3948406bc2cf6eaf4873e662e70c6a04746"


class LatentSyncAdapter:
    """LatentSync 1.6 官方推理入口的薄适配层。"""

    def __init__(self, config: LocalConfig) -> None:
        self.config = config

    def build_command(
        self,
        *,
        video: Path,
        audio: Path,
        output: Path,
        job: JobConfig,
        work_dir: Path,
    ) -> list[str | Path]:
        repo = self.config.latentsync_repo
        unet_config = repo / "configs" / "unet" / "stage2_512.yaml"
        lipsync = job.lipsync
        command = conda_run(
            self.config.conda,
            self.config.latentsync_env,
            [
                "python",
                "-m",
                "scripts.inference",
                "--unet_config_path",
                unet_config,
                "--inference_ckpt_path",
                self.config.latentsync_checkpoint,
                "--inference_steps",
                str(int(lipsync.get("inference_steps", 30))),
                "--guidance_scale",
                str(float(lipsync.get("guidance_scale", 1.3))),
                "--seed",
                str(int(lipsync.get("seed", 1247))),
                "--video_path",
                video,
                "--audio_path",
                audio,
                "--video_out_path",
                output,
                "--temp_dir",
                work_dir / "latentsync_temp",
            ],
        )
        if bool(lipsync.get("enable_deepcache", False)):
            command.append("--enable_deepcache")
        return command

    def generate(
        self,
        *,
        video: Path,
        audio: Path,
        output: Path,
        job: JobConfig,
        work_dir: Path,
        log_file: Path,
    ) -> None:
        repo = self.config.latentsync_repo
        required = (
            repo / "scripts" / "inference.py",
            repo / "configs" / "unet" / "stage2_512.yaml",
            self.config.latentsync_checkpoint,
            repo / "checkpoints" / "whisper" / "tiny.pt",
            repo / "checkpoints" / "auxiliary" / "models" / "buffalo_l" / "det_10g.onnx",
            repo
            / "checkpoints"
            / "auxiliary"
            / "models"
            / "buffalo_l"
            / "2d106det.onnx",
        )
        for path in required:
            if not path.is_file():
                raise RuntimeError(f"LatentSync 1.6 缺少必需文件: {path}")

        # 官方代码先以 CRF13 写入视频；项目补丁必须在封装音频时 copy
        # 视频流，否则会再次以 CRF18 编码，直接损失嘴唇和牙齿细节。
        pipeline_path = repo / "latentsync" / "pipelines" / "lipsync_pipeline.py"
        pipeline_text = pipeline_path.read_text(encoding="utf-8")
        if "-c:v copy" not in pipeline_text:
            raise RuntimeError(
                "LatentSync 高画质封装补丁未应用；请重跑 scripts/setup_cloud_4090.sh"
            )

        # 口型幅度旋钮：audio_amp != 1.0 时透传 LATENTSYNC_AUDIO_AMP，
        # 依赖 audio2feature 幅度补丁（scripts/setup_cloud_4090.sh 自动应用）。
        audio_amp = float(job.lipsync.get("audio_amp", 1.0))
        run_env: dict[str, str] | None = None
        if audio_amp != 1.0:
            audio2feature_path = repo / "latentsync" / "whisper" / "audio2feature.py"
            if "LATENTSYNC_AUDIO_AMP" not in audio2feature_path.read_text(encoding="utf-8"):
                raise RuntimeError(
                    "lipsync.audio_amp 需要 audio2feature 幅度补丁；请重跑 scripts/setup_cloud_4090.sh"
                )
            run_env = {"LATENTSYNC_AUDIO_AMP": str(audio_amp)}

        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            output.unlink()
        command = self.build_command(
            video=video,
            audio=audio,
            output=output,
            job=job,
            work_dir=work_dir,
        )
        run_command(command, cwd=repo, log_file=log_file, env=run_env)
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError(f"LatentSync 1.6 未生成预期文件: {output}")
