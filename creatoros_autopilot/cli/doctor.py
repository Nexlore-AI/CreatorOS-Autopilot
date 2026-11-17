# SPDX-License-Identifier: AGPL-3.0-or-later
"""`creatoros doctor` — what is installed, what is configured, and what is missing.

The point of this command is that a failure should never be a stack trace from inside a
render three hours in. Every check says what is wrong *and* the exact command or setting
that fixes it.

Checks are split into required and optional. Rendering is required — it is the thing you
can do with no keys and no accounts. Narration, publishing and the agent are optional,
because a perfectly good use of this tool is to render specs locally and post them
yourself. Only a required failure sets a non-zero exit code.

No check prints a secret. A key is reported as present or missing by name only.
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass

from ..core import media, settings as settings_mod
from ..core.schedule import zone
from ..runtime import os_name

OK, WARN, FAIL = "ok", "warn", "fail"
MARK = {OK: "+", WARN: "~", FAIL: "x"}


@dataclass
class Check:
    name: str
    state: str
    detail: str = ""
    fix: str = ""
    required: bool = True


def _python() -> Check:
    version = ".".join(str(n) for n in sys.version_info[:3])
    if sys.version_info < (3, 10):
        return Check("python", FAIL, version, "CreatorOS needs Python 3.10 or newer")
    return Check("python", OK, f"{version} ({sys.executable})")


def _imports() -> list[Check]:
    out = []
    for module, why, required in (("PIL", "Pillow, the renderer", True),
                                  ("numpy", "the renderer's maths", True),
                                  ("yaml", "reading creatoros.yaml", True)):
        try:
            __import__(module)
            out.append(Check(module, OK, why, required=required))
        except ImportError:
            out.append(Check(module, FAIL, f"missing — {why}",
                             "pip install creatoros-autopilot", required=required))
    return out


def _ffmpeg() -> list[Check]:
    out = []
    for tool in ("ffmpeg", "ffprobe"):
        path = media.find(tool)
        if path:
            out.append(Check(tool, OK, path))
        else:
            out.append(Check(tool, FAIL, "not on PATH", media.INSTALL_HINT[os_name()]))
    return out


def _package_data() -> Check:
    from ..render import engine

    missing = [name for name in ("InterDisplay-Black.otf", "Inter-Bold.otf", "Inter-Regular.otf",
                                 "Inter-SemiBold.otf", "DejaVuSansMono-Bold.ttf")
               if not os.path.isfile(os.path.join(engine.FONTS, name))]
    if missing:
        return Check("fonts", FAIL, f"missing {', '.join(missing)}",
                     "reinstall the package — fonts ship as package data")
    return Check("fonts", OK, f"{engine.FONTS}")


def _config(settings, source) -> list[Check]:
    out = [Check("config", OK if source != "built-in defaults" else WARN, source,
                 "" if source != "built-in defaults" else
                 "no creatoros.yaml found; using defaults. Copy creatoros.example.yaml next to your work.",
                 required=False)]
    try:
        zone(settings.timezone)
        out.append(Check("timezone", OK, settings.timezone))
    except ValueError as exc:
        out.append(Check("timezone", FAIL, str(exc),
                         "pip install tzdata" if os_name() == "windows" else "set a valid IANA zone"))

    workspace = settings.workspace_dir
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        probe = workspace / ".creatoros-write-test"
        probe.write_text("ok")
        probe.unlink()
        out.append(Check("workspace", OK, str(workspace)))
    except Exception as exc:
        out.append(Check("workspace", FAIL, f"{workspace} is not writable ({exc})",
                         "set `workspace:` in creatoros.yaml to a directory you own"))

    tracks = len(list(settings.music_dir.glob("*.mp3"))) if settings.music_dir.is_dir() else 0
    out.append(Check("music", OK if tracks else WARN,
                     f"{tracks} track(s) in {settings.music_dir}",
                     "" if tracks else "optional: drop royalty-free .mp3 files there for a music bed",
                     required=False))
    return out


def _secret(name: str) -> bool:
    """Whether a secret is set. Deliberately returns a bool and never the value."""
    return bool(os.environ.get(name, "").strip())


def _narration(settings) -> list[Check]:
    backend = settings.tts.backend
    if backend in ("", "none"):
        return [Check("narration", WARN, "tts.backend is none — scenes render at minimum duration",
                      "set tts.backend: elevenlabs and add ELEVENLABS_API_KEY to .env", required=False)]
    if backend == "elevenlabs":
        out = [Check("narration", OK, "elevenlabs", required=False)]
        out.append(Check("ELEVENLABS_API_KEY", OK if _secret("ELEVENLABS_API_KEY") else FAIL,
                         "set" if _secret("ELEVENLABS_API_KEY") else "missing",
                         "add ELEVENLABS_API_KEY to .env beside creatoros.yaml", required=False))
        out.append(Check("tts.voice_id", OK if settings.tts.voice_id else FAIL,
                         settings.tts.voice_id or "not set",
                         "set tts.voice_id in creatoros.yaml", required=False))
        return out
    return [Check("narration", FAIL, f"unknown tts.backend {backend!r}",
                  "Phase 1 ships 'elevenlabs' and 'none'; more arrive in Phase 2", required=False)]


def _publishing(settings) -> list[Check]:
    platforms = settings.publish.platforms
    if not platforms:
        return [Check("publishing", WARN, "publish.platforms is empty — videos render but nothing posts",
                      "add platforms to creatoros.yaml when you are ready to publish", required=False)]
    out = [Check("publishing", OK, f"{', '.join(platforms)} · mode={settings.publish.mode} "
                 f"· offset={settings.publish.offset_days}d", required=False)]
    out.append(Check("ZERNIO_API_KEY", OK if _secret("ZERNIO_API_KEY") else FAIL,
                     "set" if _secret("ZERNIO_API_KEY") else "missing",
                     "add ZERNIO_API_KEY to .env, or publish by hand", required=False))
    if settings.publish.mode == "auto":
        out.append(Check("publish mode", WARN, "auto — finished videos go live without review",
                         "set publish.mode: draft to park them instead", required=False))
    return out


def _agent(settings) -> Check:
    provider = settings.agent.provider
    if provider in ("", "none"):
        return Check("agent", WARN, "disabled — write specs yourself", required=False)
    if provider == "claude_code":
        found = shutil.which("claude")
        return Check("agent", OK if found else FAIL, found or "claude CLI not on PATH",
                     "install Claude Code, or set agent.provider: none", required=False)
    return Check("agent", FAIL, f"unknown provider {provider!r}",
                 "Phase 1 ships 'claude_code' and 'none'", required=False)


def collect(settings=None, source: str = "") -> list[Check]:
    if settings is None:
        settings = settings_mod.load()
        source = settings_mod.source_of(settings)
    checks = [_python(), *_imports(), *_ffmpeg(), _package_data()]
    checks += _config(settings, source or settings_mod.source_of(settings))
    checks += _narration(settings)
    checks += _publishing(settings)
    checks.append(_agent(settings))
    return checks


def report(checks: list[Check]) -> int:
    width = max(len(c.name) for c in checks)
    for check in checks:
        print(f"  {MARK[check.state]} {check.name.ljust(width)}  {check.detail}")
        if check.fix and check.state != OK:
            print(f"    {' ' * width}  -> {check.fix}")
    blocking = [c for c in checks if c.state == FAIL and c.required]
    optional = [c for c in checks if c.state == FAIL and not c.required]
    print()
    if blocking:
        print(f"{len(blocking)} required check(s) failed: {', '.join(c.name for c in blocking)}")
        return 1
    if optional:
        print(f"ready to render. {len(optional)} optional feature(s) not configured: "
              f"{', '.join(c.name for c in optional)}")
    else:
        print("all checks passed.")
    return 0
