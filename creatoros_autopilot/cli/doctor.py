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
import sys
from dataclasses import dataclass

from .. import plugins
from ..core import media
from ..core import settings as settings_mod
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


def _plugin(kind: str, name: str, label: str, *, hint: str = "") -> Check:
    """Ask a plugin whether it can run, and render its own answer.

    Nothing in this file knows what an ElevenLabs key looks like or where Piper's models
    live. It used to: there was an `if backend == "elevenlabs"` branch here, which meant
    every new provider needed doctor edited too. A provider's `available()` is the single
    place that knows how it fails and how to fix it.
    """
    if name in ("", "none"):
        return Check(label, WARN, f"{kind} is disabled", hint, required=False)
    try:
        backend = plugins.load(kind, name)
    except plugins.PluginError as exc:
        return Check(label, FAIL, str(exc).split(".")[0],
                     f"installed: {', '.join(plugins.names(kind)) or '(none)'}", required=False)
    try:
        ready = backend.available()
    except Exception as exc:
        return Check(label, FAIL, f"{name}: {type(exc).__name__}: {exc}", required=False)
    paid = " · paid" if getattr(backend, "paid", False) else ""
    detail = f"{name}{paid}" + (f" — {ready.detail}" if ready.detail else "")
    return Check(label, OK if ready.ok else FAIL, detail, ready.fix, required=False)


def _installed(kind: str) -> Check:
    found = plugins.names(kind)
    return Check(f"{kind} plugins", OK if found else WARN,
                 ", ".join(found) or "none installed", required=False)


def _narration(settings) -> list[Check]:
    out = [_installed(plugins.TTS),
           _plugin(plugins.TTS, settings.tts.backend, "narration",
                   hint="set tts.backend: piper for a free local voice")]
    if settings.tts.backend not in ("", "none") and not settings.tts.voice_id:
        out.append(Check("tts.voice_id", WARN, "not set",
                         "set tts.voice_id in creatoros.yaml", required=False))
    return out


def _publishing(settings) -> list[Check]:
    out = [_installed(plugins.PUBLISH)]
    platforms = settings.publish.platforms
    if not platforms:
        out.append(Check("publishing", WARN,
                         "publish.platforms is empty — videos render but nothing posts",
                         "add platforms to creatoros.yaml, or use local_folder to write "
                         "them out with their metadata", required=False))
        return out
    out.append(Check("publishing", OK, f"{', '.join(platforms)} · mode={settings.publish.mode} "
                     f"· offset={settings.publish.offset_days}d", required=False))
    # Each platform is checked through whichever backend would actually handle it, so what
    # doctor reports is what a run would do rather than an assumption about Zernio.
    for platform in platforms:
        try:
            backend = plugins.publisher_for(platform, settings.publish.backends)
        except plugins.PluginError as exc:
            out.append(Check(f"  {platform}", FAIL, str(exc), required=False))
            continue
        ready = backend.available()
        out.append(Check(f"  {platform}", OK if ready.ok else FAIL,
                         f"via {backend.name}" + (f" — {ready.detail}" if ready.detail else ""),
                         ready.fix, required=False))
    if settings.publish.mode == "auto":
        out.append(Check("publish mode", WARN,
                         "auto — finished videos go live without review",
                         "set publish.mode: draft to park them instead", required=False))
    return out


def _agent(settings) -> list[Check]:
    out = [_installed(plugins.LLM),
           _plugin(plugins.LLM, settings.agent.provider, "agent",
                   hint="write specs yourself and use `creatoros run --skip-agent`")]
    if settings.agent.provider not in ("", "none"):
        try:
            backend = plugins.load(plugins.LLM, settings.agent.provider)
            if not getattr(backend, "agentic", False):
                out.append(Check("  research", WARN,
                                 f"{backend.name} cannot run the research agent",
                                 "use claude_code for the daily run; this backend can still "
                                 "write a spec from a topic you give it", required=False))
        except plugins.PluginError:
            pass
    return out


def _style(settings) -> list[Check]:
    from ..core import brand as brand_mod

    out = [_plugin(plugins.STYLE, settings.style, "style")]
    try:
        kit = brand_mod.load(settings.brand_path)
    except brand_mod.BrandError as exc:
        out.append(Check("brand", FAIL, str(exc).split(".")[0],
                         f"available: {', '.join(brand_mod.available()) or '(none)'}",
                         required=False))
        return out
    detail = f"{kit.name} ({kit.source})"
    if not kit.logo:
        detail += " · no logo, rendering the wordmark"
    out.append(Check("brand", OK, detail, required=False))
    return out


def _gates(settings) -> list[Check]:
    if not settings.gates:
        return [Check("gates", WARN, "none enabled",
                      f"available: {', '.join(plugins.names(plugins.GATE)) or '(none)'}",
                      required=False)]
    out = []
    for name in settings.gates:
        try:
            gate = plugins.load(plugins.GATE, name)
            out.append(Check(f"gate {name}", OK,
                             "advisory" if getattr(gate, "advisory", False) else "blocking",
                             required=False))
        except plugins.PluginError as exc:
            out.append(Check(f"gate {name}", FAIL, str(exc).split(".")[0], required=False))
    return out


def collect(settings=None, source: str = "") -> list[Check]:
    if settings is None:
        settings = settings_mod.load()
        source = settings_mod.source_of(settings)
    checks = [_python(), *_imports(), *_ffmpeg(), _package_data()]
    checks += _config(settings, source or settings_mod.source_of(settings))
    checks += _style(settings)
    checks += _narration(settings)
    checks += _publishing(settings)
    checks += _agent(settings)
    checks += _gates(settings)
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
