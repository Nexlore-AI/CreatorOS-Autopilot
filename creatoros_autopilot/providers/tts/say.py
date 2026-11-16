# SPDX-License-Identifier: AGPL-3.0-or-later
"""Narration through `say`, the speech command that ships with macOS.

The free narration path existed all along and almost nobody could reach it. `say` lived as
a branch inside `legacy/reel_engine.py`, reachable only by passing `--voice say` to the
retired renderer, and that branch also chose the voice, set the rate and decided what to
do about Hindi. Being buried in a renderer had two consequences at once: nothing else in
the project could use it, and off macOS there was no free backend at all, so a machine
with no ElevenLabs key rendered silent videos.

Behind the `TTS` protocol it is one backend among several. `core.narrate` owns the
per-scene caching and the manifest, so what is left here is only "speak this line into
this file", and the fact that was invisible from inside the renderer — that this runs on
exactly one operating system — is now a line `creatoros doctor` prints before a render
rather than a failure during one.

This is the provider that is allowed to be platform-specific. `available()` is what makes
that a fact the user is told rather than a surprise.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from ...core import media
from ...interfaces.availability import Availability
from ...interfaces.tts import Voice
from ...runtime import is_macos, os_name

BINARY = "say"
DEFAULT_RATE = 185          # words per minute, the pacing the existing specs were timed at
TIMEOUT = 120               # a narration line is one sentence, so this only catches a hang

# Several macOS voices accept Devanagari and simply decline to speak it. The same Hindi
# clause lengthens a clip by 0.60s under Samantha or Rishi and by 3.05s under Lekha, and
# `say` exits 0 either way, so a Hinglish script read by an English voice ships with words
# missing and nothing anywhere reports it. The default is therefore a Hindi voice. Locale
# alone is not a sufficient test — Rishi is en_IN and still drops the script — but every
# voice Apple ships under `hi_*` does speak it, which is what is trusted below.
PREFERRED = ("Lekha", "Tara")

DEVANAGARI = re.compile(r"[ऀ-ॿ]")

VOICE_SETTINGS = "System Settings > Accessibility > Spoken Content > System Voice > Manage Voices"

# "Albert   en_US   # Hello! My name is Albert." — except the column is not a column:
# "Aman (English (India)) en_IN" is separated by a single space, so a line is recognised by
# the locale-then-hash shape rather than by the gap in front of it.
VOICE_LINE = re.compile(
    r"^(?P<name>\S.*?)\s+(?P<lang>[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})?)\s+#\s*(?P<example>.*)$"
)


class SayError(RuntimeError):
    pass


def installed_voices() -> list[Voice]:
    """Every voice this Mac can speak with. Empty wherever `say` cannot be run.

    `'?'` is quoted when this is typed at a prompt only to keep the shell from globbing it;
    as an argv element it is an ordinary string.
    """
    found = shutil.which(BINARY)
    if not found:
        return []
    try:
        listed = subprocess.run([found, "-v", "?"], capture_output=True, text=True,
                                timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return []
    voices: dict[str, Voice] = {}
    for line in listed.stdout.splitlines():
        match = VOICE_LINE.match(line.rstrip())
        if not match:
            continue
        name = match["name"].strip()
        # macOS lists the Siri variants under a name another voice already has, and `-v`
        # can only address one of them, so the duplicate is noise rather than a choice.
        voices.setdefault(name, Voice(id=name, name=name, languages=(match["lang"],),
                                      detail=(match["example"] or "").strip()))
    return list(voices.values())


def speaks_devanagari(voice: Voice) -> bool:
    return any(language.lower().startswith("hi") for language in voice.languages)


def _rate(options: dict) -> int:
    try:
        rate = int(options.get("rate") or DEFAULT_RATE)
    except (TypeError, ValueError):
        raise SayError("tts rate is words per minute and has to be a whole number, "
                       f"not {options.get('rate')!r}") from None
    if rate <= 0:
        raise SayError(f"a rate of {rate} words per minute is not a speed")
    return rate


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _run(argv: list[str], what: str) -> None:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise SayError(f"{what} produced nothing in {TIMEOUT}s and was killed") from None
    except OSError as exc:
        raise SayError(f"{what} could not be run: {exc}") from None
    if done.returncode != 0:
        detail = " ".join((done.stderr or "").split())[:300] or f"exit {done.returncode}"
        raise SayError(f"{what} failed: {detail}")


class Say:
    """Free narration, on one operating system.

    `say` writes AIFF and nothing else, so every clip goes through ffmpeg on the way out:
    `core.narrate` asks for `NN.mp3` and the renderer mixes mp3.
    """

    name = BINARY
    paid = False

    def __init__(self) -> None:
        self._voices: list[Voice] | None = None

    def available(self) -> Availability:
        """Four different "no"s, and a "yes" that still has something to say."""
        if not is_macos():
            return Availability.no(
                f"`say` is part of macOS and this is {os_name()}",
                "set tts.backend: piper in creatoros.yaml — local, free and portable",
            )
        if not shutil.which(BINARY):
            return Availability.no(
                "this is macOS, but `say` is not on PATH",
                "`say` ships with the system: check that /usr/bin is still on your PATH",
            )
        if not media.find("ffmpeg"):
            return Availability.no(
                "`say` is there, but it writes AIFF and ffmpeg is not on PATH to make mp3 "
                "of it",
                media.INSTALL_HINT[os_name()],
            )
        voices = self.voices()
        if not voices:
            return Availability.no(
                "`say -v '?'` listed no voices, so this Mac has no speech data installed",
                f"add a voice in {VOICE_SETTINGS}",
            )
        hindi = [voice.id for voice in voices if speaks_devanagari(voice)]
        if not hindi:
            # Not a failure: an English-only script reads fine, and that is most of the
            # world's. Said here anyway, because the alternative is learning it from a
            # published video with half its narration missing.
            return Availability.yes(
                f"{len(voices)} voices, none of them Hindi — a Devanagari line will be "
                f"refused rather than dropped in silence. Add one in {VOICE_SETTINGS}"
            )
        return Availability.yes(f"{len(voices)} voices, Devanagari via {hindi[0]}")

    def synthesize(self, text: str, out_path: Path, *, voice: str = "",
                   model: str = "", **options) -> Path:
        """Speak `text` into `out_path` as mp3.

        `model` is accepted and ignored: with `say` the voice *is* the model. `rate` is
        read from `options`, in words per minute.
        """
        status = self.available()
        if not status:
            raise SayError(f"{status.detail}. {status.fix}" if status.fix else status.detail)
        line = text.strip()
        if not line:
            raise SayError("nothing to speak")
        chosen = self._voice_for(voice, line)
        rate = _rate(options)

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Intermediates hang off the output, never off the input: a spec can sit on a
        # read-only mount, and in a container it usually does. Neither name ends in `.mp3`,
        # so a crashed run cannot leave something the narrator mistakes for a finished clip.
        aiff = Path(f"{out_path}.say.aiff")
        part = Path(f"{out_path}.part")

        # The data format is pinned so the intermediate is plain PCM at the rate the voices
        # synthesise at, rather than whatever the default of the day is. It has to be
        # big-endian: AIFF has no little-endian form, and asking for `LEI16` with a `.aiff`
        # output fails with "Opening output file failed: fmt?".
        argv = [BINARY, "-r", str(rate), "-o", str(aiff), "--data-format=BEI16@22050"]
        if chosen:
            argv += ["-v", chosen]
        # The line is an argv element and never goes near a shell. Narration is written by
        # a model, so it is full of quotes, dashes and the occasional backtick, and every
        # one of those has to reach the synthesiser as itself.
        argv.append(line)

        try:
            _run(argv, BINARY)
            if not aiff.is_file() or aiff.stat().st_size == 0:
                raise SayError(f"`{BINARY}` exited cleanly but wrote no audio with voice "
                               f"{chosen or '(the system default)'}")
            # `-f mp3` is explicit because the extension ffmpeg would otherwise infer the
            # format from is `.part` at this point.
            _run([media.require("ffmpeg"), "-nostdin", "-loglevel", "error", "-y",
                  "-i", str(aiff), "-f", "mp3", "-b:a", "128k", "-ar", "44100", "-ac", "1",
                  str(part)], "ffmpeg")
            # The rename is the last step, so a clip only exists once it is whole. A
            # half-written one would be worse than none: the manifest would accept it and
            # the video would ship with a truncated line.
            os.replace(part, out_path)
        finally:
            _discard(aiff)
            _discard(part)
        return out_path

    def voices(self) -> list[Voice]:
        """The installed voices, read once.

        `core.narrate` comes through here once per scene, and no voice is installed or
        removed in the middle of a render.
        """
        if self._voices is None:
            self._voices = installed_voices()
        return list(self._voices)

    def _voice_for(self, requested: str, text: str) -> str:
        """What to pass to `-v`, or "" to leave `say` on the system default voice."""
        voices = self.voices()
        by_name = {voice.id.casefold(): voice for voice in voices}
        hindi = [voice.id for voice in voices if speaks_devanagari(voice)]

        if requested:
            voice = by_name.get(requested.casefold())
            if voice is None:
                raise SayError(f"no voice named {requested!r} is installed on this Mac. "
                               f"`say -v '?'` lists the names it accepts")
            if DEVANAGARI.search(text) and not speaks_devanagari(voice):
                # Refused rather than quietly swapped: the configured voice is a decision,
                # and overriding it without a word is how the renderer used to behave.
                raise SayError(
                    f"{voice.id} ({voice.languages[0] if voice.languages else 'unknown'}) "
                    "would skip the Hindi in this line and report nothing. Set "
                    + (f"tts.voice_id to one of: {', '.join(hindi)}" if hindi
                       else f"tts.voice_id to a Hindi voice, installed from {VOICE_SETTINGS}")
                )
            return voice.id

        for candidate in PREFERRED:
            if candidate.casefold() in by_name:
                return by_name[candidate.casefold()].id
        if hindi:
            return hindi[0]
        if DEVANAGARI.search(text):
            raise SayError(
                "this line is Hinglish and no Hindi voice is installed, so `say` would read "
                f"the English and drop the rest. Add one in {VOICE_SETTINGS}, or use a "
                "backend that speaks Hindi (piper, elevenlabs)"
            )
        return ""
