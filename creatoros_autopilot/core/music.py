# SPDX-License-Identifier: AGPL-3.0-or-later
"""Choosing the music bed.

Lived inside the ElevenLabs module, which made "rotate the least recently used track"
— a decision with nothing to do with any vendor — unavailable to every other backend.

Library rotation is the default and needs no provider at all: a folder of tracks the user
owns, played least-recently-used so a week of videos does not open with the same eight bars.
Generation is opt-in, costs credits, and falls back to the library when it fails, because a
missing bed is a far smaller problem than a failed run.

No music ships with CreatorOS. Audio whose licence we cannot vouch for would make the
project undistributable, and a video posted with an unlicensed track is the user's problem
to answer for.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import spec as spec_mod

USED = "music_used.json"


def library(music_dir: str | Path) -> list[str]:
    music_dir = Path(music_dir)
    return sorted(p.name for p in music_dir.glob("*.mp3")) if music_dir.is_dir() else []


def choose(spec: dict, spec_path: str | Path, *, music_dir: str | Path,
           mode: str = "library", backend=None) -> str:
    """Pick a bed, write it into the spec, and return its name ("" for none).

    A spec that already names a track keeps it — re-rendering a video must not silently
    change its music.
    """
    if spec.get("music"):
        return str(spec["music"])
    if mode == "none":
        return ""

    music_dir = Path(music_dir)
    music_dir.mkdir(parents=True, exist_ok=True)

    if mode == "generate" and spec.get("music_prompt") and backend is not None:
        composer = getattr(backend, "compose", None)
        if composer is None:
            print(f"!! {getattr(backend, 'name', 'backend')} cannot compose music; "
                  f"using the library")
        else:
            name = f"gen_{spec.get('id', 'short')}.mp3"
            try:
                composer(spec["music_prompt"], music_dir / name)
                spec["music"] = name
            except Exception as exc:
                print(f"!! music generation failed ({type(exc).__name__}); using the library")

    if not spec.get("music"):
        tracks = library(music_dir)
        if not tracks:
            print(f"no tracks in {music_dir} — rendering without a music bed")
            return ""
        used_path = music_dir / USED
        try:
            used = json.loads(used_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            used = {}
        # Least recently used: a track never used sorts first because "" < any id.
        spec["music"] = min(tracks, key=lambda t: used.get(t, ""))
        used[spec["music"]] = str(spec.get("id", ""))
        used_path.write_text(json.dumps(used, indent=1), encoding="utf-8")

    spec_mod.save(spec, spec_path)
    return str(spec.get("music") or "")
