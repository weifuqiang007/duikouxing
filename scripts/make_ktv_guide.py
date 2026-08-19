"""把导读音频烧录成 KTV 逐字字幕视频，供操作者跟录驱动视频。

用法（用编排环境 Python 运行）：
    .conda-envs/digital-human/python.exe scripts/make_ktv_guide.py \
        --profile home --job config/job.local.yaml

输出：<jobs_root>/<job_id>/input/recording_guide_ktv.mp4

时间线推导：concat_and_normalize_audio 的结构是
    首静音 + 各 TTS 段落背靠背 + 尾静音（loudnorm 不改时长），
因此每个段落的精确起止可由段落 wav 时长累加得到；段落内部按字符权重
（汉字 1.0，顿号/逗号 0.5，句号 0.75）把时长按比例分给每个字。
逐字时间只在单个段落内估计，误差不会跨段累积。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from digital_human.audio import split_script  # noqa: E402
from digital_human.config import load_job_config, load_local_config  # noqa: E402

# 显示行宽（字数）与逐字权重。
MAX_LINE_CHARS = 15
PAUSE_WEIGHTS = {"，": 0.5, "、": 0.5, "；": 0.5, "。": 0.75, "！": 0.75, "？": 0.75, "：": 0.5}
DEFAULT_WEIGHT = 0.3
HANZI_WEIGHT = 1.0

GOLD = "&H0000D7FF"  # 已读（ASS 颜色为 &HAABBGGRR，金色 RGB 255,215,0）
WHITE = "&H00FFFFFF"  # 未读

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Current,Microsoft YaHei,58,{gold},{white},&H00000000,&H96000000,1,0,0,0,100,100,0,0,1,2.5,1,8,60,60,170,1
Style: Next,Microsoft YaHei,42,{white},{white},&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,2,0.5,8,60,60,290,1
Style: Done,Microsoft YaHei,54,{gold},{gold},&H00000000,&H96000000,1,0,0,0,100,100,0,0,1,2.5,1,5,60,60,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""".format(gold=GOLD, white=WHITE)


def media_duration(ffprobe: str, path: Path) -> float:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def ass_time(seconds: float) -> str:
    cs = max(0, round(seconds * 100))
    return f"{cs // 360000}:{cs % 360000 // 6000:02d}:{cs % 6000 // 100:02d}.{cs % 100:02d}"


def char_weight(char: str) -> float:
    if char in PAUSE_WEIGHTS:
        return PAUSE_WEIGHTS[char]
    if "一" <= char <= "鿿":
        return HANZI_WEIGHT
    return DEFAULT_WEIGHT


def segment_char_times(text: str, start: float, duration: float) -> list[tuple[str, float, float]]:
    """把一个 TTS 段落的时长按字符权重分配成逐字 (char, start, end)。"""
    weights = [char_weight(char) for char in text]
    total = sum(weights)
    times: list[tuple[str, float, float]] = []
    cursor = start
    for char, weight in zip(text, weights):
        span = duration * weight / total
        times.append((char, cursor, cursor + span))
        cursor += span
    return times


def split_display_lines(chars: list[tuple[str, float, float]]) -> list[list[tuple[str, float, float]]]:
    """按标点优先、行宽上限兜底切显示行。"""
    units: list[list[tuple[str, float, float]]] = []
    current: list[tuple[str, float, float]] = []
    for item in chars:
        current.append(item)
        if item[0] in PAUSE_WEIGHTS:
            units.append(current)
            current = []
    if current:
        units.append(current)

    lines: list[list[tuple[str, float, float]]] = []
    buffer: list[tuple[str, float, float]] = []
    for unit in units:
        if buffer and len(buffer) + len(unit) > MAX_LINE_CHARS:
            lines.append(buffer)
            buffer = []
        if len(unit) > MAX_LINE_CHARS:  # 无标点超长单元硬折行
            for index in range(0, len(unit), MAX_LINE_CHARS):
                lines.append(unit[index : index + MAX_LINE_CHARS])
            continue
        buffer.extend(unit)
    if buffer:
        lines.append(buffer)
    return lines


