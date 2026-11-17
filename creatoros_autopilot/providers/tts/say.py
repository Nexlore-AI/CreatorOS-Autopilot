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

The hard part is not running the binary, it is finding out what a voice can read. `say`
has no way to be asked, it exits 0 on a script its voice cannot pronounce, and it writes a
valid file of 0.0116 s when it declines. Locale does not answer the question either: Rishi
is `en_IN` and drops Devanagari, Tara is `en_IN` and reads it. So capability here is
*measured* — the line is spoken into a temporary file and the duration decides — and a
nomination from a locale or from a voice's own example sentence only sets the order the
measurements run in. An earlier version of this module assumed `hi_*` was necessary and
sufficient, which refused the voices that work and forced a Hindi voice onto Japanese.

This is the provider that is allowed to be platform-specific. `available()` is what makes
that a fact the user is told rather than a surprise.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path

from ...core import media
from ...interfaces.availability import Availability
from ...interfaces.tts import Voice
from ...runtime import is_macos, os_name

BINARY = "say"
DEFAULT_RATE = 185          # words per minute, the pacing the existing specs were timed at
TIMEOUT = 120               # a narration line is one sentence, so this only catches a hang

# Tried first when a line needs a voice that can read its script. Hinglish is what this
# project narrates and Lekha is the voice its specs were timed against; Tara is the en_IN
# Siri voice that reads Devanagari on a Mac where Lekha, an optional download, is absent.
# A name here changes the order the probe runs in and nothing else — neither voice is
# trusted without being measured, because the previous version of this list *was* the test
# and it was wrong in both directions.
PREFERRED = ("Lekha", "Tara")

# Nominations that cannot be read off `say -v '?'`. Measured at 185 wpm on one Devanagari
# clause: Tara 3.03 s and Aman 3.19 s against Lekha's 3.16 s, while Rishi — the same en_IN
# locale — writes 0.0116 s and exits 0. One locale, both answers, so a locale may only put
# a voice in the queue.
NOMINATED_LOCALES = {"DEVANAGARI": ("en_in",)}

# The measurement. A voice that declines a script writes the same 0.0116 s sliver whatever
# the length of the line; the shortest real word measured is 0.197 s (two Devanagari
# letters, Lekha, 300 wpm). The threshold sits between the two and grows with the text, so
# a long line cannot pass on a sliver. The rate is high because the probe only has to tell
# speech from silence, and the text is clipped because a probe is a tax on every render.
PROBE_RATE = 300
PROBE_CHARS = 120
PROBE_FLOOR = 0.1
PROBE_SECONDS_PER_CHAR = 0.004
# Voices probed before giving up. Bounded so a line in a script nobody here has speech
# data for refuses in a couple of seconds instead of synthesising 180 times to say no.
PROBE_LIMIT = 8

# Apple's embedded speech commands. `say` executes them rather than speaking them, and
# measured with Samantha at 185 wpm: `[[slnc 3000]] pause then speak` runs 3.773 s against
# 0.976 s for the same words, three seconds of dead air that no caption covers, and
# `Revenue grew [[emph +]] fast.` is byte-identical to the line without the marker — the
# marker was eaten. Both exit 0. Narration is model-written, the delimiters cannot be
# turned off (`[[dlim]]` is itself an embedded command and made the clip longer still), and
# a line that loses words silently is the exact failure this module exists to stop.
EMBEDDED_COMMAND = "[["

# Latin is the script `say` reads with any voice at all, so it is the one that never needs
# a voice chosen for it. Everything else is measured before it is spoken.
ALWAYS_READABLE = "LATIN"

VOICE_SETTINGS = "System Settings > Accessibility > Spoken Content > System Voice > Manage Voices"

# "Albert   en_US   # Hello! My name is Albert." — except the column is not a column:
# "Aman (English (India)) en_IN" is separated by a single space, so a line is recognised by
# the locale-then-hash shape rather than by the gap in front of it.
VOICE_LINE = re.compile(
    r"^(?P<name>\S.*?)\s+(?P<lang>[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})?)\s+#\s*(?P<example>.*)$"
)

#: Script names Unicode spells in a way that is no use in an error message.
SCRIPT_NAMES = {"CJK": "Han"}


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


