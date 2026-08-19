"""真人驱动视频与导读音频的同步预检。

时长容差（duration_tolerance_ratio）只能保证首尾对齐；录制时如果没有
实时跟随导读，口型与最终音轨依然会整体错位，且要到出片才能发现。
这里在渲染前用能量包络相关度做低成本校验：

- 纯 Python 实现（不依赖 numpy），20ms 窗口 RMS 包络 + 皮尔逊相关；
- 驱动音轨先按导读时长变速再比对，合格跟录不会因整体快慢被误判；
- 允许 ±max_lag_seconds 的全局起读偏移；
- 无声对口型的录音没有可比对的声音信号，跳过校验（返回 None）。

阈值经验值：实测未跟读的坏样本约 0.1，合格跟读样本应在 0.3 以上；
后续可换成 LatentSync 的 SyncNet 置信度做更精确的校验。
"""

from __future__ import annotations

import json
import math
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg import media_duration
from .process import run_command

SAMPLE_RATE = 16000
ENVELOPE_WINDOW_SECONDS = 0.02
# 录音整体 RMS 低于该值视为无声对口型，无法用音频校验。
SILENT_RMS_THRESHOLD = 0.005
# atempo 单实例有效范围，超界时不变速（相关度只会更低，不会误放行）。
ATEMPO_RANGE = (0.5, 2.0)


@dataclass(frozen=True)
class SyncReport:
    correlation: float
    offset_seconds: float

    def as_dict(self) -> dict[str, float]:
        return {
            "correlation": round(self.correlation, 4),
            "offset_seconds": round(self.offset_seconds, 3),
        }


def has_audio_stream(ffprobe: str, path: Path) -> bool:
    result = run_command(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            path,
        ]
    )
    return bool(json.loads(result.stdout).get("streams"))


def _extract_mono_audio(ffmpeg: str, source: Path, output: Path, speed: float) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    filters = f"atempo={speed:.6f},aresample={SAMPLE_RATE}"
    run_command(
        [
            ffmpeg,
            "-y",
            "-i",
            source,
            "-vn",
            "-af",
            filters,
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            output,
        ]
    )


def _read_envelope(path: Path, window_samples: int) -> tuple[list[float], float]:
    """读取 16bit PCM wav，返回 (每窗 RMS 包络, 整体 RMS)。"""
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        raw = handle.readframes(handle.getnframes())
    if width != 2:
        raise ValueError(f"同步预检需要 16bit PCM wav: {path}")
    samples = array("h")
    samples.frombytes(raw[: (len(raw) // 2) * 2])
    if channels > 1:
        samples = samples[::channels]
    if not samples:
        return [], 0.0
    total = len(samples)
    overall_rms = math.sqrt(sum(float(s) * s for s in samples) / total) / 32768.0
    envelope: list[float] = []
    for start in range(0, total - window_samples + 1, window_samples):
        energy = 0.0
        for s in samples[start : start + window_samples]:
            energy += float(s) * s
        envelope.append(math.sqrt(energy / window_samples) / 32768.0)
    return envelope, overall_rms


def _pearson(a: list[float], b: list[float]) -> float:
    n = len(a)
    if n == 0:
        return 0.0
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    cov = var_a = var_b = 0.0
    for x, y in zip(a, b):
        da = x - mean_a
        db = y - mean_b
        cov += da * db
        var_a += da * da
        var_b += db * db
    if var_a <= 0.0 or var_b <= 0.0:
        return 0.0
    return cov / math.sqrt(var_a * var_b)


def best_offset_correlation(
    driving: list[float], guide: list[float], max_lag_windows: int
) -> tuple[float, int]:
    """在 ±max_lag_windows 内搜索最佳对齐偏移。

    返回 (最佳皮尔逊相关, 对应偏移窗口数)；偏移 >0 表示驱动录音整体晚于导读。
    """
    best_correlation = 0.0
    best_lag = 0
    for lag in range(-max_lag_windows, max_lag_windows + 1):
        if lag >= 0:
            a = driving[lag:]
            b = guide[: len(a)]
        else:
            b = guide[-lag:]
            a = driving[: len(b)]
        count = min(len(a), len(b))
        if count < 20:
            continue
        correlation = _pearson(a[:count], b[:count])
        if correlation > best_correlation:
            best_correlation = correlation
            best_lag = lag
    return best_correlation, best_lag


def check_driving_sync(
    ffmpeg: str,
    ffprobe: str,
    driving: Path,
    guide: Path,
    work_dir: Path,
    max_lag_seconds: float = 3.0,
) -> SyncReport | None:
    """比对驱动视频音轨与导读音频的节奏相关度。

    返回 None 表示驱动视频没有可用的声音信号（无声对口型），无法校验。
    """
    if not has_audio_stream(ffprobe, driving):
        return None
    driving_duration = media_duration(ffprobe, driving)
    guide_duration = media_duration(ffprobe, guide)
    speed = 1.0
    if driving_duration > 0 and guide_duration > 0:
        speed = min(
            max(driving_duration / guide_duration, ATEMPO_RANGE[0]),
            ATEMPO_RANGE[1],
        )
    work_dir.mkdir(parents=True, exist_ok=True)
    driving_wav = work_dir / "sync_driving_16k.wav"
    guide_wav = work_dir / "sync_guide_16k.wav"
    _extract_mono_audio(ffmpeg, driving, driving_wav, speed)
    _extract_mono_audio(ffmpeg, guide, guide_wav, 1.0)

    window_samples = int(SAMPLE_RATE * ENVELOPE_WINDOW_SECONDS)
    driving_envelope, driving_rms = _read_envelope(driving_wav, window_samples)
    if driving_rms < SILENT_RMS_THRESHOLD:
        return None
    guide_envelope, _ = _read_envelope(guide_wav, window_samples)

    max_lag_windows = int(max_lag_seconds / ENVELOPE_WINDOW_SECONDS)
    correlation, lag = best_offset_correlation(driving_envelope, guide_envelope, max_lag_windows)
    return SyncReport(
        correlation=correlation,
        offset_seconds=lag * ENVELOPE_WINDOW_SECONDS,
    )
