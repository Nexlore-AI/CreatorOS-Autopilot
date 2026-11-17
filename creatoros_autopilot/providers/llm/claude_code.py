# SPDX-License-Identifier: AGPL-3.0-or-later
"""The research-and-scripting agent, running Claude Code headlessly.

The `claude -p` block that used to sit inside `shorts/run_shorts.sh`, now behind the `LLM`
interface next to Ollama.

This is the only backend with `agentic = True`, and the distinction is load-bearing rather
than decorative: the daily run searches the web, reads competitors, writes files and checks
its own work over many turns. A local 7B model cannot do that, and the interface lets it
say so instead of failing halfway through.

Invoked with an argv list and no shell, so a prompt containing a quote, a backtick or a
newline is data rather than something the shell gets to interpret.
"""
from __future__ import annotations

import os
import shutil
import subprocess

from ...agents import DEFAULT_PROMPT, prompt_text
from ...interfaces import Availability

ALLOWED_TOOLS = "Read,Write,Edit,Glob,Grep,Bash,WebSearch,WebFetch,Skill"
BUNDLED_PROMPT = DEFAULT_PROMPT


class ClaudeCode:
    """Claude Code, driven headlessly."""

    name = "claude_code"
    agentic = True
    paid = True

    def available(self) -> Availability:
        found = shutil.which("claude")
        if not found:
            return Availability.no(
                "the `claude` CLI is not on PATH",
                "install Claude Code (https://claude.com/claude-code), or set "
                "agent.provider: ollama for a local model",
            )
        return Availability.yes(found)

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 4096,
                 temperature: float = 0.7, **options) -> str:
        """One prompt, one answer — `claude -p` with no tools."""
        binary = shutil.which("claude")
        if not binary:
            raise FileNotFoundError("the `claude` CLI is not on PATH")
        argv = [binary, "-p", f"{system}\n\n{prompt}".strip(),
                "--max-turns", "1", "--output-format", "text"]
        done = subprocess.run(argv, capture_output=True, text=True,
                              timeout=options.get("timeout", 600))
        if done.returncode != 0:
            raise RuntimeError(f"claude -p failed ({done.returncode}): {done.stderr[:300]}")
        return done.stdout.strip()

    def run_agent(self, prompt: str, context: dict, *, max_turns: int = 240,
                  cwd: str | None = None, timeout: float | None = None) -> int:
        return run(context, prompt=None, max_turns=max_turns, cwd=cwd, timeout=timeout,
                   body=prompt)


def available() -> bool:
    return shutil.which("claude") is not None


def run(context: dict, *, prompt: str | os.PathLike | None = None, max_turns: int = 240,
        cwd: str | os.PathLike | None = None, timeout: float | None = None,
        body: str | None = None) -> int:
    """Run one agent pass. Returns the process exit code.

    `context` is appended to the prompt as `KEY=value` lines — the date, the job
    directory, how many videos to make. Keeping it out of the prompt file means the same
    prompt serves every run and every user.
    """
    binary = shutil.which("claude")
    if not binary:
        raise FileNotFoundError(
            "the `claude` CLI was not found on PATH. Install Claude Code, or set "
            "agent.provider to another backend."
        )
    body = body if body is not None else prompt_text(prompt)
    tail = "\n".join(f"{k}={v}" for k, v in context.items())
    argv = [binary, "-p", f"{body}\n\n{tail}",
            "--allowedTools", ALLOWED_TOOLS,
            "--permission-mode", "acceptEdits",
            "--max-turns", str(max_turns),
            "--output-format", "text"]
    done = subprocess.run(argv, cwd=str(cwd) if cwd else None, timeout=timeout)
    return done.returncode
