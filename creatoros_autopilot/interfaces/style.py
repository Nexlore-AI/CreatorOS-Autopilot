# SPDX-License-Identifier: AGPL-3.0-or-later
"""How a spec is turned into pixels."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from .availability import Availability


@runtime_checkable
class Style(Protocol):
    """A renderer.

    The current look ships as `nexlore-v3`. Registering a style rather than editing the
    engine is what lets someone build a different visual language — a different scene
    vocabulary, a different motion system — without forking.

    `scene_types` and `visual_types` are what `core.spec` validates against, so a style
    with its own vocabulary gets its specs validated correctly instead of against ours.
    """

    name: str
    scene_types: tuple[str, ...]
    visual_types: tuple[str, ...]
    width: int
    height: int

    def available(self) -> Availability:
        ...

    def render(self, spec: dict, spec_path: Path, out: Path | None = None,
               brand=None) -> Path:
        """Spec to video. Writes a cover frame beside the output.

        `brand` is a loaded brand kit, or None for the style's own default. A style is free
        to ignore it, but one that draws a name or a palette should not hard-code either.
        """
        ...

    def preview(self, spec: dict, spec_path: Path, out: Path | None = None,
                brand=None) -> Path:
        """One still per scene as a contact sheet — the cheap check before a long render."""
        ...
