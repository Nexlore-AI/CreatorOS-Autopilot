#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Narration and music through ElevenLabs.

One clip per scene into `<spec>_vo/NN.mp3`, plus a music bed. A clip is only regenerated
when its line changed — the hashes live in `<spec>_vo/manifest.json` — which is what keeps
a re-render from re-billing the whole video.

This is still the only TTS backend that the daily pipeline uses. Phase 2 puts it behind a
`TTS` interface alongside Piper, which is what makes the free local path real; until then
`tts.backend: none` is the keyless default and renders scenes at their minimum duration.

Keys come from the environment (`ELEVENLABS_API_KEY`), never from `creatoros.yaml`.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from ...core import spec as spec_mod

API = "https://api.elevenlabs.io/v1"
DEFAULT_MODEL = "eleven_multilingual_v2"
SETTINGS = {"stability": 0.45, "similarity_boost": 0.8, "style": 0.25, "use_speaker_boost": True}


class TTSError(RuntimeError):
    pass


def _post(url: str, body: dict, out: str, key: str) -> str | None:
    """POST expecting audio back. Returns an error string, or None on success."""
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp, open(out + ".part", "wb") as fh:
            fh.write(resp.read())
        os.replace(out + ".part", out)
        return None
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code} {exc.read()[:300].decode(errors='replace')}"
    except Exception as exc:
        return str(exc)
    finally:
        # A half-written .part would otherwise be mistaken for nothing at all next run.
        if os.path.exists(out + ".part"):
            os.remove(out + ".part")


def narrate(spec_path: str | os.PathLike, *, voice_id: str = "", model: str = "",
            api_key: str = "") -> tuple[dict, list[str]]:
    """Voice every scene that changed. Returns (spec, problems)."""
    spec_path = str(Path(spec_path).resolve())
    spec = spec_mod.load(spec_path)
    key = (api_key or os.environ.get("ELEVENLABS_API_KEY", "")).strip()
    if not key:
        raise TTSError(
            "ELEVENLABS_API_KEY is not set. Put it in .env beside creatoros.yaml, or set "
            "tts.backend: none to render without narration."
        )
    voice = (voice_id or os.environ.get("CREATOROS_TTS_VOICE_ID", "")).strip()
    if not voice:
        raise TTSError("no voice id: set tts.voice_id in creatoros.yaml")
    model = model or DEFAULT_MODEL

    out_dir = spec_mod.vo_dir(spec_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}

    problems = []
    scenes = spec.get("scenes") or []
    for i, scene in enumerate(scenes, 1):
        line = (scene.get("voice") or "").strip()
        if not line:
            problems.append(f"scene {i}: no voice line")
            continue
        target = out_dir / f"{i:02d}.mp3"
        digest = hashlib.sha1((voice + model + line).encode()).hexdigest()[:16]
        if target.exists() and manifest.get(f"{i:02d}") == digest:
            print(f"voice {i:02d} unchanged")
            continue
        err = _post(f"{API}/text-to-speech/{voice}?output_format=mp3_44100_128",
                    {"text": line, "model_id": model, "voice_settings": SETTINGS},
                    str(target), key)
        if err:
            problems.append(f"scene {i}: {err}")
            continue
        manifest[f"{i:02d}"] = digest
        print(f"voice {i:02d} ok")

    for stale in out_dir.glob("*.mp3"):            # scenes that were removed
        num = stale.name[:2]
        if num.isdigit() and int(num) > len(scenes):
            stale.unlink()
            manifest.pop(num, None)
    manifest_path.write_text(json.dumps(manifest, indent=1))
    return spec, problems


def pick_music(spec: dict, spec_path: str | os.PathLike, *, music_dir: str | os.PathLike,
               mode: str = "library", api_key: str = "") -> dict:
    """Choose or compose a music bed, and write the choice back into the spec.

    "library" rotates the least recently used track in the workspace music folder, so a
    week of videos does not open with the same eight bars. An empty folder is not an
    error: no bed is a fine video.
    """
    if spec.get("music") or mode == "none":
        return spec
    music_dir = Path(music_dir)
    music_dir.mkdir(parents=True, exist_ok=True)
    used_path = music_dir / "music_used.json"

    if mode == "generate" and spec.get("music_prompt"):
        key = (api_key or os.environ.get("ELEVENLABS_API_KEY", "")).strip()
        name = f"gen_{spec.get('id', 'short')}.mp3"
        err = _post(f"{API}/music?output_format=mp3_44100_128",
                    {"prompt": spec["music_prompt"] + " Instrumental, leaves room for a voiceover.",
                     "music_length_ms": 75000, "force_instrumental": True},
                    str(music_dir / name), key) if key else "no ELEVENLABS_API_KEY"
        if err:
            print("!! music generation failed, using the library:", err)
        else:
            spec["music"] = name

    if not spec.get("music"):
        tracks = sorted(Path(p).name for p in glob.glob(str(music_dir / "*.mp3")))
        if tracks:
            used = json.loads(used_path.read_text()) if used_path.is_file() else {}
            spec["music"] = min(tracks, key=lambda t: used.get(t, ""))
            used[spec["music"]] = spec.get("id", "")
            used_path.write_text(json.dumps(used, indent=1))
        else:
            print(f"no tracks in {music_dir} — rendering without a music bed")
    spec_mod.save(spec, spec_path)
    print("music", spec.get("music") or "(none)")
    return spec


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print("usage: python -m creatoros_autopilot.providers.tts.elevenlabs SPEC.json", file=sys.stderr)
        return 2
    spec_path = argv[0]
    spec, problems = narrate(spec_path)
    pick_music(spec, spec_path,
               music_dir=os.environ.get("CREATOROS_MUSIC_DIR", "."),
               mode=os.environ.get("CREATOROS_MUSIC_MODE", "library"))
    if problems:
        print("voice problems:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
