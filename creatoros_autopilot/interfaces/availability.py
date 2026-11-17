# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whether a provider can actually run, and what to do if it cannot."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Availability:
    """The answer to "can I use this?", with enough detail to act on.

    A bool would be cheaper and useless. Half the first-run experience of a tool like this
    is the gap between "it didn't work" and "it didn't work *because* ELEVENLABS_API_KEY
    is unset, and here is where to put it", so every provider is required to produce the
    second kind of answer.

    `detail` says what is true now ("set", "not on PATH", "3 voices"). `fix` is the single
    next action, written so it can be pasted into a terminal or a config file.
    """

    ok: bool
    detail: str = ""
    fix: str = ""

    def __bool__(self) -> bool:
        return self.ok

    @classmethod
    def yes(cls, detail: str = "") -> "Availability":
        return cls(True, detail)

    @classmethod
    def no(cls, detail: str, fix: str = "") -> "Availability":
        return cls(False, detail, fix)
