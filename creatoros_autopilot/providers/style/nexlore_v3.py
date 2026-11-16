# SPDX-License-Identifier: AGPL-3.0-or-later
"""The `nexlore-v3` house style, as a registered renderer.

The engine itself is unchanged — this is the adapter that lets a style be chosen by name
instead of being the only thing the pipeline can draw. It also publishes the scene and
visual vocabulary, so `core.spec` validates a spec against the style it will actually be
rendered with rather than against a hard-coded list.

Pulling the brand out of the engine — the wordmark drawn into every frame, the palette, the
fonts, the icon — is Phase 3. What Phase 2 fixes is that adding a *second* style no longer
means editing core.
"""
from __future__ import annotations

from pathlib import Path

from ...core import media
from ...interfaces import Availability
from ...render import engine


class NexloreV3:
    name = "nexlore-v3"
    scene_types = tuple(engine.SCENES)
    visual_types = tuple(engine.VISUALS)
    width, height = engine.W, engine.H

    def available(self) -> Availability:
        try:
            found = media.require_all()
        except media.FFmpegMissing as exc:
            return Availability.no(str(exc).split(".")[0], media.INSTALL_HINT[
                "macos" if media.is_macos() else "windows" if media.is_windows() else "linux"])
        missing = [name for name in ("InterDisplay-Black.otf", "DejaVuSansMono-Bold.ttf")
                   if not (Path(engine.FONTS) / name).is_file()]
        if missing:
            return Availability.no(f"missing bundled fonts: {', '.join(missing)}",
                                   "reinstall creatoros-autopilot")
        return Availability.yes(f"{self.width}x{self.height} · ffmpeg {found['ffmpeg']}")

    def render(self, spec: dict, spec_path: Path, out: Path | None = None) -> Path:
        return Path(engine.run("render", spec_path, str(out) if out else None))

    def preview(self, spec: dict, spec_path: Path, out: Path | None = None) -> Path:
        return Path(engine.run("preview", spec_path, str(out) if out else None))
