# SPDX-License-Identifier: AGPL-3.0-or-later
"""Research and scripting prompts.

The prompt is the instruction; the LLM backend is the thing that runs it. Keeping the
loader here rather than inside a provider is what lets `core.pipeline` fetch a prompt
without importing Claude Code — which it used to, and which made the plugin boundary a
polite fiction.
"""
from pathlib import Path

PROMPTS = Path(__file__).parent / "prompts"
DEFAULT_PROMPT = PROMPTS / "shorts_daily.md"


def prompt_text(path=None) -> str:
    """The agent prompt: the one given, else the bundled default."""
    source = Path(path) if path else DEFAULT_PROMPT
    if not source.is_file():
        raise FileNotFoundError(f"no agent prompt at {source}")
    return source.read_text(encoding="utf-8")
