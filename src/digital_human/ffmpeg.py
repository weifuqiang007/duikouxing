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


def media_fps(ffprobe: str, path: Path) -> float:
    result = run_command(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate",
            "-of",
            "json",
            path,
        ]
    )
    payload = json.loads(result.stdout)
    rate = payload["streams"][0]["r_frame_rate"]
    num, _, den = rate.partition("/")
    return float(num) / float(den or 1)


def normalize_video(
    ffmpeg: str,
    ffprobe: str,
    source: Path,
    output: Path,
    fps: int,
    *,
    crf: int = 18,
    passthrough: bool = False,
    start_seconds: float = 0.0,
) -> None:
    """统一帧率；start_seconds>0 时跳过源片开头的不稳定段（如举证件的调整期）。"""
    output.parent.mkdir(parents=True, exist_ok=True)
    seek = ["-ss", f"{start_seconds:.3f}"] if start_seconds > 0 else []
    if passthrough:
        source_fps = media_fps(ffprobe, source)
        if abs(source_fps - fps) < 0.02:
            run_command(
                [ffmpeg, "-y", *seek, "-i", source, "-an", "-c:v", "copy", output]
            )
            return
    run_command(
        [
            ffmpeg,
            "-y",
            *seek,
            "-i",
            source,
            "-an",
            "-vf",
            f"fps={fps}",
            "-c:v",
            "libx264",
            "-crf",
            str(crf),
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


def pingpong_filter_graph(segments: int, target_duration: float) -> str:
    """构建 fwd/rev 交替拼接后按时长裁切的 filter_complex（纯函数便于测试）。

    segments 为总段数，偶数段倒放，保证末段为正放（循环点回到片头）。
    """
    labels = [f"s{i}" for i in range(segments)]
    parts = [f"[0:v]split={segments}{''.join('[' + label + ']' for label in labels)}"]
    merged = []
    for index, label in enumerate(labels):
        if index % 2 == 1:
            rev = f"r{index}"
            parts.append(f"[{label}]reverse[{rev}]")
            merged.append(f"[{rev}]")
        else:
            merged.append(f"[{label}]")
    parts.append(
        f"{''.join(merged)}concat=n={segments}:v=1:a=0[cyc];"
        f"[cyc]trim=duration={target_duration:.6f}[out]"
    )
    return ";".join(parts)


def match_video_duration(
    ffmpeg: str,
    ffprobe: str,
    source: Path,
    audio: Path,
    output: Path,
    policy: str,
    fps: int,
    *,
    crf: int = 18,
) -> None:
    """对齐视频与目标音频时长；pingpong 单遍生成（只编码一次，避免多代画质损失）。"""
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
                str(crf),
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
    # 段数向上取整且保持奇数（正放结尾），一次 filter 图内完成拼接+裁切+编码。
    segments = max(2, int(target_duration / video_duration) + 1)
    if segments % 2 == 0:
        segments += 1
    run_command(
        [
            ffmpeg,
            "-y",
            "-i",
            source,
            "-filter_complex",
            pingpong_filter_graph(segments, target_duration),
            "-map",
            "[out]",
            "-an",
            "-c:v",
            "libx264",
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            output,
        ]
    )


def mux_audio(
    ffmpeg: str,
    video: Path,
    audio: Path,
    output: Path,
    *,
    copy_video: bool = False,
    crf: int = 18,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    video_args: list[str] = ["-c:v", "copy"] if copy_video else [
        "-c:v",
        "libx264",
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
    ]
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
            *video_args,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            output,
        ]
    )
