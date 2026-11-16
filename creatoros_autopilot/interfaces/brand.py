# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who the video is by: name, colours, type, voice, language.

Phase 3 fills this in and pulls the Nexlore wordmark, palette and fonts out of the
renderer. It is defined here, in Phase 2, so the `Style` interface can take a brand from
the start rather than being changed again later.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LanguageMix:
    """What language the narration is in, and how much of another is mixed into it.

    Hinglish — Hindi grammar with roughly 45% English vocabulary, written in Devanagari
    so the TTS does not switch accent mid-sentence — was hard-coded in a lexicon and a
    gate. It is one point in this space, not the shape of the space.
    """

    primary: str = "en"
    secondary: str = ""
    #: Target share of `secondary` words, 0..1. None disables the mix gate entirely.
    ratio: float | None = None
    tolerance: float = 0.085
    #: The script the narration is written in, which may differ from the language — the
    #: Devanagari rule is a TTS requirement, not a language choice.
    script: str = ""
    #: Loanwords of `secondary` as spelled in `script`, for the mix gate to recognise.
    lexicon: tuple[str, ...] = ()


@dataclass
class Brand:
    """A channel's identity, as data."""

    name: str = "CreatorOS"
    tagline: str = ""
    logo: str = ""                      # path; empty renders a wordmark instead
    palette: dict = field(default_factory=dict)
    fonts: dict = field(default_factory=dict)
    cta: str = ""                       # the follow button's label
    language: LanguageMix = field(default_factory=LanguageMix)

    def cta_label(self) -> str:
        return self.cta or f"Follow {self.name}"
