# SPDX-License-Identifier: AGPL-3.0-or-later
"""Finding things worth making a video about."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .availability import Availability


@dataclass(frozen=True)
class Item:
    """One candidate topic, from one source."""

    title: str
    url: str
    summary: str = ""
    published: dt.datetime | None = None
    source: str = ""
    score: float = 0.0            # the source's own notion of popularity, if it has one
    extra: dict = field(default_factory=dict)

    def key(self) -> str:
        """Identity for deduplication across sources — the same paper on arXiv and on
        Hugging Face must not become two videos."""
        return (self.url or self.title).strip().lower().rstrip("/")


@runtime_checkable
class ResearchSource(Protocol):
    """Somewhere topics come from.

    Research used to be whatever the agent decided to do inside a prompt, which meant
    none of it could be reused, swapped or tested. A source here is a plain function of
    (since, limit, query) -> items, so it can be unit tested against a recorded response
    and a user in a different niche can register their own.
    """

    name: str

    def available(self) -> Availability:
        ...

    def fetch(self, *, since: dt.datetime | None = None, limit: int = 20,
              query: str = "") -> list[Item]:
        """Recent items, newest first. Network failures raise; empty is a valid answer."""
        ...
