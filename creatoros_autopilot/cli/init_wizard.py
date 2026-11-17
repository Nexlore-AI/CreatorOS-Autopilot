# SPDX-License-Identifier: AGPL-3.0-or-later
"""`creatoros init` — from install to a first rendered video.

The wizard's job is to get someone to a video, not to collect every setting. So it asks
about the things that cannot be guessed, defaults everything else, and **never requires a
key**: the path it offers first is render-only, which works offline, costs nothing and
proves the install before anyone is asked for a credential.

Two rules it does not bend:

* **Secrets go to `.env`, never to `creatoros.yaml`.** A key typed here is written to a
  file that is gitignored, is never echoed back, and is never printed in a summary.
* **Nothing is overwritten without being asked.** Running `init` twice in the wrong
  directory must not silently replace a working configuration.

Non-interactive use (`--preset`, `--yes`) exists so this is testable and scriptable;
a wizard that can only be driven by hand cannot be regression-tested, and the first-run
path is the one most worth testing.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .. import plugins
from ..core import brand as brand_mod
from ..core import settings as settings_mod
from ..runtime import default_workspace

#: Keys a provider needs, so the wizard can offer to collect them. Names only — the wizard
#: never holds a default value for a secret.
SECRETS = {
    "elevenlabs": ["ELEVENLABS_API_KEY"],
    "zernio": ["ZERNIO_API_KEY"],
    "youtube": ["YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"],
    "instagram": ["INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_BUSINESS_ACCOUNT_ID"],
}


@dataclass
class Answers:
    workspace: str = ""
    timezone: str = "UTC"
    brand: str = ""
    style: str = "nexlore-v3"
    tts: str = "none"
    voice_id: str = ""
    agent: str = "none"
    platforms: list = field(default_factory=list)
    backends: list = field(default_factory=list)
    mode: str = "draft"
    videos_per_day: int = 1
    run_at: str = "06:30"
    gates: list = field(default_factory=list)
    secrets: dict = field(default_factory=dict)


class Console:
    """Prompting, with every question answerable by pressing return.

    `assume` drives it non-interactively: each question takes its default. That is what
    `--yes` does, and what the tests use.
    """

    def __init__(self, assume: bool = False, stream=None):
        self.assume = assume
        self.out = stream or sys.stdout

    def say(self, text: str = "") -> None:
        print(text, file=self.out)

    def ask(self, question: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        if self.assume:
            self.say(f"{question}{suffix}: {default}")
            return default
        try:
            given = input(f"{question}{suffix}: ").strip()
        except EOFError:
            return default
        return given or default

    def choose(self, question: str, options: list, default: str = "",
               describe=None) -> str:
        if not options:
            return default
        default = default if default in options else options[0]
        self.say(f"\n{question}")
        for index, option in enumerate(options, 1):
            note = f"  — {describe(option)}" if describe else ""
            mark = " (default)" if option == default else ""
            self.say(f"  {index}. {option}{mark}{note}")
        given = self.ask("choose", default)
        if given.isdigit() and 1 <= int(given) <= len(options):
            return options[int(given) - 1]
        return given if given in options else default

    def confirm(self, question: str, default: bool = True) -> bool:
        given = self.ask(f"{question} (y/n)", "y" if default else "n").lower()
        return given.startswith("y")

    def secret(self, name: str) -> str:
        """Read a key without echoing it, and without storing a default for it."""
        if self.assume:
            return ""
        import getpass
        try:
            return getpass.getpass(f"  {name} (leave blank to skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            return ""


def _free(kind: str) -> list:
    """Plugin names of this kind that need no key, cheapest-to-try first."""
    free: list[str] = []
    paid: list[str] = []
    for name in plugins.names(kind):
        try:
            backend = plugins.load(kind, name)
        except plugins.PluginError:
            continue
        (paid if getattr(backend, "paid", False) or
         getattr(backend, "third_party", False) else free).append(name)
    return free + paid


def _describe(kind: str):
    def describe(name: str) -> str:
        try:
            backend = plugins.load(kind, name)
        except plugins.PluginError:
            return "not installed"
        bits = []
        if getattr(backend, "paid", False):
            bits.append("costs money")
        elif kind in (plugins.TTS, plugins.LLM):
            bits.append("free")
        if getattr(backend, "third_party", False):
            bits.append("needs a third-party account")
        ready = backend.available()
        bits.append("ready" if ready.ok else f"not set up: {ready.detail}")
        return " · ".join(bits)
    return describe


def interview(console: Console, preset: str = "") -> Answers:
    answers = Answers()
    console.say("CreatorOS Autopilot — setup\n")
    console.say("Every question has a default. Pressing return all the way through gives")
    console.say("you a working render-only setup that needs no key and posts nothing.\n")

    answers.workspace = console.ask(
        "Where should renders, logs and the ledger live?", str(default_workspace()))
    answers.timezone = console.ask(
        "Your timezone (IANA, e.g. Europe/Berlin)", answers.timezone)

    kits = brand_mod.available()
    if kits:
        answers.brand = console.choose(
            "Brand kit — the name, colours and fonts on every frame",
            [""] + kits, answers.brand,
            describe=lambda k: "the neutral built-in look" if not k else "")
    styles = plugins.names(plugins.STYLE)
    answers.style = console.choose("Visual style", styles, answers.style)

    answers.tts = console.choose(
        "Narration", ["none"] + _free(plugins.TTS), "none", describe=_describe(plugins.TTS))
    if answers.tts != "none":
        answers.voice_id = console.ask("  voice id (blank to set later)", "")

    answers.agent = console.choose(
        "Who writes the scripts?", ["none"] + _free(plugins.LLM), "none",
        describe=_describe(plugins.LLM))

    console.say("\nPublishing. `draft` and the local folder are safe: nothing goes live.")
    publishers = plugins.names(plugins.PUBLISH)
    chosen = console.choose("Publish with", ["none"] + publishers, "none",
                            describe=_describe(plugins.PUBLISH))
    if chosen != "none":
        answers.backends = [chosen]
        backend = plugins.load(plugins.PUBLISH, chosen)
        reach = [p for p in backend.platforms if p != "*"]
        default_platforms = ",".join(reach) or "instagram,youtube"
        answers.platforms = [p.strip() for p in console.ask(
            "  platforms (comma separated)", default_platforms).split(",") if p.strip()]
        answers.mode = console.choose(
            "  when a video is ready", ["draft", "auto"], "draft",
            describe=lambda m: ("create it and leave it for you to review"
                                if m == "draft" else "schedule or publish it, no review"))
        answers.videos_per_day = int(console.ask("  videos per day", "1") or 1)
        answers.run_at = console.ask("  daily run time (HH:MM)", answers.run_at)

    gates = plugins.names(plugins.GATE)
    if gates and console.confirm(f"\nEnable quality gates ({', '.join(gates)})?", False):
        answers.gates = gates

    needed = []
    for name in {answers.tts, *answers.backends, *answers.platforms}:
        needed += SECRETS.get(name, [])
    missing = [key for key in dict.fromkeys(needed) if not os.environ.get(key)]
    if missing:
        console.say("\nKeys. These go to .env, which is gitignored, and are never shown back.")
        console.say("Leave any blank to add it yourself later.")
        for key in missing:
            value = console.secret(key)
            if value:
                answers.secrets[key] = value
    return answers


def to_yaml(answers: Answers) -> str:
    lines = [
        "# Written by `creatoros init`. Settings only — secrets live in .env.",
        "# Every key is documented in creatoros.example.yaml.",
        "",
        f"workspace: {answers.workspace!r}",
        f"timezone: {answers.timezone}",
        f"style: {answers.style}",
    ]
    if answers.brand:
        lines.append(f"brand: {answers.brand}")
    lines += [
        "",
        "content:",
        f"  videos_per_day: {answers.videos_per_day}",
        f'  run_at: "{answers.run_at}"',
        "",
        "agent:",
        f"  provider: {answers.agent or 'none'}",
        "",
        "tts:",
        f"  backend: {answers.tts or 'none'}",
        f'  voice_id: "{answers.voice_id}"',
        "",
        "publish:",
        f"  mode: {answers.mode}",
        f"  platforms: [{', '.join(answers.platforms)}]",
        f"  backends: [{', '.join(answers.backends)}]",
        "  offset_days: 1",
        '  post_times: ["09:00"]',
    ]
    if answers.gates:
        lines += ["", f"gates: [{', '.join(answers.gates)}]"]
    return "\n".join(lines) + "\n"


def write_env(path: Path, secrets: dict) -> list:
    """Merge secrets into .env without disturbing what is already there."""
    existing, order = {}, []
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = raw.partition("=")
            if sep and not raw.strip().startswith("#"):
                existing[key.strip()] = value
                order.append(key.strip())
    written = []
    for key, value in secrets.items():
        if key not in existing:
            order.append(key)
        existing[key] = value
        written.append(key)
    body = "\n".join(f"{key}={existing[key]}" for key in order)
    path.write_text(
        "# Secrets only. Gitignored. Never put these in creatoros.yaml.\n" + body + "\n",
        encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return written


def run(console: Console, directory: Path, *, force: bool = False,
        preset: str = "") -> int:
    config_path = directory / settings_mod.CONFIG_NAME
    if config_path.exists() and not force:
        console.say(f"{config_path} already exists.")
        if not console.confirm("Replace it?", False):
            console.say("Nothing was changed.")
            return 1

    answers = interview(console, preset)
    directory.mkdir(parents=True, exist_ok=True)
    config_path.write_text(to_yaml(answers), encoding="utf-8")

    env_path = directory / ".env"
    written = write_env(env_path, answers.secrets) if answers.secrets else []

    # Prove it parses rather than claiming it does. A wizard that writes a file the loader
    # rejects is worse than no wizard.
    try:
        settings_mod.load(config_path, use_env=False)
    except Exception as exc:
        console.say(f"\n!! {config_path} was written but does not load: {exc}")
        return 1

    console.say(f"\nwrote {config_path}")
    if written:
        console.say(f"wrote {env_path} ({len(written)} key(s) — values not shown)")
    console.say("\nNext:")
    console.say("  creatoros doctor")
    console.say("  creatoros render examples/specs/kv-cache.json -o first.mp4")
    if answers.mode == "draft" or not answers.platforms:
        console.say("\nNothing will be published until you set publish.platforms and mode.")
    return 0
