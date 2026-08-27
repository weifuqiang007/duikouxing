from __future__ import annotations

import re


_MAJOR_BOUNDARY = re.compile(r"(?<=[。！？；!?;])|\n+")
_MINOR_BOUNDARY = re.compile(r"(?<=[，、,:：])")


# 这是一个中文语音文本分句器，把一段长文本切成不超过 max_chars（默认 60 字）的短句。
def split_script(text: str, max_chars: int = 60) -> list[str]:
    """Split Chinese speech text without dropping any non-whitespace character."""
    if max_chars < 10:
        raise ValueError("max_chars 至少为 10")
    normalized = re.sub(r"[ \t]+", " ", text.strip())
    if not normalized:
        return []
    major_parts = [part.strip() for part in _MAJOR_BOUNDARY.split(normalized) if part.strip()]
    result: list[str] = []
    for part in major_parts:
        if len(part) <= max_chars:
            result.append(part)
            continue
        minor_parts = [item.strip() for item in _MINOR_BOUNDARY.split(part) if item.strip()]
        current = ""
        for item in minor_parts:
            if current and len(current) + len(item) > max_chars:
                result.append(current)
                current = item
            else:
                current += item
        if current:
            result.append(current)
    return result

