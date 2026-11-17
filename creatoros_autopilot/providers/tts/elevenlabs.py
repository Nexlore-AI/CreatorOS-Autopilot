# SPDX-License-Identifier: AGPL-3.0-or-later
"""ElevenLabs narration.

This backend is now only what a TTS backend should be: one line in, one audio file out.
Everything it used to also own — walking the scenes, hashing each line so an unchanged one
is not re-billed, deleting clips for scenes that were removed, choosing a music bed — moved
to `core.narrate` and `core.music`, where every backend gets it. That caching is the reason
a re-render does not cost money, and it should never have belonged to one vendor.

It is a paid backend, so `paid = True`: `creatoros doctor` warns, and the test suite refuses
to call it. Free local narration is `piper`.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from ...interfaces import Availability, Voice

API = "https://api.elevenlabs.io/v1"
DEFAULT_MODEL = "eleven_multilingual_v2"
# Tuned for explainer narration: enough stability to stay level across a 60-second script,
# enough style to not sound flat.
VOICE_SETTINGS = {"stability": 0.45, "similarity_boost": 0.8, "style": 0.25,
                  "use_speaker_boost": True}


class ElevenLabsError(RuntimeError):
    pass


class ElevenLabs:
    name = "elevenlabs"
    paid = True

    def __init__(self, api_key: str | None = None, key_env: str = "ELEVENLABS_API_KEY",
                 model: str = DEFAULT_MODEL):
        self._key = (api_key or "").strip()
        self.key_env = key_env
        self.model = model

    # ------------------------------------------------------------------ auth
    @property
    def key(self) -> str:
        key = self._key or os.environ.get(self.key_env, "").strip()
        if not key:
            raise ElevenLabsError(f"{self.key_env} is not set")
        return key

    def _has_key(self) -> bool:
        return bool(self._key or os.environ.get(self.key_env, "").strip())

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(f"{API}{path}", headers={"xi-api-key": self.key})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())

    # -------------------------------------------------------------- protocol
    def available(self) -> Availability:
        if not self._has_key():
            return Availability.no(
                f"{self.key_env} is not set",
                f"add {self.key_env} to .env, or set tts.backend: piper for a free local voice",
            )
        try:
            user = self._get("/user/subscription")
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return Availability.no("the key was rejected", "check the key in .env")
            return Availability.no(f"HTTP {exc.code}", "check https://status.elevenlabs.io")
        except Exception as exc:
            return Availability.no(f"unreachable ({type(exc).__name__})", "check your network")
        used = user.get("character_count")
        cap = user.get("character_limit")
        if isinstance(used, int) and isinstance(cap, int):
            left = cap - used
            # Worth surfacing: a 4-video day is roughly 2,400 characters, so a Starter plan's
            # monthly allowance is consumed well before a month of daily runs.
            if left <= 0:
                return Availability.no(
                    f"character quota exhausted ({used}/{cap})",
                    "wait for the reset, upgrade, or set tts.backend: piper",
                )
            return Availability.yes(f"{left:,} of {cap:,} characters left this period")
        return Availability.yes("key accepted")

    def synthesize(self, text: str, out_path: Path, *, voice: str = "", model: str = "",
                   **options) -> Path:
        if not voice:
            raise ElevenLabsError("no voice id: set tts.voice_id in creatoros.yaml")
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        body = {"text": text, "model_id": model or self.model,
                "voice_settings": {**VOICE_SETTINGS, **(options.get("voice_settings") or {})}}
        # mp3_44100_128 rather than 192 kbps: the higher bitrate needs a Creator plan, and
        # the difference is inaudible under a music bed.
        url = f"{API}/text-to-speech/{voice}?output_format=mp3_44100_128"
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), method="POST",
            headers={"xi-api-key": self.key, "Content-Type": "application/json",
                     "Accept": "audio/mpeg"})
        part = out_path.with_suffix(out_path.suffix + ".part")
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                part.write_bytes(resp.read())
            # Atomic: core.narrate treats any existing non-empty file as done, so a
            # half-written clip would ship inside the video with no error anywhere.
            os.replace(part, out_path)
        except urllib.error.HTTPError as exc:
            raise ElevenLabsError(
                f"HTTP {exc.code} {exc.read()[:300].decode(errors='replace')}") from None
        finally:
            if part.exists():
                part.unlink()
        return out_path

    def voices(self) -> list[Voice]:
        if not self._has_key():
            return []
        try:
            payload = self._get("/voices")
        except Exception:
            return []
        out = []
        for entry in payload.get("voices", []):
            labels = entry.get("labels") or {}
            out.append(Voice(
                id=entry.get("voice_id", ""),
                name=entry.get("name", ""),
                languages=tuple(filter(None, [labels.get("language", "")])),
                gender=labels.get("gender", ""),
                detail=entry.get("category", ""),
            ))
        return out

    # ------------------------------------------------------- beyond the protocol
    def compose(self, prompt: str, out_path: Path, *, length_ms: int = 75000) -> Path:
        """Generate an instrumental bed. Not part of the TTS protocol.

        `core.music` calls this only when music.mode is "generate", and falls back to the
        library when it fails or is absent — so a backend without it is not a problem.
        """
        out_path = Path(out_path)
        body = {"prompt": prompt + " Instrumental, leaves room for a voiceover.",
                "music_length_ms": length_ms, "force_instrumental": True}
        req = urllib.request.Request(
            f"{API}/music?output_format=mp3_44100_128", data=json.dumps(body).encode(),
            method="POST", headers={"xi-api-key": self.key,
                                    "Content-Type": "application/json",
                                    "Accept": "audio/mpeg"})
        part = out_path.with_suffix(".part")
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                part.write_bytes(resp.read())
            os.replace(part, out_path)
        finally:
            if part.exists():
                part.unlink()
        return out_path
