# SPDX-License-Identifier: AGPL-3.0-or-later
"""Walking a spec's scenes and voicing the ones that changed.

This was inside the ElevenLabs module, which meant the behaviour that keeps a re-render
from re-billing the whole video — hash each line, skip the ones whose audio is already
on disk — belonged to one backend. Every other backend would have had to reimplement it,
or quietly not have it.

It is provider-independent, so it lives here and every `TTS` plugin gets it for free. A
plugin author writes one method, `synthesize(text, path)`, and inherits the caching, the
stale-clip cleanup and the manifest.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import spec as spec_mod

MANIFEST = "manifest.json"


@dataclass
class NarrationResult:
    written: list[int] = field(default_factory=list)
    unchanged: list[int] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)      # scenes with no voice line
    problems: list[str] = field(default_factory=list)
    out_dir: Path | None = None

    @property
    def ok(self) -> bool:
        return not self.problems

    def summary(self) -> str:
        return (f"{len(self.written)} written, {len(self.unchanged)} unchanged, "
                f"{len(self.skipped)} silent")


def _fingerprint(backend, text: str, voice: str, model: str) -> str:
    """What makes a clip stale.

    The backend name is in the hash on purpose: switching from ElevenLabs to Piper must
    re-voice everything, and comparing only the text would leave a video half in each
    voice with nothing to indicate it.
    """
    parts = [getattr(backend, "name", type(backend).__name__), voice, model, text]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:20]


def narrate(spec: dict, spec_path: str | Path, backend, *, voice: str = "",
            model: str = "", force: bool = False, **options) -> NarrationResult:
    """Voice every scene whose line changed. Returns what happened."""
    spec_path = Path(spec_path).resolve()
    out_dir = spec_mod.vo_dir(spec_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / MANIFEST
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}

    result = NarrationResult(out_dir=out_dir)
    scenes = spec.get("scenes") or []
    for index, scene in enumerate(scenes, 1):
        key = f"{index:02d}"
        line = (scene.get("voice") or "").strip()
        if not line:
            result.skipped.append(index)
            continue
        target = out_dir / f"{key}.mp3"
        digest = _fingerprint(backend, line, voice, model)
        if not force and target.exists() and manifest.get(key) == digest:
            result.unchanged.append(index)
            continue
        try:
            backend.synthesize(line, target, voice=voice, model=model, **options)
        except Exception as exc:
            result.problems.append(f"scene {index}: {type(exc).__name__}: {exc}")
            continue
        if not target.exists() or target.stat().st_size == 0:
            result.problems.append(f"scene {index}: {backend.name} wrote no audio")
            continue
        manifest[key] = digest
        result.written.append(index)

    # Clips for scenes that no longer exist would otherwise be mixed into the video at
    # their old offsets.
    for stale in out_dir.glob("*.mp3"):
        number = stale.stem[:2]
        if number.isdigit() and int(number) > len(scenes):
            stale.unlink()
            manifest.pop(number, None)

    manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return result
