# SPDX-License-Identifier: AGPL-3.0-or-later
"""Publish-date and slot arithmetic.

This module exists because the shell pipeline it replaces got this wrong in a way that
could not be seen. `shorts/run_shorts.sh` computed the publish date and the slot timestamp
with BSD `date`:

    PUBDATE="$(TZ=... date -j -v+${OFFSET}d -f %Y-%m-%d "$DATE" +%F 2>/dev/null || echo "$DATE")"
    TARGET_TS="$(TZ=... date -j -f '%Y-%m-%dT%H:%M:%S' "$TARGET" +%s 2>/dev/null || echo 0)"

`date -j -v+Nd` is BSD-only. On GNU coreutils both commands fail, and both failures are
swallowed by a fallback that keeps going with a wrong value:

  * the first falls back to today, so the publish offset silently disappears;
  * the second falls back to 0, so `TARGET_TS > NOW_TS` is false for every slot, every
    video takes the "slot already passed" branch, and the whole day publishes immediately
    instead of being scheduled.

So on Linux that pipeline did not fail — it posted four videos at once. Everything here is
pure Python with `zoneinfo`, every slot decision is a value that can be asserted on, and
`tests/test_schedule.py` holds the regression: a slot in the future must never decide
`now`.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

try:
    from zoneinfo import ZoneInfo
except ImportError as exc:  # pragma: no cover - 3.9 and below, which we do not support
    raise ImportError("CreatorOS needs Python 3.10+ for zoneinfo") from exc

DRAFT, SCHEDULE, NOW = "draft", "schedule", "now"


def zone(name: str) -> ZoneInfo:
    """A timezone, with an error that says how to fix it.

    Windows ships no system tz database, so this is the usual first failure there. The
    `tzdata` wheel is declared as a Windows dependency in pyproject.toml; this message is
    for the case where someone installed without it.
    """
    try:
        return ZoneInfo(name)
    except Exception as exc:
        raise ValueError(
            f"unknown timezone {name!r} ({exc}). On Windows this usually means the tz "
            "database is missing: pip install tzdata"
        ) from exc


def today(tz_name: str, now: dt.datetime | None = None) -> dt.date:
    """The current date in `tz_name` — the pipeline's notion of "today"."""
    return (now or dt.datetime.now(dt.timezone.utc)).astimezone(zone(tz_name)).date()


def parse_times(times: str | list[str]) -> list[str]:
    """Slot times as a list of "HH:MM", from a list or a comma string."""
    if isinstance(times, str):
        times = times.split(",")
    out = []
    for raw in times:
        s = str(raw).strip()
        if not s:
            continue
        try:
            h, m = s.split(":")
            out.append(f"{int(h):02d}:{int(m):02d}")
        except Exception:
            raise ValueError(f"bad slot time {raw!r}: expected HH:MM") from None
    if not out:
        raise ValueError("no slot times configured")
    return out


def slot_time(index: int, times: list[str]) -> str:
    """The slot for a 1-based video number, clamped to the last entry.

    Matches the shell's `${TIMES[$((i-1))]:-${TIMES[-1]}}`: more videos than slots reuses
    the final slot rather than failing.
    """
    if index < 1:
        raise ValueError(f"slot index is 1-based, got {index}")
    return times[index - 1] if index <= len(times) else times[-1]


def publish_date(run_date: dt.date, offset_days: int) -> dt.date:
    """The date whose slots this run publishes onto.

    Rendering four videos takes hours, so they are scheduled onto a later day and never
    race the clock. `offset_days=0` means same-day, where a slot already behind us
    publishes immediately.
    """
    if offset_days < 0:
        raise ValueError(f"offset_days must be >= 0, got {offset_days}")
    return run_date + dt.timedelta(days=offset_days)


def slot_datetime(day: dt.date, hhmm: str, tz_name: str) -> dt.datetime:
    """An aware datetime for a wall-clock slot on a given day.

    Built with `datetime.combine(..., tzinfo=...)` so the offset is resolved for that
    actual date, which matters in any zone that observes DST. A wall time that does not
    exist on a spring-forward date resolves per `fold=0` rather than raising; it is off by
    the DST shift for that one slot on that one day, which is the behaviour a scheduler
    wants over a crash.
    """
    hour, minute = (int(x) for x in hhmm.split(":"))
    return dt.datetime.combine(day, dt.time(hour, minute), tzinfo=zone(tz_name))


@dataclass(frozen=True)
class SlotDecision:
    """What to do with one rendered video."""

    mode: str                    # DRAFT | SCHEDULE | NOW
    at: dt.datetime | None       # the scheduled instant, only when mode == SCHEDULE
    reason: str

    @property
    def publishes_immediately(self) -> bool:
        return self.mode == NOW


def decide(
    slot_dt: dt.datetime,
    now: dt.datetime | None = None,
    publish_mode: str = "draft",
) -> SlotDecision:
    """Draft, schedule for later, or publish now — for one slot.

    `publish_mode` is the user's setting: "draft" never publishes (the safe default),
    "auto" schedules a future slot and publishes a slot already behind us.
    """
    if publish_mode not in ("draft", "auto"):
        raise ValueError(f"publish mode must be 'draft' or 'auto', got {publish_mode!r}")
    if publish_mode == "draft":
        return SlotDecision(DRAFT, None, "publish mode is draft")

    if slot_dt.tzinfo is None:
        raise ValueError("slot_dt must be timezone-aware")
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    if slot_dt > now:
        return SlotDecision(SCHEDULE, slot_dt, f"slot is {_pretty(slot_dt - now)} away")
    return SlotDecision(NOW, None, f"slot passed {_pretty(now - slot_dt)} ago")


def is_due(run_at: str, tz_name: str, now: dt.datetime | None = None) -> bool:
    """Whether a scheduled run should go ahead, for the `--if-due` gate.

    The shell version compared the machine's local clock against the run hour while
    deriving "today" in the configured zone. Those agree only when the machine happens to
    sit in that zone. Here both use `tz_name`, so the gate means the same thing on a
    laptop that travels and on a server in UTC.
    """
    local = (now or dt.datetime.now(dt.timezone.utc)).astimezone(zone(tz_name))
    hour, minute = (int(x) for x in run_at.split(":"))
    return (local.hour * 60 + local.minute) >= (hour * 60 + minute)


def _pretty(delta: dt.timedelta) -> str:
    total = int(delta.total_seconds())
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    if total < 86400:
        return f"{total // 3600}h{(total % 3600) // 60:02d}m"
    return f"{total // 86400}d{(total % 86400) // 3600}h"


def isoformat(moment: dt.datetime) -> str:
    """The wall-clock ISO string publishers expect, without an offset suffix.

    Zernio takes `scheduledFor` plus a separate `timezone` field, so the timestamp must be
    naive wall time; sending an offset-aware string double-applies the zone.
    """
    return moment.strftime("%Y-%m-%dT%H:%M:%S")
