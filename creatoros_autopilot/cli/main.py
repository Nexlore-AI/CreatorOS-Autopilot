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
def cmd_render(args) -> int:
    from ..render import engine

    _settings(args)
    engine.run("render", args.spec, args.out)
    return 0


def cmd_preview(args) -> int:
    from ..render import engine

    _settings(args)
    engine.run("preview", args.spec, args.out)
    return 0


def cmd_audio(args) -> int:
    from ..providers.tts import elevenlabs

    settings = _settings(args)
    if settings.tts.backend in ("", "none"):
        print("tts.backend is none — nothing to generate. Set it in creatoros.yaml.", file=sys.stderr)
        return 1
    spec, problems = elevenlabs.narrate(args.spec, voice_id=settings.tts.voice_id,
                                        model=settings.tts.model)
    elevenlabs.pick_music(spec, args.spec, music_dir=settings.music_dir, mode=settings.music.mode)
    if problems:
        print("voice problems:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    return 0


def cmd_publish(args) -> int:
    from ..providers.publish import zernio

    settings = _settings(args)
    platforms = ([p.strip() for p in args.platforms.split(",") if p.strip()]
                 if args.platforms else list(settings.publish.platforms))
    if not platforms:
        print("no platforms: pass --platforms or set publish.platforms in creatoros.yaml",
              file=sys.stderr)
        return 1
    try:
        res = zernio.publish(args.video, platforms, args.mode, at=args.at,
                             tz=args.tz or settings.timezone,
                             yt_visibility=settings.publish.youtube.visibility,
                             yt_category=settings.publish.youtube.category_id,
                             dry_run=args.dry_run)
    except Exception as exc:
        print(f"publish failed: {exc}", file=sys.stderr)
        return 1
    return 0 if res.ok else 1


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
