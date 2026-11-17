# SPDX-License-Identifier: AGPL-3.0-or-later
"""Content pillars: the recurring series a channel publishes, and when each is due.

A pillar is one strand of a channel — "paper of the day", "myth of the week" — with its own
brief, its own slot and its own cadence. They existed as `pillars/*.json` that described
all of this and that **nothing read**: `schedule_ist` was declared and launchd ran one
daily job regardless, and `order: sequential` pointed at a state file no code opened.

So this module is the part that was missing rather than a rewrite of the part that was
there. It answers one question — what is due on this date — and it answers it for daily,
weekday, weekend, weekly and monthly cadences, which is what lets a channel run three times
a week without someone editing a crontab.

Sequential pillars pull their next topic from a cursor so a week adds up to one subject.
The cursor lives in the workspace, not the repo: it is state, not content.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
CADENCES = ("daily", "weekdays", "weekends", "weekly", "monthly")


class PillarError(ValueError):
    pass


@dataclass
class Pillar:
    name: str
    display: str = ""
    slot: int = 1
    brief: str = ""
    cadence: str = "daily"
    days: tuple = ()              # explicit weekdays; overrides cadence when present
    day_of_month: int = 1         # for cadence: monthly
    at: str = ""                  # slot time; empty uses the global post_times
    order: str = "random"         # random | sequential
    series: tuple = ()            # the arc, for sequential pillars
    platforms: tuple = ()
    source: str = ""

    def due_on(self, date: dt.date) -> bool:
        """Whether this pillar runs on `date`."""
        if self.days:
            return DAYS[date.weekday()] in self.days
        if self.cadence == "daily":
            return True
        if self.cadence == "weekdays":
            return date.weekday() < 5
        if self.cadence == "weekends":
            return date.weekday() >= 5
        if self.cadence == "weekly":
            # Anchored to Monday rather than "every 7th run", so a missed day does not
            # shift the whole schedule forward for ever.
            return date.weekday() == 0
        if self.cadence == "monthly":
            return date.day == self.day_of_month
        raise PillarError(f"{self.name}: unknown cadence {self.cadence!r}")


def _load_one(path: Path) -> Pillar:
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text) if path.suffix in (".yaml", ".yml") else json.loads(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise PillarError(f"{path}: {exc}") from None
    if not isinstance(data, dict):
        raise PillarError(f"{path}: a pillar must be a mapping")

    known = {"display", "slot", "brief", "cadence", "days", "day_of_month", "at",
             "order", "series", "platforms", "spine", "schedule_ist", "voice_lang",
             "also_story", "resource"}
    unknown = set(data) - known
    if unknown:
        raise PillarError(f"{path}: unknown key(s) {sorted(unknown)}")

    cadence = str(data.get("cadence") or "daily").lower()
    days = data.get("days")
    if isinstance(days, str):
        # The old files used `days: "daily"` / `"weekends"`, which is a cadence.
        if days.lower() in CADENCES:
            cadence, days = days.lower(), ()
        else:
            days = [d.strip().lower()[:3] for d in days.split(",") if d.strip()]
    days = tuple(d.lower()[:3] for d in (days or ()))
    bad = [d for d in days if d not in DAYS]
    if bad:
        raise PillarError(f"{path}: unknown day(s) {bad}; use {DAYS}")
    if cadence not in CADENCES:
        raise PillarError(f"{path}: unknown cadence {cadence!r}; use one of {CADENCES}")

    return Pillar(
        name=path.stem,
        display=str(data.get("display") or path.stem),
        slot=int(data.get("slot") or 1),
        brief=str(data.get("brief") or ""),
        cadence=cadence,
        days=days,
        day_of_month=int(data.get("day_of_month") or 1),
        at=str(data.get("at") or data.get("schedule_ist") or ""),
        order=str(data.get("order") or "random").lower(),
        series=tuple(data.get("series") or ()),
        platforms=tuple(data.get("platforms") or ()),
        source=str(path),
    )


def load_all(directory: str | Path) -> list:
    directory = Path(directory)
    if not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.iterdir()):
        if path.suffix in (".yaml", ".yml", ".json"):
            found.append(_load_one(path))
    return sorted(found, key=lambda p: (p.slot, p.name))


def due(pillars: list, date: dt.date, names: list | None = None) -> list:
    """The pillars that run on `date`, in slot order.

    `names` restricts to a configured subset: a repo may ship ten example pillars while a
    channel runs four.
    """
    chosen = [p for p in pillars if not names or p.name in names]
    return [p for p in chosen if p.due_on(date)]


# ------------------------------------------------------------------ sequential state
def cursor_path(workspace: str | Path) -> Path:
    return Path(workspace) / "series.json"


def next_topic(pillar: Pillar, workspace: str | Path, advance: bool = True) -> str:
    """The next entry in a sequential pillar's arc, or "" when it has none.

    Returning "" rather than wrapping is deliberate: a finished arc should prompt the
    writer for more, not quietly start the series again from episode one.
    """
    if pillar.order != "sequential" or not pillar.series:
        return ""
    path = cursor_path(workspace)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    index = int(state.get(pillar.name, 0))
    if index >= len(pillar.series):
        return ""
    topic = str(pillar.series[index])
    if advance:
        state[pillar.name] = index + 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=1), encoding="utf-8")
    return topic
