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

from . import media
from . import spec as spec_mod

MANIFEST = "manifest.json"

#: Narration slower than this is not speech. Used to catch a backend that exits cleanly
#: having spoken nothing — the clip is not empty, so a size check passes it.
MIN_SECONDS_PER_CHAR = 0.012


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


def _implausible(target: Path, line: str, backend) -> str:
    """Whether a written clip is too short to be the line it claims to be.

    A size check is not enough, and the gap is not theoretical. A TTS backend handed text
    its voice cannot pronounce — Devanagari to an English-only macOS voice, an unsupported
    script to Piper's phonemiser — exits 0 and writes a valid but near-silent file.
    `narrate` then records the hash, the renderer falls back to the scene's minimum
    duration, and the video ships with that line missing. Every later run reports
    "unchanged", so it is never retried.

    Measured on macOS: a Hindi clause that takes 3.05 s in a voice that speaks it takes
    0.0116 s in one that does not.
    """
    if not target.exists() or target.stat().st_size == 0:
        return f"{backend.name} wrote no audio"
    expected = max(0.25, len(line) * MIN_SECONDS_PER_CHAR)
    try:
        actual = media.duration(str(target))
    except Exception:
        # ffprobe could not read it at all, which is its own kind of broken: a frameless
        # file here makes every later render fail with nothing pointing at the cause.
        return f"{backend.name} wrote a file ffprobe cannot read"
    if actual < expected * 0.5:
        return (f"{backend.name} produced {actual:.2f}s for {len(line)} characters "
                f"(expected at least {expected * 0.5:.2f}s) — the voice probably cannot "
                f"pronounce this script")
    return ""


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
        problem = _implausible(target, line, backend)
        if problem:
            # Leave no half-good clip behind: the manifest is not updated, but an
            # existing file would be accepted by the next run's size check.
            target.unlink(missing_ok=True)
            result.problems.append(f"scene {index}: {problem}")
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
