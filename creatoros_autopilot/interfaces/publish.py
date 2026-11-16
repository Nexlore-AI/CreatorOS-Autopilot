# SPDX-License-Identifier: AGPL-3.0-or-later
"""Getting a finished video onto a platform."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .availability import Availability

DRAFT, SCHEDULE, NOW = "draft", "schedule", "now"


@dataclass(frozen=True)
class PublishRequest:
    """One video, going to one or more platforms, once."""

    video: str
    job_id: str
    platforms: tuple[str, ...]
    mode: str = DRAFT                 # draft | schedule | now
    at: str | None = None             # naive wall-clock ISO8601, for mode == schedule
    tz: str = "UTC"
    spec: dict = field(default_factory=dict)
    dry_run: bool = False


@dataclass
class PlatformOutcome:
    """What happened on one platform.

    Per-platform rather than per-post because a multi-platform publisher can create the
    post and still fail one platform inside it. Collapsing that into a single bool either
    hides a real failure or re-posts the platform that worked.
    """

    platform: str
    ok: bool
    url: str = ""
    post_id: str = ""
    error: str = ""


@dataclass
class PublishResult:
    ok: bool
    mode: str = DRAFT
    post_id: str = ""
    outcomes: list[PlatformOutcome] = field(default_factory=list)
    dry_run: bool = False
    error: str = ""

    def outcome_for(self, platform: str) -> PlatformOutcome | None:
        return next((o for o in self.outcomes if o.platform == platform), None)

    def ok_for(self, platform: str) -> bool:
        outcome = self.outcome_for(platform)
        return self.ok if outcome is None else outcome.ok

    def url_for(self, platform: str) -> str:
        outcome = self.outcome_for(platform)
        return outcome.url if outcome else ""


@runtime_checkable
class Publisher(Protocol):
    """A way to get a video onto one or more platforms.

    `platforms` is what this backend can reach, so the pipeline can route: Zernio claims
    instagram and youtube, the YouTube Data API backend claims only youtube, and
    `local_folder` claims whatever you ask of it. Where two backends claim the same
    platform, config decides.
    """

    name: str
    platforms: tuple[str, ...]
    #: Whether using this needs a paid or third-party account beyond the platform itself.
    third_party: bool

    def available(self) -> Availability:
        ...

    def publish(self, request: PublishRequest) -> PublishResult:
        """Do it. Must honour `dry_run` by making no network call that changes anything."""
        ...

    def requires(self, platform: str) -> tuple[str, ...]:
        """Keys this backend needs under `spec["platforms"][platform]`.

        Optional. It exists so that "YouTube needs a title, a description and tags" is
        knowledge the YouTube publisher holds, not something `core.spec` hard-codes — core
        validated against a literal list of platform names until this method existed, which
        meant adding a platform meant editing core.
        """
        ...
