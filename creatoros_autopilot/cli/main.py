# SPDX-License-Identifier: AGPL-3.0-or-later
"""The `creatoros` command.

This replaces seven Bash scripts — `run_daily.sh`, `setup.sh`, `shorts/run_shorts.sh`,
`shorts/setup_shorts.sh`, `shorts/short`, `reel` and `zernio` — which between them held
the lock, the due-time gate, the slot arithmetic, the ledger write and the idempotency
check, none of which could be tested and one of which was silently wrong off macOS.

Argparse rather than a CLI framework: the command surface is small, and a dependency
every user installs should earn its place.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .. import __version__
from ..core import settings as settings_mod


def _settings(args):
    """Settings for this invocation, with command-line overrides applied last."""
    settings = settings_mod.load(getattr(args, "config", None))
    if getattr(args, "workspace", None):
        settings.workspace = str(Path(args.workspace).expanduser())
    # The renderer reads the music library from the environment, so that a spec rendered
    # directly (or in a worker process) finds the same library as one rendered by `run`.
    os.environ.setdefault("CREATOROS_MUSIC_DIR", str(settings.music_dir))
    return settings


# ------------------------------------------------------------------- commands
def _style_and_brand(settings):
    from .. import plugins
    from ..core import brand as brand_mod

    style = plugins.load(plugins.STYLE, settings.style)
    kit = brand_mod.load(settings.brand_path)
    return style, kit


def cmd_render(args) -> int:
    from ..core import spec as spec_mod

    settings = _settings(args)
    style, kit = _style_and_brand(settings)
    style.render(spec_mod.load(args.spec), Path(args.spec),
                 Path(args.out) if args.out else None, brand=kit)
    return 0


def cmd_preview(args) -> int:
    from ..core import spec as spec_mod

    settings = _settings(args)
    style, kit = _style_and_brand(settings)
    style.preview(spec_mod.load(args.spec), Path(args.spec),
                  Path(args.out) if args.out else None, brand=kit)
    return 0


def cmd_audio(args) -> int:
    from .. import plugins
    from ..core import music, narrate
    from ..core import spec as spec_mod

    settings = _settings(args)
    if settings.tts.backend in ("", "none"):
        print("tts.backend is none — nothing to generate. Set it in creatoros.yaml "
              "(piper is free and runs locally).", file=sys.stderr)
        return 1
    backend = plugins.load(plugins.TTS, settings.tts.backend)
    ready = backend.available()
    if not ready:
        print(f"creatoros: {backend.name}: {ready.detail}"
              + (f"\n  -> {ready.fix}" if ready.fix else ""), file=sys.stderr)
        return 1
    spec = spec_mod.load(args.spec)
    result = narrate.narrate(spec, args.spec, backend,
                             voice=settings.tts.voice_id, model=settings.tts.model)
    print(result.summary())
    music.choose(spec, args.spec, music_dir=settings.music_dir,
                 mode=settings.music.mode, backend=backend)
    if result.problems:
        print("voice problems:\n  " + "\n  ".join(result.problems), file=sys.stderr)
        return 1
    return 0


def cmd_publish(args) -> int:
    from .. import plugins
    from ..core import spec as spec_mod
    from ..interfaces import PublishRequest

    settings = _settings(args)
    platforms = ([p.strip() for p in args.platforms.split(",") if p.strip()]
                 if args.platforms else list(settings.publish.platforms))
    if not platforms:
        print("no platforms: pass --platforms or set publish.platforms in creatoros.yaml",
              file=sys.stderr)
        return 1
    sibling = Path(args.video).with_suffix(".json")
    spec = spec_mod.load(sibling) if sibling.is_file() else {}
    ok = True
    # One call per backend, with the platforms that backend actually reaches.
    by_backend: dict = {}
    for platform in platforms:
        backend = plugins.publisher_for(platform, settings.publish.backends)
        by_backend.setdefault(backend.name, (backend, []))[1].append(platform)
    for backend, owned in by_backend.values():
        request = PublishRequest(
            video=args.video, job_id=spec_mod.job_id(spec, sibling),
            platforms=tuple(owned), mode=args.mode, at=args.at,
            tz=args.tz or settings.timezone, spec=spec, dry_run=args.dry_run)
        try:
            res = backend.publish(request)
        except Exception as exc:
            print(f"creatoros: {backend.name}: {exc}", file=sys.stderr)
            ok = False
            continue
        for outcome in res.outcomes:
            mark = "+" if outcome.ok else "x"
            print(f"  {mark} {outcome.platform} via {backend.name}"
                  + (f"  {outcome.url}" if outcome.url else "")
                  + (f"  {outcome.error}" if outcome.error else ""))
        ok = ok and res.ok
    return 0 if ok else 1


def cmd_run(args) -> int:
    from ..core import pipeline

    settings = _settings(args)
    result = pipeline.run(settings, date=args.date, if_due=args.if_due,
                          skip_agent=args.skip_agent, dry_run=args.dry_run)
    if result.message:
        print(result.message)
    return 0 if result.state in ("ok", "not-due", "busy", "nothing-to-publish") else 1


def cmd_status(args) -> int:
    from ..core import pipeline, schedule as sched
    from ..core.ledger import Ledger

    settings = _settings(args)
    date = args.date or sched.today(settings.timezone).isoformat()
    job_dir = settings.job_dir(date)
    videos = pipeline.rendered_videos(job_dir)
    ledger = Ledger(settings.ledger_path)
    platforms = list(settings.publish.platforms)

    print(f"date         : {date} ({settings.timezone})")
    print(f"workspace    : {settings.workspace_dir}")
    print(f"job folder   : {job_dir}")
    print(f"due at       : {settings.content.run_at} · "
          f"{'due' if sched.is_due(settings.content.run_at, settings.timezone) else 'not yet'}")
    print(f"platforms    : {', '.join(platforms) or '(none)'} · mode={settings.publish.mode} "
          f"· slots {', '.join(sched.parse_times(settings.publish.post_times))} "
          f"· offset {settings.publish.offset_days}d")
    status_file = job_dir / "STATUS.md"
    state = "not run"
    if status_file.is_file():
        state = next((l for l in status_file.read_text(encoding="utf-8").splitlines()
                      if l.startswith("state:")), "unknown")
    print(f"agent        : {state}")
    print(f"rendered     : {len(videos)} of {settings.content.videos_per_day}")
    for slot, video in videos:
        job_id = pipeline._job_id_for(video)
        done = sorted(ledger.done_platforms(job_id))
        pending = [p for p in platforms if p not in done]
        print(f"  {slot:02d} {video.name}  published={','.join(done) or '-'}"
              f"  pending={','.join(pending) or '-'}")
    stamp = job_dir / pipeline.STAMP
    print(f"published    : {stamp.read_text(encoding='utf-8').strip() if stamp.is_file() else 'no'}")
    return 0


def cmd_init(args) -> int:
    from . import init_wizard

    console = init_wizard.Console(assume=args.yes)
    return init_wizard.run(console, Path(args.directory or Path.cwd()),
                           force=args.force, preset=args.preset or "")


def cmd_brands(args) -> int:
    from ..core import brand as brand_mod

    names = brand_mod.available()
    if not names:
        print("no brand kits found. They are YAML files in ./brands/ or your config "
              "directory; some ship with CreatorOS.")
        return 0
    settings = _settings(args)
    for name in names:
        try:
            kit = brand_mod.load(name)
        except brand_mod.BrandError as exc:
            print(f"  x {name}: {exc}")
            continue
        mark = "*" if name == settings.brand else " "
        print(f"{mark} {name:18s} {kit.name}"
              + (f" — {kit.tagline}" if kit.tagline else "")
              + ("" if kit.logo else "  (wordmark, no logo)"))
    return 0


def cmd_doctor(args) -> int:
    from . import doctor

    settings = _settings(args)
    print(f"CreatorOS Autopilot {__version__}\n")
    return doctor.report(doctor.collect(settings, settings_mod.source_of(settings)))


def cmd_schedule(args) -> int:
    from ..runtime import units

    settings = _settings(args)
    unit = units.build(settings.content.run_at,
                       workspace=settings.workspace_dir,
                       config=settings_mod.find_config(getattr(args, "config", None)))
    if args.action == "show":
        print(f"# backend: {unit.backend}\n# would write: {unit.path}\n")
        print(unit.text)
        print(f"# then run: {' | '.join(' '.join(c) for c in unit.install_cmds)}")
        return 0
    if args.action == "install":
        for line in units.install(unit):
            print(line)
        print(f"\nruns `creatoros run --if-due` daily at {settings.content.run_at} "
              f"({settings.timezone}).")
        return 0
    for line in units.remove(unit):
        print(line)
    return 0


# ---------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="creatoros",
        description="Research, write, voice, render and publish short videos.",
        epilog="Start with: creatoros doctor",
    )
    parser.add_argument("--version", action="version", version=f"creatoros {__version__}")
    parser.add_argument("--config", metavar="PATH", help="path to creatoros.yaml")
    parser.add_argument("--workspace", metavar="PATH", help="override the workspace directory")
    sub = parser.add_subparsers(dest="command", required=True)

    render = sub.add_parser("render", help="render a spec to MP4")
    render.add_argument("spec")
    render.add_argument("-o", "--out", help="output path (default: beside the spec)")
    render.set_defaults(func=cmd_render)

    preview = sub.add_parser("preview", help="contact sheet of every scene, to check layout")
    preview.add_argument("spec")
    preview.add_argument("-o", "--out")
    preview.set_defaults(func=cmd_preview)

    audio = sub.add_parser("audio", help="generate narration and pick a music bed")
    audio.add_argument("spec")
    audio.set_defaults(func=cmd_audio)

    publish = sub.add_parser("publish", help="publish one rendered video")
    publish.add_argument("video")
    publish.add_argument("--platforms", help="comma list (default: publish.platforms)")
    publish.add_argument("--mode", default="draft", choices=["draft", "now", "schedule"])
    publish.add_argument("--at", help="ISO8601 wall-clock time, for --mode schedule")
    publish.add_argument("--tz", help="timezone for --at (default: the configured one)")
    publish.add_argument("--dry-run", action="store_true", help="print the payload, call nothing")
    publish.set_defaults(func=cmd_publish)

    run = sub.add_parser("run", help="a full day: make the videos, then publish them")
    run.add_argument("--date", help="YYYY-MM-DD (default: today in the configured timezone)")
    run.add_argument("--if-due", action="store_true", help="only run if due and not already done")
    run.add_argument("--skip-agent", action="store_true", help="publish what is already rendered")
    run.add_argument("--dry-run", action="store_true", help="decide everything, publish nothing")
    run.set_defaults(func=cmd_run)

    status = sub.add_parser("status", help="what has run, and what is still pending")
    status.add_argument("--date")
    status.set_defaults(func=cmd_status)

    doctor_p = sub.add_parser("doctor", help="check dependencies, config, keys and accounts")
    doctor_p.set_defaults(func=cmd_doctor)

    init_p = sub.add_parser("init", help="set up creatoros.yaml and .env")
    init_p.add_argument("directory", nargs="?", help="where to write (default: here)")
    init_p.add_argument("--yes", action="store_true",
                        help="take every default; never prompt")
    init_p.add_argument("--force", action="store_true", help="replace an existing config")
    init_p.add_argument("--preset", help="start from a shipped preset")
    init_p.set_defaults(func=cmd_init)

    brands_p = sub.add_parser("brands", help="list the brand kits that are available")
    brands_p.set_defaults(func=cmd_brands)

    schedule = sub.add_parser("schedule", help="install the daily job (launchd/systemd/schtasks)")
    schedule.add_argument("action", choices=["show", "install", "remove"], nargs="?", default="show")
    schedule.set_defaults(func=cmd_schedule)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        # Expected, actionable failures: a bad config key, a missing ffmpeg, an invalid
        # spec. These get one clear line, not a traceback the user has to read past.
        print(f"creatoros: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
