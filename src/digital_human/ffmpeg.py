from __future__ import annotations

import json
from pathlib import Path

from .process import run_command


def media_duration(ffprobe: str, path: Path) -> float:
    result = run_command(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            path,
        ]
    )
    payload = json.loads(result.stdout)
    return float(payload["format"]["duration"])


def normalize_video(ffmpeg: str, source: Path, output: Path, fps: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            ffmpeg,
            "-y",
            "-i",
            source,
            "-an",
            "-vf",
            f"fps={fps}",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            output,
        ]
    )


def extract_reference_audio(
    ffmpeg: str,
    source: Path,
    output: Path,
    start_seconds: float,
    duration_seconds: float,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            ffmpeg,
            "-y",
            "-ss",
            f"{start_seconds:.3f}",
            "-i",
            source,
            "-t",
            f"{duration_seconds:.3f}",
            "-vn",
            "-ar",
            "48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            output,
        ]
    )


def concat_and_normalize_audio(ffmpeg: str, inputs: list[Path], output: Path) -> None:
    if not inputs:
        raise ValueError("没有可拼接的音频")
    output.parent.mkdir(parents=True, exist_ok=True)
    args: list[str | Path] = [ffmpeg, "-y"]
    for item in inputs:
        args.extend(["-i", item])
    concat_inputs = "".join(f"[{index}:a]" for index in range(len(inputs)))
    filter_graph = (
        f"{concat_inputs}concat=n={len(inputs)}:v=0:a=1[joined];"
        "[joined]loudnorm=I=-16:LRA=11:TP=-1.5[out]"
    )
    args.extend(
        [
            "-filter_complex",
            filter_graph,
            "-map",
            "[out]",
            "-ar",
            "48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            output,
        ]
    )
    run_command(args)


def match_video_duration(
    ffmpeg: str,
    ffprobe: str,
    source: Path,
    audio: Path,
    output: Path,
    policy: str,
    fps: int,
) -> None:
    video_duration = media_duration(ffprobe, source)
    target_duration = media_duration(ffprobe, audio)
    output.parent.mkdir(parents=True, exist_ok=True)
    if video_duration + (1 / fps) >= target_duration:
        run_command(
            [
                ffmpeg,
                "-y",
                "-i",
                source,
                "-t",
                f"{target_duration:.6f}",
                "-an",
                "-c:v",
                "libx264",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                output,
            ]
        )
        return
    if policy != "pingpong":
        raise ValueError(
            f"目标音频 {target_duration:.2f}s 长于视频 {video_duration:.2f}s，"
            "duration_policy 必须为 pingpong"
        )
    cycle = output.with_name("base_pingpong_cycle.mp4")
    run_command(
        [
            ffmpeg,
            "-y",
            "-i",
            source,
            "-filter_complex",
            "[0:v]split=2[fwd][revsrc];[revsrc]reverse[rev];"
            "[fwd][rev]concat=n=2:v=1:a=0[out]",
            "-map",
            "[out]",
            "-an",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            cycle,
        ]
    )
    run_command(
        [
            ffmpeg,
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            cycle,
            "-t",
            f"{target_duration:.6f}",
            "-an",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            output,
        ]
    )


def mux_audio(ffmpeg: str, video: Path, audio: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            ffmpeg,
            "-y",
            "-i",
            video,
            "-i",
            audio,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            output,
        ]
    )
