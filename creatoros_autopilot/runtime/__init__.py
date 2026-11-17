# SPDX-License-Identifier: AGPL-3.0-or-later
"""The only place in CreatorOS that is allowed to care which operating system it is on.

Everything OS-specific lives here: notifications, config directories and the scheduler
unit files. Core, render, providers and quality must stay portable, and
`tests/test_portability.py` enforces that by failing if they grow an `osascript`,
`launchctl`, `/opt/homebrew` or `$HOME`-literal reference.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

MACOS, LINUX, WINDOWS = "macos", "linux", "windows"


def os_name() -> str:
    if sys.platform == "darwin":
        return MACOS
    if sys.platform in ("win32", "cygwin"):
        return WINDOWS
    return LINUX


def is_macos() -> bool:
    return os_name() == MACOS


def is_windows() -> bool:
    return os_name() == WINDOWS


def user_config_dir() -> Path:
    """Where `creatoros.yaml` lives when it is not beside the project.

    Follows each platform's own convention rather than planting a dotfile in `$HOME`:
    `%APPDATA%` on Windows, `XDG_CONFIG_HOME` on Linux (defaulting to `~/.config`), and
    `~/Library/Application Support` on macOS.
    """
    if is_windows():
        base = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
    elif is_macos():
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "creatoros"


def default_workspace() -> Path:
    """Where renders and state go when config does not say.

    Deliberately not inside the installed package or the clone: a `pip install` must not
    write into site-packages, and a `git pull` must not fight with a day's output.
    """
    env = os.environ.get("CREATOROS_WORKSPACE")
    if env:
        return Path(env).expanduser()
    if is_windows():
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return Path(base) / "creatoros"
    if is_macos():
        return Path.home() / "Library" / "Application Support" / "creatoros" / "workspace"
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "creatoros"


def notify(message: str, title: str = "CreatorOS") -> bool:
    """Best-effort desktop notification. Never raises, never blocks a run.

    A notification is a courtesy, so a missing `notify-send` or a sandboxed shell must not
    be able to fail a render that already succeeded. Returns whether it got through, which
    is what `creatoros doctor` reports.
    """
    try:
        if is_macos():
            script = f'display notification {_osa(message)} with title {_osa(title)}'
            cmd = ["osascript", "-e", script]
        elif is_windows():
            ps = (
                "[reflection.assembly]::LoadWithPartialName('System.Windows.Forms')"
                ">$null; $n = New-Object System.Windows.Forms.NotifyIcon; "
                "$n.Icon = [System.Drawing.SystemIcons]::Information; "
                f"$n.BalloonTipTitle = {_pwsh(title)}; $n.BalloonTipText = {_pwsh(message)}; "
                "$n.Visible = $true; $n.ShowBalloonTip(5000); Start-Sleep -Seconds 1"
            )
            cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps]
        else:
            cmd = ["notify-send", title, message]
        subprocess.run(cmd, check=True, capture_output=True, timeout=20)
        return True
    except Exception:
        return False


def _osa(text: str) -> str:
    """An AppleScript string literal. Only `\\` and `"` need escaping."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _pwsh(text: str) -> str:
    """A PowerShell single-quoted literal, where `'` is escaped by doubling it."""
    return "'" + text.replace("'", "''") + "'"
