# SPDX-License-Identifier: AGPL-3.0-or-later
"""The checks a script has to pass before it is worth rendering."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class GateResult:
    """Whether a spec passes, and what to change if not."""

    gate: str
    ok: bool
    score: float | None = None
    target: str = ""
    messages: list[str] = field(default_factory=list)
    #: Per-scene detail, when the gate has it — `{scene_index: "what is wrong"}`.
    detail: dict = field(default_factory=dict)
    #: Set by the gate runner from the gate's own `advisory` flag, so a caller holding
    #: only results can still tell a warning from a failure.
    advisory: bool = False

    def summary(self) -> str:
        head = f"{self.gate}: {'pass' if self.ok else 'FAIL'}"
        if self.score is not None:
            head += f" ({self.score:g}{' / ' + self.target if self.target else ''})"
        return head


@runtime_checkable
class Gate(Protocol):
    """A quality check with an opinion.

    These are the part of the pipeline that encodes a house style most directly — the
    language-mix gate exists because narration written in formal Hindi reads wrong to the
    audience Nexlore is for. That is a judgement about one channel, not a universal
    truth, so gates are registered and opt-in rather than compiled into the run. A
    Spanish-language user drops `langmix` and adds their own.
    """

    name: str
    #: A gate marked advisory reports but never blocks.
    advisory: bool

    def check(self, spec: dict, **options) -> GateResult:
        ...
