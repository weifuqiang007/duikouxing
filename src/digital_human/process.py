from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable


class CommandError(RuntimeError):
    """Raised when an external command fails."""


def run_command(
    args: Iterable[str | Path],
    *,
    cwd: Path | None = None,
    log_file: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [str(item) for item in args]
    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(result.stdout, encoding="utf-8")
    if result.returncode != 0:
        tail = result.stdout[-4000:]
        raise CommandError(
            f"命令失败（退出码 {result.returncode}）: {' '.join(command)}\n{tail}"
        )
    return result


def conda_run(conda: str, prefix: Path, command: Iterable[str | Path]) -> list[str | Path]:
    """Build a conda command using an explicit environment prefix on the project drive."""
    return [conda, "run", "-p", prefix, *command]
