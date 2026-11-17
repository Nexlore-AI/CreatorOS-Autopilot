# SPDX-License-Identifier: AGPL-3.0-or-later
"""Finding ffmpeg, and asking it things.

The pipeline shells out to ffmpeg and ffprobe. Five shell scripts used to guarantee they
were reachable by prepending `/opt/homebrew/bin` to `PATH`, which is a Homebrew-on-Apple-
Silicon path and nothing else. Here they are looked up on `PATH` like any other program,
with one error message that names the platform's actual install command.
"""
from __future__ import annotations

import json
import shutil
import subprocess

from ..runtime import is_macos, is_windows

INSTALL_HINT = {
    "macos": "brew install ffmpeg",
    "linux": "apt install ffmpeg   (or dnf/pacman/apk equivalent)",
    "windows": "winget install Gyan.FFmpeg   (or: choco install ffmpeg)",
}


class FFmpegMissing(RuntimeError):
    pass


def _hint() -> str:
    return INSTALL_HINT["macos" if is_macos() else "windows" if is_windows() else "linux"]


def find(tool: str) -> str | None:
    """The absolute path to `ffmpeg` or `ffprobe`, or None."""
    return shutil.which(tool)


def require(tool: str = "ffmpeg") -> str:
    path = find(tool)
    if not path:
        raise FFmpegMissing(f"{tool} was not found on PATH. Install it with: {_hint()}")
    return path


def require_all() -> dict[str, str]:
    """Both binaries, or one error naming every missing one."""
    found, missing = {}, []
    for tool in ("ffmpeg", "ffprobe"):
        path = find(tool)
        if path:
            found[tool] = path
        else:
            missing.append(tool)
    if missing:
        raise FFmpegMissing(
            f"{' and '.join(missing)} not found on PATH. Install with: {_hint()}"
        )
    return found


def version(tool: str = "ffmpeg") -> str | None:
    path = find(tool)
    if not path:
        return None
    try:
        out = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=20)
        return out.stdout.splitlines()[0] if out.stdout else None
    except Exception:
        return None


def duration(path: str) -> float:
    """Length of a media file in seconds, via ffprobe."""
    probe = require("ffprobe")
    out = subprocess.run(
        [probe, "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {out.stderr.strip()[:200]}")
    try:
        return float(json.loads(out.stdout)["format"]["duration"])
    except Exception as exc:
        raise RuntimeError(f"ffprobe gave no duration for {path}") from exc
