from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict
from pathlib import Path

from .adapters.dots_tts import DotsTTSAdapter
from .adapters.musetalk import MuseTalkAdapter
from .adapters.liveportrait import LivePortraitAdapter
from .audio import split_script
from .composite import composite_video
from .config import JobConfig, LocalConfig
from .ffmpeg import (
    concat_and_normalize_audio,
    extract_reference_audio,
    match_video_duration,
    mux_audio,
    normalize_driving_video,
    normalize_video,
)
from .manifest import sha256_file, write_manifest
from .synccheck import check_driving_sync


class Pipeline:
    def __init__(self, local: LocalConfig, job: JobConfig, force: bool = False) -> None:
        self.local = local
        self.job = job
        self.force = force
        self.driving_sync: dict[str, object] | None = None
        self.root = local.jobs_root / job.job_id
        self.input_dir = self.root / "input"
        self.work_dir = self.root / "work"
        self.output_dir = self.root / "output"
        self.log_dir = self.root / "logs"
        for directory in (self.input_dir, self.work_dir, self.output_dir, self.log_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def _should_run(self, output: Path) -> bool:
        return self.force or not output.is_file() or output.stat().st_size == 0

    def prepare_driving_guide(self) -> Path:
        """先生成客户音色导读，供真人逐字、按节奏跟录驱动视频。"""
        _, reference, target_audio, _ = self._prepare_audio()
        guide_audio = self.input_dir / "recording_guide.wav"
        if self._should_run(guide_audio):
            shutil.copy2(target_audio, guide_audio)
        guide_text = self.input_dir / "recording_script.txt"
        guide_text.write_text(
            "录制要求：戴单边耳机播放 recording_guide.wav，逐字同步跟读。\n"
            "首帧正脸、闭嘴、自然表情，摄像头固定，避免转头。\n"
            "本次话术：\n" + self.job.script + "\n",
            encoding="utf-8",
        )
        # reference 在 prepare 阶段也必须真正产出，保留变量便于明确依赖关系。
        if not reference.is_file():
            raise RuntimeError("参考音频生成失败")
        return guide_audio

    def _prepare_audio(self) -> tuple[Path, Path, Path, list[str]]:
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

        segments = split_script(self.job.script, int(self.job.tts.get("max_chars_per_segment", 60)))
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
            concat_and_normalize_audio(
                self.local.ffmpeg,
                segment_paths,
                target_audio,
                float(self.job.tts.get("lead_silence_seconds", 0.5)),
                float(self.job.tts.get("tail_silence_seconds", 0.5)),
            )
        return source_copy, reference, target_audio, segments

    def run(self) -> Path:
        if self.job.backend == "liveportrait" and self.job.driving_video is None:
            raise RuntimeError(
                "LivePortrait 任务缺少 driving_video；请先运行 prepare-driving，"
                "按照导读录制后再填写路径"
            )
        fingerprint = self._job_fingerprint()
        manifest_path = self.root / "manifest.json"
        if manifest_path.is_file() and not self.force:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            if previous.get("job_fingerprint") != fingerprint:
                raise RuntimeError(
                    "相同 job_id 的输入或配置已经变化；请更换 job_id，或确认后使用 --force"
                )

        source_copy, reference, target_audio, segments = self._prepare_audio()
        normalized_video = self.work_dir / "source_25fps.mp4"
        fps = int(self.job.video.get("fps", 25))
        if self._should_run(normalized_video):
            normalize_video(self.local.ffmpeg, source_copy, normalized_video, fps)

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

        if self.job.backend == "liveportrait":
            silent_result = self._run_liveportrait(base_video, target_audio, fps)
        else:
            silent_result = self._run_musetalk(base_video, target_audio, fps)

        final = self.output_dir / "final.mp4"
        if self._should_run(final):
            mux_audio(self.local.ffmpeg, silent_result, target_audio, final)

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
            "video_backend": self.job.backend,
            "liveportrait_commit": (
                "9b294b3d0536135442ea73cb01e6cb3ca7029dd3"
                if self.job.backend == "liveportrait"
                else None
            ),
            "musetalk_version": "1.5" if self.job.backend == "musetalk" else None,
            "driving_sync": self.driving_sync,
            "job_config": self._safe_job_summary(),
            "output": str(final),
            "status": "completed",
        }
        write_manifest(manifest_path, manifest_payload)
        return final

    def _run_musetalk(self, base_video: Path, target_audio: Path, fps: int) -> Path:
        generated = self.work_dir / "musetalk_result.mp4"
        if self._should_run(generated):
            MuseTalkAdapter(self.local).generate(
                video=base_video,
                audio=target_audio,
                output=generated,
                job=self.job,
                work_dir=self.work_dir,
                log_file=self.log_dir / "musetalk.log",
            )
        composite = self.work_dir / "composite_silent.mkv"
        if self._should_run(composite):
            composite_video(
                base_video,
                generated,
                composite,
                self.job.mouth_roi,
                fps,
                self.job.composite,
            )
        return composite

    def _run_liveportrait(self, base_video: Path, target_audio: Path, fps: int) -> Path:
        assert self.job.driving_video is not None
        driving_copy = self.input_dir / self.job.driving_video.name
        if self._should_run(driving_copy):
            shutil.copy2(self.job.driving_video, driving_copy)
        self._check_driving_sync(driving_copy, target_audio)
        normalized_driving = self.work_dir / "driving_25fps.mp4"
        if self._should_run(normalized_driving):
            normalize_driving_video(
                self.local.ffmpeg,
                self.local.ffprobe,
                driving_copy,
                target_audio,
                normalized_driving,
                fps,
                float(self.job.performance_drive.get("duration_tolerance_ratio", 0.12)),
            )
        generated = self.work_dir / "liveportrait_pasteback.mp4"
        if self._should_run(generated):
            LivePortraitAdapter(self.local).generate(
                source=base_video,
                driving=normalized_driving,
                output=generated,
                job=self.job,
                work_dir=self.work_dir,
                log_file=self.log_dir / "liveportrait.log",
            )
        return generated

    def _check_driving_sync(self, driving_copy: Path, target_audio: Path) -> None:
        """渲染前校验驱动录音是否真的跟随了导读；时长容差挡不住"自说自话"的录音。"""
        threshold = float(self.job.performance_drive.get("min_sync_envelope", 0.3))
        if threshold <= 0:
            self.driving_sync = {"status": "disabled"}
            return
        report = check_driving_sync(
            self.local.ffmpeg,
            self.local.ffprobe,
            driving_copy,
            target_audio,
            self.work_dir,
        )
        if report is None:
            self.driving_sync = {
                "status": "skipped",
                "reason": "驱动视频无可比对的声音信号（无声对口型无法校验）",
            }
            return
        self.driving_sync = {"status": "measured", **report.as_dict()}
        if report.correlation < threshold:
            raise RuntimeError(
                f"真人驱动视频与导读音频的节奏相关度仅 {report.correlation:.2f}"
                f"（要求 ≥ {threshold:.2f}，全局偏移 {report.offset_seconds:+.1f}s），"
                "录音疑似没有实时跟随导读。请戴耳机边听 recording_guide 边跟读重录，"
                "或在配置中将 min_sync_envelope 调低/置 0 关闭校验"
            )

    def _job_fingerprint(self) -> str:
        payload = _json_safe(asdict(self.job))
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        )
        digest.update(sha256_file(self.job.source_video).encode("ascii"))
        if self.job.reference_audio:
            digest.update(sha256_file(self.job.reference_audio).encode("ascii"))
        if self.job.driving_video:
            digest.update(sha256_file(self.job.driving_video).encode("ascii"))
        return digest.hexdigest()

    def _safe_job_summary(self) -> dict[str, object]:
        return {
            "job_id": self.job.job_id,
            "consent_confirmed": self.job.consent_confirmed,
            "local_only": self.job.local_only,
            "source_video_name": self.job.source_video.name,
            "has_separate_reference_audio": self.job.reference_audio is not None,
            "driving_video_name": (self.job.driving_video.name if self.job.driving_video else None),
            "reference_text_sha256": hashlib.sha256(
                self.job.reference_text.encode("utf-8")
            ).hexdigest(),
            "reference_text_length": len(self.job.reference_text),
            "script_sha256": hashlib.sha256(self.job.script.encode("utf-8")).hexdigest(),
            "script_length": len(self.job.script),
            "tts": self.job.tts,
            "video": self.job.video,
            "lipsync": self.job.lipsync,
            "backend": self.job.backend,
            "performance_drive": self.job.performance_drive,
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
