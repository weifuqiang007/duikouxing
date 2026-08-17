from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from ..config import JobConfig, LocalConfig
from ..process import conda_run, run_command


class MuseTalkAdapter:
    def __init__(self, config: LocalConfig) -> None:
        self.config = config

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
        repo = self.config.musetalk_repo
        models = repo / "models"
        yaml_path = work_dir / "musetalk.yaml"
        result_name = "musetalk_result.mp4"
        yaml_path.write_text(
            yaml.safe_dump(
                {
                    "task_0": {
                        "video_path": str(video),
                        "audio_path": str(audio),
                        "result_name": result_name,
                    }
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        result_root = work_dir / "musetalk_results"
        ffmpeg_path = shutil.which(self.config.ffmpeg)
        if not ffmpeg_path:
            raise RuntimeError(f"找不到 FFmpeg: {self.config.ffmpeg}")
        ffmpeg_dir = str(Path(ffmpeg_path).resolve().parent)
        lipsync = job.lipsync
        command: list[str | Path] = conda_run(
            self.config.conda,
            self.config.musetalk_env,
            [
            "python",
            "-m",
            "scripts.inference",
            "--inference_config",
            yaml_path,
            "--result_dir",
            result_root,
            "--unet_model_path",
            models / "musetalkV15" / "unet.pth",
            "--unet_config",
            models / "musetalkV15" / "musetalk.json",
            "--whisper_dir",
            models / "whisper",
            "--version",
            "v15",
            "--batch_size",
            str(int(lipsync.get("batch_size", self.config.musetalk_batch_size))),
            "--gpu_id",
            str(self.config.gpu_id),
            "--parsing_mode",
            str(lipsync.get("parsing_mode", "jaw")),
            "--extra_margin",
            str(int(lipsync.get("extra_margin", 10))),
            "--left_cheek_width",
            str(int(lipsync.get("left_cheek_width", 90))),
            "--right_cheek_width",
            str(int(lipsync.get("right_cheek_width", 90))),
            "--ffmpeg_path",
            ffmpeg_dir,
            ],
        )
        if bool(lipsync.get("use_float16", self.config.use_float16)):
            command.append("--use_float16")
        run_command(command, cwd=repo, log_file=log_file)
        produced = result_root / "v15" / result_name
        if not produced.is_file():
            raise RuntimeError(f"MuseTalk 未生成预期文件: {produced}")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(produced, output)