def scripts_in(text: str) -> tuple[str, ...]:
    """The writing systems a line uses, Latin and punctuation aside, first appearance first.

    Unicode names every letter after its script — DEVANAGARI LETTER KA, CYRILLIC SMALL
    LETTER A, CJK UNIFIED IDEOGRAPH-4ECA — so this needs no table of code-point ranges.
    The one Devanagari range that used to live here was this module's entire idea of "a
    script a voice might not read", which is why a Japanese line was handed to a Hindi
    voice and came back as 0.0116 s of nothing.

    Combining marks are skipped rather than classified: a Devanagari vowel sign is not
    `isalpha()`, and every script that has them also has letters.
    """
    found: dict[str, None] = {}
    for char in text:
        if not char.isalpha():
            continue
        try:
            name = unicodedata.name(char)
        except ValueError:                       # unnamed, so unclassifiable here
            continue
        script = name.split()[0].split("-")[0]
        if script != ALWAYS_READABLE:
            found[script] = None
    return tuple(found)


def script_names(scripts: tuple[str, ...]) -> str:
    """Scripts as a human would name them, for an error that has to be acted on."""
    return ", ".join(SCRIPT_NAMES.get(s, s.title()) for s in scripts) or "this text"


def _locales(voice: Voice) -> tuple[str, ...]:
    """A voice's locale and its bare language, lowercased: ("en_in", "en")."""
    forms: dict[str, None] = {}
    for language in voice.languages:
        tag = language.strip().lower().replace("-", "_")
        if tag:
            forms[tag] = None
            forms[tag.split("_")[0]] = None
    return tuple(forms)


def _demonstrated(voices: list[Voice]) -> dict[str, set[str]]:
    """Which scripts each locale is shown to read, by its voices' own example sentences.

    Every voice introduces itself in its own language, so Lekha's "..." proves hi reads
    Devanagari and Kyoko's proves ja reads kana and Han. This is the only capability
    evidence `say -v '?'` carries, and it is incomplete rather than wrong: Geeta (te_IN)
    and Piya (bn_IN) say hello in English, so their scripts are proven by nobody.
    """
    proven: dict[str, set[str]] = {}
    for voice in voices:
        shown = set(scripts_in(voice.detail))
        if not shown:
            continue
        for locale in _locales(voice):
            proven.setdefault(locale, set()).update(shown)
    return proven


def matching_voices(name: str, voices: list[Voice]) -> list[Voice]:
    """Voices a configured name could mean.

    macOS lists the Siri voices as `Tara (English (India))`, so the bare name a user types
    in `creatoros.yaml` — and the bare name `PREFERRED` holds — matches no voice id at all
    unless the parenthesised locale is allowed to be left off. More than one match is
    returned rather than resolved: `Eddy` is fourteen voices, and picking one of them is
    not this function's call to make.
    """
    want = name.strip().casefold()
    if not want:
        return []
    exact = [voice for voice in voices if voice.id.casefold() == want]
    if exact:
        return exact
    return [voice for voice in voices if voice.id.casefold().startswith(f"{want} (")]


def nominees(scripts: tuple[str, ...], voices: list[Voice]) -> list[str]:
    """Voice ids worth measuring for these scripts, best first.

    A nomination is a guess narrowed down, never a capability: the voices whose own
    example sentence is written in the script come first, then the voices of a locale
    another voice proved reads it, then the locales measurement has shown read a script
    their listing never mentions. The probe decides, and this only keeps it from having to
    try every voice on the machine.
    """
    if not scripts:
        return []
    wanted = set(scripts)
    proven = _demonstrated(voices)
    extra = {locale for script in scripts for locale in NOMINATED_LOCALES.get(script, ())}
    preference = {voice.id: rank for rank, name in enumerate(PREFERRED)
                  for voice in matching_voices(name, voices)}

    def tier(voice: Voice) -> int:
        shown = set(scripts_in(voice.detail))
        if wanted <= shown:
            return 0
        if wanted & shown:
            return 1
        locales = _locales(voice)
        shown = set().union(*(proven.get(locale, set()) for locale in locales)) if locales else set()
        if wanted <= shown:
            return 2
        if wanted & shown:
            return 3
        if any(locale in extra for locale in locales):
            return 4
        return 99

    ranked = [(tier(voice), preference.get(voice.id, len(PREFERRED)), order, voice.id)
              for order, voice in enumerate(voices)]
    return [voice_id for rank, _, _, voice_id in sorted(ranked) if rank < 99]


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


