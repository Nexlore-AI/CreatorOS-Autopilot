# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning a line of narration into an audio file."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from .availability import Availability


@dataclass(frozen=True)
class Voice:
    """One voice a backend can speak in."""

    id: str
    name: str = ""
    languages: tuple[str, ...] = ()
    gender: str = ""
    detail: str = ""


@runtime_checkable
class TTS(Protocol):
    """A narration backend.

    Deliberately narrow: one line in, one audio file out. Everything a backend would
    otherwise reimplement — walking the scenes, hashing each line so an unchanged one is
    not re-billed, deleting clips for scenes that were removed — lives once in
    `core.narrate`. That caching was ElevenLabs-specific and is the main reason a
    re-render did not cost money; a plugin author should not have to know it exists to
    benefit from it.
    """

    name: str
    #: Whether this backend costs money per character. `creatoros doctor` warns on paid
    #: backends, and the test suite refuses to call one.
    paid: bool

    def available(self) -> Availability:
        """Can this speak right now — binary installed, key set, model pulled?"""
        ...

    def synthesize(self, text: str, out_path: Path, *, voice: str = "",
                   model: str = "", **options) -> Path:
        """Write `text` as audio to `out_path` and return it.

        Must write atomically — a half-written file that looks complete is worse than no
        file, because the cache will treat it as done and the video will ship with a
        truncated line.
        """
        ...

    def voices(self) -> list[Voice]:
        """The voices on offer. May be empty if the backend cannot enumerate them."""
        ...