def karaoke_text(line: list[tuple[str, float, float]], lead_seconds: float = 0.0) -> str:
    """生成 {\k} 逐字标签；lead_seconds 为事件开头到第一个字翻转的额外延时。"""
    parts: list[str] = []
    if lead_seconds > 0.001:
        parts.append("{\\k%d}" % max(1, round(lead_seconds * 100)))
    for index, (char, start, end) in enumerate(line):
        if index + 1 < len(line):
            duration = line[index + 1][1] - start
        else:
            duration = end - start
        parts.append("{\\k%d}%s" % (max(1, round(duration * 100)), _escape(char)))
    return "".join(parts)


def _escape(char: str) -> str:
    return char.replace("{", "（").replace("}", "）")


def build_ass(
    lines: list[list[tuple[str, float, float]]],
    audio_duration: float,
    lead_silence: float,
) -> str:
    events: list[str] = []
    for index, line in enumerate(lines):
        line_start = line[0][1]
        line_end = line[-1][2]
        hold_until = lines[index + 1][0][1] if index + 1 < len(lines) else audio_duration
        end = max(line_end, hold_until)
        if index == 0:
            # 事件从 0 开始（含首静音），首字翻转时间由 lead 标签补偿。
            text = karaoke_text(line, lead_seconds=line_start)
            events.append(f"Dialogue: 0,{ass_time(0)},{ass_time(end)},Current,,0,0,0,,{text}")
        else:
            text = karaoke_text(line)
            events.append(f"Dialogue: 0,{ass_time(line_start)},{ass_time(end)},Current,,0,0,0,,{text}")
        if index + 1 < len(lines):
            preview = "".join(_escape(char) for char, _, _ in lines[index + 1])
            preview_start = lines[index][0][1]
            events.append(
                f"Dialogue: 1,{ass_time(preview_start)},{ass_time(lines[index + 1][0][1])},Next,,0,0,0,,{preview}"
            )
    if audio_duration - (lines[-1][-1][2] if lines else 0) > 0.4:
        events.append(
            f"Dialogue: 0,{ass_time(lines[-1][-1][2])},{ass_time(audio_duration)},Done,,0,0,0,,话术完毕，闭嘴保持半秒"
        )
    return HEADER + "\n".join(events) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 KTV 逐字字幕导读视频")
    parser.add_argument("--profile", choices=["office", "home"], required=True)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    local = load_local_config(project_root / "config" / f"local.{args.profile}.yaml")
    job = load_job_config(args.job)

    input_dir = local.jobs_root / job.job_id / "input"
    guide = input_dir / "recording_guide.wav"
    segment_dir = local.jobs_root / job.job_id / "work" / "tts_segments"
    if not guide.is_file() or not segment_dir.is_dir():
        raise SystemExit("缺少导读音频或 TTS 分段；请先运行 scripts/prepare_driving.ps1")

    segments = split_script(job.script, int(job.tts.get("max_chars_per_segment", 60)))
    wavs = sorted(segment_dir.glob("*.wav"))
    if len(wavs) != len(segments):
        raise SystemExit(f"TTS 分段数 {len(wavs)} 与话术分句数 {len(segments)} 不一致，请用 -Force 重新生成导读")

    lead = float(job.tts.get("lead_silence_seconds", 0.5))
    audio_duration = media_duration(args.ffprobe, guide)

    chars: list[tuple[str, float, float]] = []
    cursor = lead
    for text, wav in zip(segments, wavs):
        duration = media_duration(args.ffprobe, wav)
        chars.extend(segment_char_times(text, cursor, duration))
        cursor += duration

    lines = split_display_lines(chars)
    ass_path = input_dir / "recording_guide_ktv.ass"
    ass_path.write_text(build_ass(lines, audio_duration, lead), encoding="utf-8-sig")

    output = input_dir / "recording_guide_ktv.mp4"
    subprocess.run(
        [
            args.ffmpeg, "-y",
            "-f", "lavfi", "-i", "color=c=0x101018:s=1280x720:r=25",
            "-i", str(guide),
            "-map", "0:v", "-map", "1:a",
            "-vf", "ass=recording_guide_ktv.ass,format=yuv420p",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", str(output),
        ],
        cwd=input_dir,
        check=True,
    )
    print(f"KTV 导读视频已生成：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
