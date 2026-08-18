from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict
from pathlib import Path

from .adapters.dots_tts import DotsTTSAdapter
from .adapters.musetalk import MuseTalkAdapter
from .audio import split_script
from .composite import composite_video
from .config import JobConfig, LocalConfig
from .ffmpeg import (
    concat_and_normalize_audio,
    extract_reference_audio,
    match_video_duration,
    mux_audio,
    normalize_video,
)
from .manifest import sha256_file, write_manifest


class Pipeline:
    def __init__(self, local: LocalConfig, job: JobConfig, force: bool = False) -> None:
        self.local = local
        self.job = job
        self.force = force
        self.root = local.jobs_root / job.job_id
        self.input_dir = self.root / "input"
        self.work_dir = self.root / "work"
        self.output_dir = self.root / "output"
        self.log_dir = self.root / "logs"
        for directory in (self.input_dir, self.work_dir, self.output_dir, self.log_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def _should_run(self, output: Path) -> bool:
        return self.force or not output.is_file() or output.stat().st_size == 0

    def run(self) -> Path:
        fingerprint = self._job_fingerprint()
        manifest_path = self.root / "manifest.json"
        if manifest_path.is_file() and not self.force:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            if previous.get("job_fingerprint") != fingerprint:
                raise RuntimeError(
                    "相同 job_id 的输入或配置已经变化；请更换 job_id，或确认后使用 --force"
                )
        source_copy = self.input_dir / self.job.source_video.name
        if self._should_run(source_copy):
            shutil.copy2(self.job.source_video, source_copy)
        reference = self.input_dir / "reference.wav"
        if self.job.reference_audio:
            if self._should_run(reference):
                shutil.copy2(self.job.reference_audio, reference)
        elif self._should_run(reference):
            extract_reference_audio(
                self.local.ffmpeg,
                source_copy,
                reference,
                self.job.reference_start_seconds,
                self.job.reference_duration_seconds,
            )

        normalized_video = self.work_dir / "source_25fps.mp4"
        fps = int(self.job.video.get("fps", 25))
        if self._should_run(normalized_video):
            normalize_video(self.local.ffmpeg, source_copy, normalized_video, fps)

        segments = split_script(
            self.job.script, int(self.job.tts.get("max_chars_per_segment", 60))
        )
        if not segments:
            raise RuntimeError("话术分句结果为空")
        segment_dir = self.work_dir / "tts_segments"
        segment_dir.mkdir(exist_ok=True)
        tts = DotsTTSAdapter(self.local)
        segment_paths: list[Path] = []
        for index, text in enumerate(segments):
            output = segment_dir / f"{index:03d}.wav"
            segment_paths.append(output)
            if self._should_run(output):
                tts.generate(
                    text=text,
                    prompt_audio=reference,
                    prompt_text=self.job.reference_text,
                    output=output,
                    profile=str(self.job.tts.get("profile", "auto")),
                    language=str(self.job.tts.get("language", "ZH")),
                    guidance_scale=float(self.job.tts.get("guidance_scale", 1.2)),
                    seed=int(self.job.tts.get("seed", 42)) + index,
                    log_file=self.log_dir / f"tts_{index:03d}.log",
                )

        target_audio = self.work_dir / "target_normalized.wav"
        if self._should_run(target_audio):
            concat_and_normalize_audio(self.local.ffmpeg, segment_paths, target_audio)

        base_video = self.work_dir / "base_duration_matched.mp4"
        if self._should_run(base_video):
            match_video_duration(
                self.local.ffmpeg,
                self.local.ffprobe,
                normalized_video,
                target_audio,
                base_video,
                str(self.job.video.get("duration_policy", "pingpong")),
                fps,
            )

        musetalk_video = self.work_dir / "musetalk_result.mp4"
        if self._should_run(musetalk_video):
            MuseTalkAdapter(self.local).generate(
                video=base_video,
                audio=target_audio,
                output=musetalk_video,
                job=self.job,
                work_dir=self.work_dir,
                log_file=self.log_dir / "musetalk.log",
            )

        # FFV1 无损中间件，避免 mp4v 再次磨平刚恢复的皮肤高频细节。
        composite = self.work_dir / "composite_silent.mkv"
        if self._should_run(composite):
            composite_video(
                base_video,
                musetalk_video,
                composite,
                self.job.mouth_roi,
                fps,
                self.job.composite,
            )

        final = self.output_dir / "final.mp4"
        if self._should_run(final):
            mux_audio(self.local.ffmpeg, composite, target_audio, final)

        manifest_payload = {
            "job_id": self.job.job_id,
            "job_fingerprint": fingerprint,
            "source_sha256": sha256_file(source_copy),
            "reference_sha256": sha256_file(reference),
            "output_sha256": sha256_file(final),
            "segments": segments,
            "tts_profile": self.job.tts.get("profile", "auto"),
            "machine_profile": self.local.profile,
            "expected_gpu": self.local.expected_gpu,
            "musetalk_version": "1.5",
            "job_config": self._safe_job_summary(),
            "output": str(final),
            "status": "completed",
        }
        write_manifest(manifest_path, manifest_payload)
        return final

    def _job_fingerprint(self) -> str:
        payload = _json_safe(asdict(self.job))
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        )
        digest.update(sha256_file(self.job.source_video).encode("ascii"))
        if self.job.reference_audio:
            digest.update(sha256_file(self.job.reference_audio).encode("ascii"))
        return digest.hexdigest()

    def _safe_job_summary(self) -> dict[str, object]:
        return {
            "job_id": self.job.job_id,
            "consent_confirmed": self.job.consent_confirmed,
            "local_only": self.job.local_only,
            "source_video_name": self.job.source_video.name,
            "has_separate_reference_audio": self.job.reference_audio is not None,
            "reference_text_sha256": hashlib.sha256(
                self.job.reference_text.encode("utf-8")
            ).hexdigest(),
            "reference_text_length": len(self.job.reference_text),
            "script_sha256": hashlib.sha256(self.job.script.encode("utf-8")).hexdigest(),
            "script_length": len(self.job.script),
            "tts": self.job.tts,
            "video": self.job.video,
            "lipsync": self.job.lipsync,
            "composite": self.job.composite,
            "mouth_roi": _json_safe(asdict(self.job.mouth_roi)),
        }


def _json_safe(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    json.dumps(value)
    return value
