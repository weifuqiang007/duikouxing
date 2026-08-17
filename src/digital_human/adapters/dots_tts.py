from __future__ import annotations

from pathlib import Path

from ..config import LocalConfig
from ..process import conda_run, run_command


class DotsTTSAdapter:
    def __init__(self, config: LocalConfig) -> None:
        self.config = config

    def generate(
        self,
        *,
        text: str,
        prompt_audio: Path,
        prompt_text: str,
        output: Path,
        profile: str,
        language: str,
        guidance_scale: float,
        seed: int,
        log_file: Path,
    ) -> None:
        if profile == "auto":
            profile = self.config.tts_profile
        if profile == "quality":
            model = self.config.dots_quality_model
            steps = 10
        elif profile == "fast":
            model = self.config.dots_fast_model
            steps = 4
        else:
            raise ValueError(f"未知 dots.tts profile: {profile}")
        output.parent.mkdir(parents=True, exist_ok=True)
        run_command(
            conda_run(
                self.config.conda,
                self.config.dots_env,
                [
                "dots.tts",
                "--model-name-or-path",
                model,
                "--text",
                text,
                "--prompt-audio",
                prompt_audio,
                "--prompt-text",
                prompt_text,
                "--num-steps",
                str(steps),
                "--guidance-scale",
                str(guidance_scale),
                "--language",
                language,
                "--seed",
                str(seed),
                "--output",
                output,
                ],
            ),
            log_file=log_file,
        )
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError(f"dots.tts 未生成有效音频: {output}")
