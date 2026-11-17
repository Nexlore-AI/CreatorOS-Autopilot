# SPDX-License-Identifier: AGPL-3.0-or-later
"""The language-mix gate: how much of a second language is in the narration.

This gate exists because of one specific, measurable failure. The Nexlore narration was
being written in near-pure Hindi — about 15–25% English across the 2026-11-15 and 11-16
Shorts — which reads as formal and textbook-ish to the Gen-Z Indian developers the channel
is for. They say "loss", "step", "slow", "blow up", not "पहाड़", "कीमत", "रेंगती".

That is a judgement about one channel, not a universal truth, which is why it is a
registered, opt-in gate rather than something the pipeline does to everyone. A
Spanish-language channel drops it. A channel that mixes differently supplies its own
lexicon and ratio through its brand, and the same machinery works.

The band is empirical. Rewriting two 2026-11-16 scenes as naturally as possible landed at
44% and 39%, and that is about the ceiling: the Hindi skeleton (है, हो जाती है, तो, रखो) is
the grammar, and replacing it to chase a higher number stops sounding like speech.

The ratio is a **lower bound**: a word counts as the secondary language only if it is Latin
script or in the lexicon, so an unrecognised loanword reads as primary and pushes the number
down. A near-miss is worth re-reading rather than trusting.
"""
from __future__ import annotations

import re

from ...interfaces import GateResult
from ...quality.langmix import ENGLISH, PREFER_ENGLISH, WORD


class LanguageMix:
    """Measures the secondary-language share of a spec's narration."""

    name = "langmix"
    advisory = False

    def __init__(self, lexicon=None, prefer: dict | None = None,
                 low: float = 0.38, high: float = 0.55):
        self.lexicon = set(lexicon) if lexicon is not None else set(ENGLISH)
        self.prefer = dict(prefer) if prefer is not None else dict(PREFER_ENGLISH)
        self.low, self.high = low, high

    @classmethod
    def from_brand(cls, kit) -> "LanguageMix | None":
        """Build the gate from a brand's `language` block, or None if it has no mix.

        This is what makes Hinglish a setting rather than a fact about the pipeline. The
        band used to be two module constants and the lexicon a literal set; a channel that
        mixed Spanish into Portuguese, or did not mix at all, had no way through. A brand
        with no `secondary` or no `ratio` disables the gate entirely, which is the right
        answer for a single-language channel rather than a check it always fails.
        """
        language = getattr(kit, "language", None)
        if not language or not language.secondary or language.ratio is None:
            return None
        return cls(lexicon=set(language.lexicon) or None,
                   low=max(0.0, language.ratio - language.tolerance),
                   high=min(1.0, language.ratio + language.tolerance))

    def analyse(self, lines: list[str]) -> tuple[list[str], list[str], list[tuple[str, str]]]:
        words, secondary, flagged = [], [], []
        for line in lines:
            for word in WORD.findall(line):
                words.append(word)
                if re.fullmatch(r"[A-Za-z]+", word) or word in self.lexicon:
                    secondary.append(word)
                    continue
                for formal, better in self.prefer.items():
                    # Match the stem too, so "हिस्सों" and "हिस्सा" both flag.
                    if word == formal or word.startswith(formal):
                        flagged.append((word, better))
                        break
        return words, secondary, flagged

    def check(self, spec: dict, **options) -> GateResult:
        lines = [(scene.get("voice") or "").strip()
                 for scene in (spec.get("scenes") or [])]
        lines = [line for line in lines if line]
        if not lines:
            return GateResult(self.name, ok=True, messages=["no narration to measure"])

        low = options.get("low", self.low)
        high = options.get("high", self.high)
        words, secondary, flagged = self.analyse(lines)
        ratio = len(secondary) / len(words) if words else 0.0

        detail = {}
        for index, line in enumerate(lines, 1):
            scene_words, scene_secondary, _ = self.analyse([line])
            if scene_words:
                detail[index] = f"{len(scene_secondary) / len(scene_words):.0%}"

        messages = []
        in_band = low <= ratio <= high
        if ratio < low:
            messages.append(
                f"{ratio:.0%} secondary-language words, below the {low:.0%} floor — this "
                f"will read as formal. Swap concept nouns and verbs, keep the grammar.")
        elif ratio > high:
            messages.append(
                f"{ratio:.0%} secondary-language words, above the {high:.0%} ceiling — this "
                f"reads as the second language with the first used as glue.")
        for word, better in dict(flagged).items():
            messages.append(f"'{word}' → say '{better}'")

        return GateResult(
            gate=self.name,
            ok=in_band and not flagged,
            score=round(ratio * 100, 1),
            target=f"{low:.0%}–{high:.0%}",
            messages=messages,
            detail=detail,
        )