def _speech_argv(rate: int, out: Path, voice: str, line: str) -> list[str]:
    """One `say` command line, built the same way for a probe and for a real clip.

    The data format is pinned so the intermediate is plain PCM at the rate the voices
    synthesise at, rather than whatever the default of the day is. It has to be
    big-endian: AIFF has no little-endian form, and asking for `LEI16` with a `.aiff`
    output fails with "Opening output file failed: fmt?".

    `--` is the load-bearing part. The line is an argv element and never goes near a
    shell, but `say` still parses it for options: without the terminator, "-20 percent is
    what it actually costs." died with `say: invalid option -- 2` and took the render with
    it, naming neither the scene nor the reason.
    """
    argv = [BINARY, "-r", str(rate), "-o", str(out), "--data-format=BEI16@22050"]
    if voice:
        argv += ["-v", voice]
    return argv + ["--", line]


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
        # (voice, scripts) -> (speaks it, what was measured). A render comes through here
        # once per scene and a probe costs half a second, so the answer is kept.
        self._probed: dict[tuple[str, tuple[str, ...]], tuple[bool, str]] = {}

    def available(self) -> Availability:
        """Four different "no"s, and a "yes" that still has something to say.

        No probing happens here. `synthesize` calls this on every line and `doctor` calls
        it on a cold machine, so it reports what the listing can be read for free; the
        measurement belongs where a real script is in hand.
        """
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
        # Devanagari is singled out because it is the script this project's own narration
        # mixes into English, and the one where a wrong voice is silent rather than loud.
        hindi = nominees(("DEVANAGARI",), voices)
        if not hindi:
            # Not a failure: an English-only script reads fine, and that is most of the
            # world's. Said here anyway, because the alternative is learning it from a
            # published video with half its narration missing.
            return Availability.yes(
                f"{len(voices)} voices, none whose language reads Devanagari — such a line "
                f"is measured before it is spoken and refused if nothing can read it, "
                f"never dropped in silence. Add a voice in {VOICE_SETTINGS}"
            )
        return Availability.yes(f"{len(voices)} voices, Devanagari measured against "
                                f"{', '.join(hindi[:3])}")

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
        self._refuse_embedded_commands(line)
        chosen = self._voice_for(voice, line)
        rate = _rate(options)

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Intermediates hang off the output, never off the input: a spec can sit on a
        # read-only mount, and in a container it usually does. Neither name ends in `.mp3`,
        # so a crashed run cannot leave something the narrator mistakes for a finished clip.
        aiff = Path(f"{out_path}.say.aiff")
        part = Path(f"{out_path}.part")

        try:
            # Narration is written by a model, so it is full of quotes, dashes and the
            # occasional backtick, and every one of those has to reach the synthesiser as
            # itself — see `_speech_argv` for the two ways that went wrong.
            _run(_speech_argv(rate, aiff, chosen, line), BINARY)
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

    def _refuse_embedded_commands(self, line: str) -> None:
        """Refuse a line `say` would execute instead of speaking. See `EMBEDDED_COMMAND`."""
        if EMBEDDED_COMMAND not in line:
            return
        marker = re.search(r"\[\[[^\]]{0,30}\]?\]?", line)
        raise SayError(
            f"this line contains {(marker.group(0) if marker else EMBEDDED_COMMAND)!r}, "
            "which `say` executes as an embedded speech command instead of speaking it: "
            "`[[slnc 3000]]` injects three seconds of dead air and `[[emph +]]` disappears, "
            "both with exit 0 and nothing in the clip to show for it. Remove the double "
            "brackets from the narration."
        )

    def _measure(self, voice: str, text: str, scripts: tuple[str, ...]) -> tuple[bool, str]:
        """Speak a sample and see whether anything came out. Cached per voice and script."""
        key = (voice, scripts)
        if key not in self._probed:
            self._probed[key] = self._probe(voice, text[:PROBE_CHARS])
        return self._probed[key]

    def _probe(self, voice: str, sample: str) -> tuple[bool, str]:
        """One measurement: `(speaks it, what was measured)`.

        A failed probe is an answer, not an error — a voice that cannot read the script is
        exactly what this is looking for — so the reason is returned to be quoted in the
        refusal rather than raised from here.
        """
        with tempfile.TemporaryDirectory(prefix="creatoros-say-") as tmp:
            aiff = Path(tmp) / "probe.aiff"
            try:
                done = subprocess.run(_speech_argv(PROBE_RATE, aiff, voice, sample),
                                      capture_output=True, text=True, timeout=TIMEOUT)
            except (OSError, subprocess.SubprocessError) as exc:
                return False, f"could not be run ({exc})"
            if done.returncode != 0:
                return False, (" ".join((done.stderr or "").split())[:120]
                               or f"exit {done.returncode}")
            if not aiff.is_file() or aiff.stat().st_size == 0:
                return False, "wrote no audio"
            try:
                spoken = media.duration(str(aiff))
            except Exception:
                return False, "wrote audio ffprobe cannot read"
        needed = max(PROBE_FLOOR, PROBE_SECONDS_PER_CHAR * len(sample))
        if spoken < needed:
            return False, f"{spoken:.4f}s of audio for {len(sample)} characters"
        return True, f"{spoken:.2f}s"

    def _voice_for(self, requested: str, text: str) -> str:
        """What to pass to `-v`, or "" to leave `say` on the system default voice."""
        voices = self.voices()
        scripts = scripts_in(text)

        if requested:
            matches = matching_voices(requested, voices)
            if not matches:
                raise SayError(f"no voice named {requested!r} is installed on this Mac. "
                               f"`say -v '?'` lists the names it accepts")
            if len(matches) > 1:
                raise SayError(
                    f"{requested!r} names {len(matches)} voices on this Mac "
                    f"({', '.join(voice.id for voice in matches[:4])}...); set tts.voice_id "
                    "to one of them in full"
                )
            voice = matches[0]
            if scripts:
                speaks, measured = self._measure(voice.id, text, scripts)
                if not speaks:
                    # Refused rather than quietly swapped: the configured voice is a
                    # decision, and overriding it without a word is how the renderer used
                    # to behave. Measured, not assumed from the locale — that assumption
                    # refused Tara, which reads Devanagari perfectly well.
                    options = [name for name in nominees(scripts, voices)
                               if name != voice.id][:4]
                    raise SayError(
                        f"{voice.id} ({voice.languages[0] if voice.languages else 'unknown'}) "
                        f"does not read {script_names(scripts)}: {measured}. `say` exits 0 "
                        "and writes near-silence, so the line would ship with those words "
                        "missing. "
                        + (f"Voices worth trying: {', '.join(options)}" if options
                           else f"Install a voice that reads it, in {VOICE_SETTINGS}")
                    )
            return voice.id

        if not scripts:
            # Nothing but Latin, so there is nothing to choose: `say` reads it with
            # whatever voice the user picked in System Settings, and that is their
            # decision. This used to return a Hindi voice for every line on the machine,
            # including English ones, which is the same silent override the explicit path
            # above refuses to perform.
            return ""

        # A script that needs a voice. The system voice is measured first for the same
        # reason: if it can read the line, there is no call to override it.
        tried: list[str] = []
        for candidate in [""] + [name for name in nominees(scripts, voices) if name]:
            speaks, measured = self._measure(candidate, text, scripts)
            if speaks:
                return candidate
            tried.append(f"{candidate or '(system voice)'} {measured}")
            if len(tried) >= PROBE_LIMIT:
                break
        raise SayError(
            f"no voice on this Mac reads {script_names(scripts)}, so `say` would drop those "
            f"words and exit 0. Measured: {'; '.join(tried)}. Add a voice in "
            f"{VOICE_SETTINGS}, name one with tts.voice_id, or use a backend that does not "
            "depend on the system's speech data (piper, elevenlabs)"
        )
