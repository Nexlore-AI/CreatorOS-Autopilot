# SPDX-License-Identifier: AGPL-3.0-or-later
"""The research-and-scripting agent, running Claude Code headlessly.

This is the `claude -p` block that used to sit inside `shorts/run_shorts.sh`. It is still
the only agent backend; Phase 2 puts it behind an `LLM` interface next to Ollama, which is
what makes a fully local run possible.

Invoked with an argv list and no shell, so a prompt containing a quote, a backtick or a
newline is data rather than something the shell gets to interpret.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ALLOWED_TOOLS = "Read,Write,Edit,Glob,Grep,Bash,WebSearch,WebFetch,Skill"
BUNDLED_PROMPT = Path(__file__).parent / "prompts" / "shorts_daily.md"


def available() -> bool:
    return shutil.which("claude") is not None


def prompt_text(path: str | os.PathLike | None = None) -> str:
    source = Path(path) if path else BUNDLED_PROMPT
    if not source.is_file():
        raise FileNotFoundError(f"no agent prompt at {source}")
    return source.read_text(encoding="utf-8")


def run(context: dict, *, prompt: str | os.PathLike | None = None, max_turns: int = 240,
        cwd: str | os.PathLike | None = None, timeout: float | None = None) -> int:
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
    body = prompt_text(prompt)
    tail = "\n".join(f"{k}={v}" for k, v in context.items())
    argv = [binary, "-p", f"{body}\n\n{tail}",
            "--allowedTools", ALLOWED_TOOLS,
            "--permission-mode", "acceptEdits",
            "--max-turns", str(max_turns),
            "--output-format", "text"]
    done = subprocess.run(argv, cwd=str(cwd) if cwd else None, timeout=timeout)
    return done.returncode
