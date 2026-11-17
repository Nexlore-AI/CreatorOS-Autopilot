# SPDX-License-Identifier: AGPL-3.0-or-later
"""Writing: both the agentic kind and the single-shot kind."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .availability import Availability


@dataclass(frozen=True)
class Message:
    role: str      # system | user | assistant
    content: str


@runtime_checkable
class LLM(Protocol):
    """A model backend.

    Two methods, because the two jobs are genuinely different and pretending otherwise
    would make the local path a lie.

    `complete()` is one prompt in, one string out. Every backend can do it.

    `run_agent()` is the daily run: search the web, read competitors, write files, check
    its own work over many turns. Claude Code does this natively. A 7B model on Ollama
    does not, and wrapping it in a loop that pretends it can would produce a worse video
    and a much longer debugging session. So a backend declares `agentic` honestly, and
    the pipeline picks a scripted flow — topic in, spec out via `complete()` — for
    backends that say no. The free local path is real, and it is narrower than the paid
    one. Saying so is better than discovering it.
    """

    name: str
    #: Whether `run_agent` is genuinely supported.
    agentic: bool
    paid: bool

    def available(self) -> Availability:
        ...

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 4096,
                 temperature: float = 0.7, **options) -> str:
        """One prompt, one answer."""
        ...

    def run_agent(self, prompt: str, context: dict, *, max_turns: int = 240,
                  cwd: str | None = None, timeout: float | None = None) -> int:
        """Run a multi-turn agent with file and web tools. Returns an exit code.

        Backends with `agentic = False` raise `NotImplementedError`.
        """
        ...
