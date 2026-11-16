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
    """
    from ..providers.publish import zernio

    publisher = publisher or zernio
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
            res = publisher.publish(
                str(video), pending, decision.mode,
                at=sched.isoformat(decision.at) if decision.at else None,
                tz=settings.timezone,
                yt_visibility=settings.publish.youtube.visibility,
                yt_category=settings.publish.youtube.category_id,
                dry_run=dry_run,
            )
            # Zernio answers per platform, and can create the post while one platform
            # fails inside it (HTTP 207). Recording a blanket result would then either
            # lose a real failure or re-post a platform that already succeeded, so each
            # platform's row is written from its own status.
            statuses = {e.get("platform"): e for e in (getattr(res, "platforms", None) or [])}
            ok_by_platform = {p: _platform_ok(res, statuses.get(p)) for p in pending}
            item.ok = bool(res.ok) and all(ok_by_platform.values())
            item.platforms = [p for p, ok in ok_by_platform.items() if ok]
            if not dry_run:
                for platform in pending:
                    entry = statuses.get(platform) or {}
                    ledger.record(Entry(
                        job_id=job_id, platform=platform, ok=ok_by_platform[platform],
                        mode=decision.mode, when=when, file=str(video),
                        title=_title_for(video), post_id=getattr(res, "post_id", ""),
                        post_url=res.url_for(platform),
                        error=str(entry.get("error") or entry.get("errorCategory") or ""),
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
    if settings.agent.provider in ("", "none"):
        print("agent: disabled (agent.provider is none)")
        return
    if settings.agent.provider != "claude_code":
        raise ValueError(
            f"unknown agent provider {settings.agent.provider!r}. Phase 1 ships "
            "'claude_code' and 'none'; more arrive with the plugin system in Phase 2."
        )
    from ..agents import claude_code

    code = claude_code.run(
        {"DATE": date, "OUT": str(job_dir), "VIDEOS_PER_DAY": settings.content.videos_per_day},
        prompt=settings.agent.prompt or None,
        max_turns=settings.agent.max_turns,
        cwd=settings.workspace_dir,
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
