# SPDX-License-Identifier: AGPL-3.0-or-later
"""One day's run: agent, then publish what was rendered.

This is the Python port of `shorts/run_shorts.sh`. Everything the shell held — the lock,
the due-time gate, the slot arithmetic, the ledger write and the idempotency check — now
lives in modules that can be unit tested. The shell version could not be: its idempotency
check was `grep -F` for a JSON substring, and its date arithmetic was wrong off macOS in a
way no test could have caught because there were no tests.

The pipeline takes a `Settings` and touches nothing else. It builds no path from `$HOME`,
reads no global, and returns a result object instead of printing and exiting — which is
what lets the same code run later inside a server worker.
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from pathlib import Path

from ..runtime import notify
from . import schedule as sched
from . import spec as spec_mod
from .ledger import Entry, Ledger
from .lock import Lock, LockBusy
from .settings import Settings

STAMP = ".published"


@dataclass
class VideoResult:
    slot: int
    video: Path
    job_id: str
    platforms: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    decision: sched.SlotDecision | None = None
    ok: bool = False
    error: str = ""


@dataclass
class RunResult:
    date: str
    publish_date: str = ""
    state: str = "ok"            # ok | not-due | busy | nothing-to-publish | partial | failed
    message: str = ""
    videos: list[VideoResult] = field(default_factory=list)

    @property
    def published(self) -> int:
        return sum(1 for v in self.videos if v.ok)

    @property
    def failed(self) -> int:
        return sum(1 for v in self.videos if not v.ok and not v.skipped)


def rendered_videos(job_dir: Path) -> list[tuple[int, Path]]:
    """The day's finished MP4s, as (slot, path), slot order.

    `*_scenes` holds the per-scene intermediates the renderer concatenates, so it is
    excluded; an unfinished render leaves those behind and they are not publishable.
    """
    out = []
    if not job_dir.is_dir():
        return out
    for path in sorted(job_dir.glob("*.mp4")):
        if "_scenes" in path.name:
            continue
        slot = spec_mod.slot_of(path)
        if slot is not None:
            out.append((slot, path))
    return sorted(out)


FAILED_STATUSES = {"failed", "error", "rejected", "cancelled"}


def _routes(platforms: list[str], preferred, override) -> dict:
    """backend name -> (backend, the platforms it will handle).

    Keyed on the backend's *name*, not the object. The registry builds a fresh instance per
    lookup, so keying on identity gave one route per platform — and for Zernio, whose whole
    advantage is that one upload serves both platforms, that would mean uploading the same
    video twice and creating two posts where there was one.
    """
    from .. import plugins

    routes: dict = {}
    for platform in platforms:
        backend = override or plugins.publisher_for(platform, preferred or ())
        key = getattr(backend, "name", None) or id(backend)
        routes.setdefault(key, (backend, []))[1].append(platform)
    return routes


def _publish_via(routes: dict, video, job_id: str, pending: list[str], decision,
                 settings, dry_run: bool):
    """Call each backend once with the platforms it owns, and merge the outcomes."""
    from ..interfaces import PlatformOutcome, PublishRequest, PublishResult
    from . import spec as spec_mod

    spec = {}
    sibling = Path(video).with_suffix(".json")
    if sibling.is_file():
        try:
            spec = spec_mod.load(sibling)
        except Exception:
            spec = {}

    outcomes, ok, post_id = [], True, ""
    for backend, owned in routes.values():
        mine = [p for p in pending if p in owned]
        if not mine:
            continue
        request = PublishRequest(
            video=str(video), job_id=job_id, platforms=tuple(mine), mode=decision.mode,
            at=None if decision.at is None else _iso(decision.at), tz=settings.timezone,
            spec=spec, dry_run=dry_run)
        res = backend.publish(request)
        post_id = post_id or res.post_id
        ok = ok and res.ok
        for platform in mine:
            found = res.outcome_for(platform)
            outcomes.append(found or PlatformOutcome(platform, res.ok))
    return PublishResult(ok=ok, mode=decision.mode, post_id=post_id,
                         outcomes=outcomes, dry_run=dry_run)


def _iso(moment):
    return sched.isoformat(moment)


def _platform_ok(result, status: dict | None) -> bool:
    """Whether one platform inside a multi-platform post actually succeeded.

    Absent per-platform detail the overall result stands — a publisher that reports
    nothing finer is taken at its word rather than guessed at.
    """
    if not result.ok:
        return False
    if not status:
        return True
    if status.get("error") or status.get("errorCategory"):
        return False
    return str(status.get("status", "")).lower() not in FAILED_STATUSES


def _job_id_for(video: Path) -> str:
    sibling = video.with_suffix(".json")
    if sibling.is_file():
        try:
            return spec_mod.job_id(spec_mod.load(sibling), sibling)
        except Exception:
            pass
    return video.stem


def _title_for(video: Path) -> str:
    sibling = video.with_suffix(".json")
    if sibling.is_file():
        try:
            meta = (spec_mod.load(sibling).get("platforms") or {}).get("youtube") or {}
            return str(meta.get("title") or "")
        except Exception:
            pass
    return video.stem


def publish_day(settings: Settings, date: str, *, dry_run: bool = False,
                publisher=None) -> RunResult:
    """Publish every rendered video for `date` onto its slot. Idempotent.

    Idempotency is per `(job_id, platform)`, so a run that posted to YouTube and failed on
    Instagram retries Instagram alone instead of posting the video twice or not at all.

    The publisher is resolved from the registry per platform, never imported here. That is
    the rule the plugin system exists to enforce: adding a publisher must not mean editing
    this file. `publisher=` is for tests and for a caller that already has one.
    """
    result = RunResult(date=date)
    job_dir = settings.job_dir(date)
    videos = rendered_videos(job_dir)
    if not videos:
        result.state = "nothing-to-publish"
        result.message = f"no rendered videos in {job_dir}"
        return result

    platforms = list(settings.publish.platforms)
    if not platforms:
        result.state = "nothing-to-publish"
        result.message = ("publish.platforms is empty, so nothing is published. "
                          f"The {len(videos)} rendered video(s) are in {job_dir}.")
        return result

    # Group the platforms by which backend reaches them, so one upload serves every
    # platform a single backend can publish to — and two backends mean two calls, not one
    # call that silently drops a platform.
    try:
        routes = _routes(platforms, settings.publish.backends, publisher)
    except Exception as exc:
        result.state = "failed"
        result.message = str(exc)
        return result

    times = sched.parse_times(settings.publish.post_times)
    run_date = dt.date.fromisoformat(date)
    pub_date = sched.publish_date(run_date, settings.publish.offset_days)
    result.publish_date = pub_date.isoformat()
    ledger = Ledger(settings.ledger_path)
    now = dt.datetime.now(dt.timezone.utc)

    for slot, video in videos:
        job_id = _job_id_for(video)
        item = VideoResult(slot=slot, video=video, job_id=job_id)
        pending = ledger.pending(job_id, platforms)
        item.skipped = [p for p in platforms if p not in pending]
        if not pending:
            item.ok = True
            result.videos.append(item)
            print(f"slot {slot}: {video.name} already published to {', '.join(item.skipped)}")
            continue
        item.platforms = pending

        slot_dt = sched.slot_datetime(pub_date, sched.slot_time(slot, times), settings.timezone)
        decision = sched.decide(slot_dt, now, settings.publish.mode)
        item.decision = decision
        when = sched.isoformat(decision.at) if decision.at else decision.mode
        print(f"slot {slot}: {video.name} -> {', '.join(pending)} @ {when}  ({decision.reason})")

        try:
            res = _publish_via(
                routes, video, job_id, pending, decision, settings, dry_run)
            # Zernio answers per platform, and can create the post while one platform
            # fails inside it (HTTP 207). Recording a blanket result would then either
            # lose a real failure or re-post a platform that already succeeded, so each
            # platform's row is written from its own status.
            ok_by_platform = {p: res.ok_for(p) for p in pending}
            item.ok = bool(res.ok) and all(ok_by_platform.values())
            item.platforms = [p for p, ok in ok_by_platform.items() if ok]
            if not dry_run:
                for platform in pending:
                    found = res.outcome_for(platform)
                    ledger.record(Entry(
                        job_id=job_id, platform=platform, ok=ok_by_platform[platform],
                        mode=decision.mode, when=when, file=str(video),
                        title=_title_for(video), post_id=res.post_id,
                        post_url=res.url_for(platform),
                        error=(found.error if found else ""),
                    ))
        except Exception as exc:
            item.ok, item.error = False, f"{type(exc).__name__}: {exc}"
            print(f"!! slot {slot} failed: {item.error}")
            if not dry_run:
                for platform in pending:
                    ledger.record_fail(job_id, platform, item.error, mode=decision.mode,
                                       file=str(video), title=_title_for(video))
        result.videos.append(item)

    result.state = "ok" if result.failed == 0 else "partial"
    if result.failed == 0 and not dry_run:
        stamp = job_dir / STAMP
        stamp.write_text(
            f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M:%SZ} "
            f"{result.published}/{len(videos)} published to {','.join(platforms)} "
            f"for {result.publish_date}\n", encoding="utf-8")
    return result


def run(settings: Settings, *, date: str | None = None, if_due: bool = False,
        skip_agent: bool = False, dry_run: bool = False) -> RunResult:
    """A full day: make the videos, then publish them."""
    settings.ensure_dirs()
    date = date or sched.today(settings.timezone).isoformat()
    job_dir = settings.job_dir(date)

    if if_due:
        if (job_dir / STAMP).is_file():
            return RunResult(date=date, state="ok", message="today is already published")
        if not sched.is_due(settings.content.run_at, settings.timezone):
            return RunResult(date=date, state="not-due",
                             message=f"not due until {settings.content.run_at} {settings.timezone}")

    try:
        lock = Lock(settings.lock_path).acquire()
    except LockBusy as exc:
        return RunResult(date=date, state="busy", message=str(exc))

    try:
        job_dir.mkdir(parents=True, exist_ok=True)
        if not skip_agent:
            status = job_dir / "STATUS.md"
            already_done = status.is_file() and status.read_text(encoding="utf-8").startswith("state: done")
            if already_done:
                print(f"agent: {status} says done, skipping")
            else:
                _run_agent(settings, date, job_dir)
        result = publish_day(settings, date, dry_run=dry_run)
        _announce(result)
        return result
    finally:
        lock.release()


def _run_agent(settings: Settings, date: str, job_dir: Path) -> None:
    from .. import agents, plugins

    if settings.agent.provider in ("", "none"):
        print("agent: disabled (agent.provider is none)")
        return
    backend = plugins.load(plugins.LLM, settings.agent.provider)
    ready = backend.available()
    if not ready:
        raise RuntimeError(f"agent backend {backend.name!r}: {ready.detail}. {ready.fix}")
    if not getattr(backend, "agentic", False):
        # Honest refusal rather than a loop pretending to be an agent. A local model that
        # cannot search the web or read files produces a worse video and a much longer
        # debugging session than one that says so.
        raise RuntimeError(
            f"{backend.name!r} cannot run the research agent (it is not agentic). Use "
            f"agent.provider: claude_code for the daily run, or write specs yourself and "
            f"use `creatoros run --skip-agent`."
        )
    code = backend.run_agent(
        agents.prompt_text(settings.agent.prompt or None),
        {"DATE": date, "OUT": str(job_dir),
         "VIDEOS_PER_DAY": settings.content.videos_per_day},
        max_turns=settings.agent.max_turns,
        cwd=str(settings.workspace_dir),
    )
    print(f"agent finished rc={code}")


def _announce(result: RunResult) -> None:
    if result.state == "nothing-to-publish":
        print(result.message)
        return
    total = len(result.videos)
    print(f"published {result.published}/{total}, failed {result.failed}")
    notify(f"{result.published}/{total} videos queued"
           + (f" · {result.failed} failed" if result.failed else ""),
           title=f"CreatorOS · {result.date}")
