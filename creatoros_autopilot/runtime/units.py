# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scheduling a daily run, on whichever OS you are on.

What this replaces: `setup.sh` and `shorts/setup_shorts.sh` wrote a launchd plist and
called `launchctl bootstrap`. That is macOS and nothing else, so on Linux or Windows there
was no way to schedule the pipeline at all.

Each backend below generates a native unit — launchd on macOS, a systemd user timer on
Linux, a Scheduled Task on Windows — rather than running a Python process forever. A
scheduler the OS owns survives a reboot, a crash and a logout, and it is the one the user
already knows how to inspect and disable. `creatoros schedule show` prints the unit
without installing it, so nothing is written to a user's machine unseen.

Every generated unit runs `creatoros run --if-due`, and the due-time gate is enforced in
Python (`core.schedule.is_due`), so a missed wake-up catches up instead of skipping a day.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import LINUX, MACOS, WINDOWS, os_name

LABEL = "com.creatoros.autopilot"
SERVICE = "creatoros-autopilot"


@dataclass
class Unit:
    """A generated scheduler unit, before it touches the system."""

    backend: str
    path: Path
    text: str
    install_cmds: list[list[str]]
    remove_cmds: list[list[str]]
    inspect: str                 # how the user checks on it themselves


def _executable() -> list[str]:
    """How to invoke CreatorOS from a scheduler, as an argv list.

    Prefers the installed console script. Falls back to `python -m`, which is what a
    `pip install --user` or a venv that is not on the scheduler's PATH needs — a scheduler
    does not load a login shell, so a bare `creatoros` often is not found.
    """
    found = shutil.which("creatoros")
    if found:
        return [found]
    return [sys.executable, "-m", "creatoros_autopilot"]


def build(run_at: str = "06:30", workspace: str | os.PathLike | None = None,
          config: str | os.PathLike | None = None) -> Unit:
    hour, minute = (int(x) for x in run_at.split(":"))
    argv = _executable() + ["run", "--if-due"]
    if config:
        argv += ["--config", str(Path(config).resolve())]
    env = {"CREATOROS_WORKSPACE": str(Path(workspace).resolve())} if workspace else {}
    backend = os_name()
    if backend == MACOS:
        return _launchd(argv, hour, minute, env)
    if backend == WINDOWS:
        return _schtasks(argv, hour, minute, env)
    return _systemd(argv, hour, minute, env)


# ------------------------------------------------------------------------ macOS
def _launchd(argv, hour, minute, env) -> Unit:
    path = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    args = "".join(f"\n      <string>{_xml(a)}</string>" for a in argv)
    envs = "".join(f"\n      <key>{_xml(k)}</key><string>{_xml(v)}</string>" for k, v in env.items())
    logs = Path.home() / "Library" / "Logs" / "creatoros"
    text = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array>{args}
  </array>
  <key>EnvironmentVariables</key><dict>{envs}
  </dict>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>{hour}</integer><key>Minute</key><integer>{minute}</integer></dict>
  <key>RunAtLoad</key><true/>
  <key>ThrottleInterval</key><integer>300</integer>
  <key>StandardOutPath</key><string>{_xml(str(logs / 'run.out.log'))}</string>
  <key>StandardErrorPath</key><string>{_xml(str(logs / 'run.err.log'))}</string>
</dict></plist>
"""
    uid = os.getuid()
    return Unit(
        backend="launchd", path=path, text=text,
        install_cmds=[["launchctl", "bootout", f"gui/{uid}/{LABEL}"],
                      ["launchctl", "bootstrap", f"gui/{uid}", str(path)]],
        remove_cmds=[["launchctl", "bootout", f"gui/{uid}/{LABEL}"]],
        inspect=f"launchctl list | grep {LABEL}",
    )


# ------------------------------------------------------------------------ Linux
def _systemd(argv, hour, minute, env) -> Unit:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"
    envs = "".join(f'Environment="{k}={v}"\n' for k, v in env.items())
    text = f"""# {SERVICE}.service
[Unit]
Description=CreatorOS Autopilot daily run

[Service]
Type=oneshot
{envs}ExecStart={' '.join(_sh(a) for a in argv)}

# ---8<--- {SERVICE}.timer
[Unit]
Description=CreatorOS Autopilot daily run

[Timer]
OnCalendar=*-*-* {hour:02d}:{minute:02d}:00
Persistent=true

[Install]
WantedBy=timers.target
"""
    return Unit(
        backend="systemd", path=base / f"{SERVICE}.service", text=text,
        install_cmds=[["systemctl", "--user", "daemon-reload"],
                      ["systemctl", "--user", "enable", "--now", f"{SERVICE}.timer"]],
        remove_cmds=[["systemctl", "--user", "disable", "--now", f"{SERVICE}.timer"]],
        inspect=f"systemctl --user list-timers {SERVICE}.timer",
    )


# ---------------------------------------------------------------------- Windows
def _schtasks(argv, hour, minute, env) -> Unit:
    path = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "creatoros" / "scheduled-task.cmd"
    exports = "".join(f"set {k}={v}\r\n" for k, v in env.items())
    text = "@echo off\r\n" + exports + " ".join(_win(a) for a in argv) + "\r\n"
    return Unit(
        backend="schtasks", path=path, text=text,
        install_cmds=[["schtasks", "/Create", "/F", "/TN", SERVICE, "/SC", "DAILY",
                       "/ST", f"{hour:02d}:{minute:02d}", "/TR", f'"{path}"']],
        remove_cmds=[["schtasks", "/Delete", "/F", "/TN", SERVICE]],
        inspect=f"schtasks /Query /TN {SERVICE}",
    )


def install(unit: Unit) -> list[str]:
    """Write the unit and register it. Returns the lines to show the user."""
    unit.path.parent.mkdir(parents=True, exist_ok=True)
    unit.path.write_text(unit.text, encoding="utf-8")
    if unit.backend == "systemd":
        service, _, timer = unit.text.partition(f"# ---8<--- {SERVICE}.timer")
        unit.path.write_text(service.strip() + "\n", encoding="utf-8")
        (unit.path.parent / f"{SERVICE}.timer").write_text(timer.strip() + "\n", encoding="utf-8")
    if unit.backend == "schtasks":
        os.chmod(unit.path, 0o755)
    out = [f"wrote {unit.path}"]
    for cmd in unit.install_cmds:
        done = subprocess.run(cmd, capture_output=True, text=True)
        # The first command of each backend is a best-effort teardown of a previous
        # install, so a non-zero exit there is normal and must not abort the install.
        if done.returncode != 0 and cmd is not unit.install_cmds[0]:
            out.append(f"!! {' '.join(cmd)} -> {done.returncode} {done.stderr.strip()[:200]}")
        else:
            out.append(f"ran {' '.join(cmd)}")
    out.append(f"check it with: {unit.inspect}")
    return out


def remove(unit: Unit) -> list[str]:
    out = []
    for cmd in unit.remove_cmds:
        subprocess.run(cmd, capture_output=True, text=True)
        out.append(f"ran {' '.join(cmd)}")
    for target in (unit.path, unit.path.parent / f"{SERVICE}.timer"):
        if target.exists():
            target.unlink()
            out.append(f"removed {target}")
    return out


def _xml(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _sh(text: str) -> str:
    return f'"{text}"' if " " in str(text) else str(text)


def _win(text: str) -> str:
    return f'"{text}"' if " " in str(text) else str(text)
