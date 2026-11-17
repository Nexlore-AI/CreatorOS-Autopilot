# SPDX-License-Identifier: AGPL-3.0-or-later
"""Narration through Piper, which costs nothing and needs no account.

This is the backend that makes trying CreatorOS free. Piper is a local neural TTS: one
binary, one voice model of about 60 MB, no key, no quota and no network. ElevenLabs reads
Hinglish better, but it bills per character and it puts a signup in front of the first
render, so the keyless path through the pipeline runs through here — and so does any test
that needs real audio, since no test may call a backend that bills.

Two details of Piper's command line shape this module. It treats each **line** of stdin as
a separate utterance while `--output_file` names a single WAV, so a narration line that
contains a newline is spoken twice and the file keeps whichever half finished last — the
text is flattened to one line before it is piped. And Piper emits WAV where `core.narrate`
and the renderer expect `NN.mp3`, so ffmpeg converts; that is why a working Piper with no
ffmpeg on `PATH` is still reported unavailable rather than failing three scenes into a run.

Voice models are not bundled and cannot be: they are separate downloads from
https://huggingface.co/rhasspy/piper-voices, and each one is *two* files, `<name>.onnx`
and `<name>.onnx.json`. Piper's own error when the json is missing names neither the file
nor where to get it, so that pairing is checked here instead.

Where those two files live is not a detail either: it decides whether the *scheduled*
run can speak, which is the only run most installs ever make. The search path is absolute
and comes from `runtime` — never a relative directory. `search_path` says why at length.
Nothing here writes into a checkout, which is as well: `.gitignore` covers `*.mp3` and
`*.wav` but not `*.onnx`, so a `PIPER_VOICE_DIR` pointed inside a clone would offer git
60 MB of model.

If `piper` was installed from a release archive rather than with pip, it also needs its
`espeak-ng-data` directory beside the binary. Nothing here can see that from the outside,
so the check is Piper's: its stderr is quoted back in full when synthesis fails.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from ...core import media
from ...interfaces.availability import Availability
from ...interfaces.tts import Voice
from ...runtime import default_workspace, os_name, user_config_dir

VOICES_URL = "https://huggingface.co/rhasspy/piper-voices"
RELEASES_URL = "https://github.com/rhasspy/piper/releases"
DEFAULT_VOICE_DIR = "piper-voices"
EXAMPLE = "en_US-lessac-medium"
EXAMPLE_URL = f"{VOICES_URL}/resolve/main/en/en_US/lessac/medium/{EXAMPLE}.onnx"

# `apt install piper` is deliberately not suggested on Linux: that package is a
# gaming-mouse configurator. pip is the one install that is the same on all three.
INSTALL_HINT = {
    "macos": f"pip install piper-tts   (or unpack a build from {RELEASES_URL} and link "
             "piper into /usr/local/bin)",
    "linux": f"pip install piper-tts   (or unpack a build from {RELEASES_URL} into "
             "~/.local/bin)",
    "windows": f"pip install piper-tts   (or unpack piper_windows_amd64.zip from "
               f"{RELEASES_URL} and add that folder to PATH)",
}

TIMEOUT = 300


class PiperError(RuntimeError):
    pass


def binary() -> str | None:
    """The piper executable, honouring a `PIPER_BIN` override.

    `which` is used even for the override, because it answers the question that matters —
    is this thing executable — for both a bare command name and an absolute path.
    """
    override = os.environ.get("PIPER_BIN", "").strip()
    return shutil.which(override) if override else shutil.which("piper")


def search_path() -> list[Path]:
    """Every directory a voice model is looked for in, read on every call.

    The default used to be `Path.cwd() / "piper-voices"`, which made "is a voice
    installed?" a question about where the user was standing. It answered yes from the
    project directory and no one level up, and `runtime/units.py` emits no working
    directory for the unit it installs, so the daily run looked in `/piper-voices` under
    launchd and `~/piper-voices` under a systemd user timer. The scheduled run is this
    product's primary mode: the default was wrong in exactly the case nobody watches, and
    right whenever anyone checked by hand.

    `user_config_dir()` comes first because it is the one location that does not move. The
    workspace does: it is set in `creatoros.yaml` or by `CREATOROS_WORKSPACE`, which
    `schedule install` bakes into the unit, so voices kept under the workspace would be
    found interactively and missing at 06:30 — the same bug wearing a different hat. The
    workspace is still searched, for anyone who would rather keep 60 MB of model beside
    the renders, and `PIPER_VOICE_DIR` still overrides both outright.
    """
    env = os.environ.get("PIPER_VOICE_DIR", "").strip()
    if env:
        return [Path(env).expanduser()]
    # Deduplicated because on macOS the workspace sits inside the config directory, so
    # both helpers can name the same folder.
    found: list[Path] = []
    for base in (user_config_dir(), default_workspace()):
        path = base / DEFAULT_VOICE_DIR
        if path not in found:
            found.append(path)
    return found


def voice_dir() -> Path:
    """Where a voice downloaded now should go: the first directory on the search path."""
    return search_path()[0]


def _searched() -> str:
    """The search path, for a message that has to say "not here"."""
    return " or ".join(str(path) for path in search_path())


def _root_of(onnx: Path) -> Path | None:
    """Which search directory a model was found under, for messages that name a place."""
    for directory in search_path():
        try:
            onnx.relative_to(directory)
        except ValueError:
            continue
        return directory
    return None


def _quoted(path: Path | str) -> str:
    """A path as one shell word, for a fix line that is meant to be pasted.

    Double quotes rather than `shlex.quote`: the default directory contains a space on
    macOS ("Application Support"), and `"..."` is the one form cmd.exe, PowerShell and
    every POSIX shell agree on. Unquoted, curl reads two half-paths and drops a 60 MB
    model into a file called `Application`.
    """
    return '"' + str(path) + '"'


def config_for(onnx: Path) -> Path | None:
    """The model's config, which Piper cannot load it without.

    Piper looks for `<model>.onnx.json`. Some mirrors and a few hand-made exports drop the
    `.onnx`, so that spelling is accepted too and passed explicitly with `--config`.
    """
    for candidate in (Path(f"{onnx}.json"), onnx.with_suffix(".json")):
        if candidate.is_file():
            return candidate
    return None


def models(models_dir: Path | None = None) -> list[tuple[Path, Path | None]]:
    """Every `.onnx` on the search path, or in one named directory, with its config or None.

    The search is recursive because the Hugging Face repo is laid out
    `<language>/<locale>/<name>/<quality>/` and people clone or rsync it whole rather than
    flattening it into one folder.
    """
    dirs = search_path() if models_dir is None else [Path(models_dir)]
    found: list[Path] = []
    for directory in dirs:
        try:
            if directory.is_dir():
                found += sorted(directory.rglob("*.onnx"))
        except OSError:
            continue
    # `dict.fromkeys` keeps the order and drops the duplicate when one search directory
    # contains another, which on macOS they do.
    return [(onnx, config_for(onnx)) for onnx in dict.fromkeys(found)]


def _read_config(path: Path | None) -> dict:
    """A model's metadata, or an empty dict. Never raises: this only ever adds detail."""
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _section(info: dict, key: str) -> dict:
    value = info.get(key)
    return value if isinstance(value, dict) else {}


def _download_fix(models_dir: Path) -> str:
    onnx = models_dir / f"{EXAMPLE}.onnx"
    return (
        f"download a voice — both files — or set PIPER_VOICE_DIR to where yours already are:\n"
        f"  curl -L --create-dirs -o {_quoted(onnx)} {EXAMPLE_URL} "
        f"-o {_quoted(f'{onnx}.json')} {EXAMPLE_URL}.json\n"
        f"the rest, in dozens of languages, are at {VOICES_URL}"
    )


def _legacy_hint() -> str:
    """Voices sitting in `./piper-voices`, which was the default until it proved unschedulable.

    Not searched any more, but named: otherwise upgrading turns 60 MB of model that is
    right there into a baffling "no voices" and a second download.
    """
    try:
        stray = Path.cwd() / DEFAULT_VOICE_DIR
    except OSError:          # the working directory was deleted under us; advice only
        return ""
    if stray in search_path() or not any(config for _, config in models(stray)):
        return ""
    return (f"\nthere are voices in {stray}, which is no longer searched because the working "
            f"directory is not the same one the scheduler runs in: move them, or set "
            f"PIPER_VOICE_DIR={_quoted(stray)}")


def _find(name: str, models_dir: Path | None = None) -> Path | None:
    """One model, named either by path or by model name.

    A name is looked for on the whole search path, not just in the directory a download
    would go to: a voice in either place is installed, and "I can see it but piper says it
    is missing" is not an error anyone can act on.
    """
    candidate = Path(name).expanduser()
    if candidate.suffix == ".onnx":
        return candidate if candidate.is_file() else None
    if candidate.parent != Path("."):
        with_suffix = Path(f"{candidate}.onnx")
        return with_suffix if with_suffix.is_file() else None
    dirs = search_path() if models_dir is None else [Path(models_dir)]
    for directory in dirs:
        try:
            matches = sorted(directory.rglob(f"{name}.onnx")) if directory.is_dir() else []
        except OSError:
            continue
        if matches:
            return matches[0]
    return None


def _resolve(voice: str, model: str) -> tuple[Path, Path]:
    """The model to speak with and its config, or an error naming what is installed."""
    requested = voice.strip()
    if not requested and model.strip() and _find(model.strip()):
        # `tts.model` exists for ElevenLabs, where the model and the voice are separate
        # things, so a config written for it holds something like "eleven_multilingual_v2".
        # For Piper the model *is* the voice, so the field is honoured — but only when it
        # names something on disk, never as a reason to fail.
        requested = model.strip()

    if not requested:
        usable = [onnx for onnx, config in models() if config]
        if len(usable) == 1:
            return usable[0], config_for(usable[0])
        if not usable:
            raise PiperError(f"no piper voice models in {_searched()}. "
                             f"{_download_fix(voice_dir())}{_legacy_hint()}")
        raise PiperError(
            f"{len(usable)} piper voices are installed and none was chosen. Set tts.voice_id "
            f"in creatoros.yaml to one of: {', '.join(sorted(p.stem for p in usable))}"
        )

    found = _find(requested)
    if not found:
        installed = ", ".join(sorted(onnx.stem for onnx, _ in models())) or "(none)"
        raise PiperError(
            f"no piper voice model {requested!r}: looked for {requested}.onnx under "
            f"{_searched()}, and found {installed}. Models come from {VOICES_URL}; set "
            f"PIPER_VOICE_DIR if yours live somewhere else.{_legacy_hint()}"
        )
    config = config_for(found)
    if not config:
        raise PiperError(
            f"{found} has no config beside it, and piper cannot load a model without one. "
            f"Download {found.name}.json from {VOICES_URL} into {found.parent}."
        )
    return found, config


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _wav_is_empty(path: Path) -> bool:
    """Whether a WAV is a header with no samples behind it.

    Deliberately narrow: yes only for a RIFF/WAVE file whose `data` chunk is followed by
    nothing at all. Anything this cannot parse gets no opinion, because refusing to speak
    over a container we failed to read would be a worse bug than the one it catches.

    The declared chunk length is not consulted — some writers leave it at 0 or at
    0xFFFFFFFF when the output is a stream — only whether a single byte of audio exists.
    """
    try:
        with path.open("rb") as handle:
            header = handle.read(12)
            if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
                return False
            while True:
                chunk = handle.read(8)
                if len(chunk) < 8:
                    return False            # ran out before any data chunk: no opinion
                length = int.from_bytes(chunk[4:], "little")
                if chunk[:4] == b"data":
                    return handle.read(1) == b""
                # Chunks are word-aligned: an odd length carries a pad byte after it.
                handle.seek(length + length % 2, 1)
    except OSError:
        return False


def _tail(stream: bytes, limit: int = 400) -> str:
    text = " ".join(stream.decode("utf-8", "replace").split())
    return text[-limit:] if text else "no output"


class Piper:
    """Local, offline, free narration."""

    name = "piper"
    paid = False

    def available(self) -> Availability:
        """Each "no" here is a separate problem, because each has a separate fix.

        Collapsing "piper is missing" and "the voices are missing" into one answer sends
        half the people who hit it to the wrong page.
        """
        found = binary()
        if not found:
            override = os.environ.get("PIPER_BIN", "").strip()
            if override:
                return Availability.no(
                    f"PIPER_BIN is set to {override}, which is not an executable",
                    "point PIPER_BIN at the piper binary, or unset it to use PATH",
                )
            return Availability.no("piper is not on PATH", INSTALL_HINT[os_name()])

        installed = models()
        usable = [onnx for onnx, config in installed if config]
        if not usable:
            if installed:
                names = ", ".join(sorted(onnx.name for onnx, _ in installed))
                return Availability.no(
                    f"piper is installed, but no model in {_searched()} has its .onnx.json "
                    f"({names})",
                    f"a voice is two files: fetch <model>.onnx.json next to each model from "
                    f"{VOICES_URL}",
                )
            return Availability.no(
                f"piper is installed but there are no voice models in {_searched()}",
                _download_fix(voice_dir()) + _legacy_hint(),
            )

        if not media.find("ffmpeg"):
            # Piper writes WAV and core.narrate wants NN.mp3. Without ffmpeg this backend
            # can speak and still not deliver a clip, which is not "available".
            return Availability.no(
                "piper and its voices are installed, but ffmpeg is not on PATH and the "
                "clips have to be mp3",
                media.INSTALL_HINT[os_name()],
            )

        where = ", ".join(sorted({str(_root_of(onnx) or onnx.parent) for onnx in usable}))
        return Availability.yes(f"{found}, {len(usable)} voice(s) in {where}")

    def synthesize(self, text: str, out_path: Path, *, voice: str = "",
                   model: str = "", **options) -> Path:
        line = re.sub(r"\s+", " ", text).strip()
        if not line:
            raise PiperError("nothing to speak")
        found = binary()
        if not found:
            raise PiperError(f"piper is not on PATH. {INSTALL_HINT[os_name()]}")
        onnx, config = _resolve(voice, model)

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Intermediates sit beside the finished clip, in the directory core.narrate has
        # already created and written to. Neither name matches the `*.mp3` the narrator
        # and the renderer glob for, so a crashed run cannot leave something that looks
        # like audio.
        wav = Path(f"{out_path}.piper.wav")
        part = Path(f"{out_path}.part")

        command = [found, "--model", str(onnx), "--output_file", str(wav)]
        if config != Path(f"{onnx}.json"):
            command += ["--config", str(config)]
        if str(options.get("speaker", "")).strip():
            command += ["--speaker", str(options["speaker"]).strip()]
        if str(options.get("length_scale", "")).strip():
            command += ["--length_scale", str(options["length_scale"]).strip()]

        try:
            try:
                spoken = subprocess.run(command, input=line.encode("utf-8"),
                                        capture_output=True, timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                raise PiperError(f"piper produced nothing in {TIMEOUT}s on {onnx.name} "
                                 "and was killed") from None
            except OSError as exc:
                raise PiperError(f"piper could not be run: {exc}") from None
            if spoken.returncode != 0:
                raise PiperError(
                    f"piper exited {spoken.returncode} on {onnx.name}: {_tail(spoken.stderr)}"
                )
            if not wav.is_file() or wav.stat().st_size == 0:
                raise PiperError(f"piper wrote no audio for {onnx.name}: {_tail(spoken.stderr)}")
            if _wav_is_empty(wav):
                # Exit 0, a valid WAV, nothing in it. Piper does this when its phonemiser
                # produces no phonemes for the text — a script the voice has no model for
                # is the usual reason. A size check passes a 44-byte header, and ffmpeg
                # turns zero samples into a frameless mp3 that is not empty either, so
                # this used to be returned as a finished clip: the manifest recorded it,
                # the renderer fell back to the scene's minimum duration, and the video
                # shipped with the line silent. Every later run then said "unchanged".
                raise PiperError(
                    f"piper exited cleanly but spoke nothing: {onnx.stem} produced an "
                    f"empty wav for {len(line)} characters. Its phonemiser most likely "
                    f"has no voice for this script — use a model for the language you are "
                    f"writing in, from {VOICES_URL}. piper said: {_tail(spoken.stderr)}"
                )

            # `-f mp3` is explicit because the extension ffmpeg would infer the format
            # from is `.part` at this point.
            try:
                converted = subprocess.run(
                    [media.require("ffmpeg"), "-y", "-loglevel", "error", "-i", str(wav),
                     "-f", "mp3", "-b:a", "128k", "-ar", "44100", "-ac", "1", str(part)],
                    capture_output=True, timeout=TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                raise PiperError(f"ffmpeg did not convert piper's wav in {TIMEOUT}s and "
                                 "was killed") from None
            if converted.returncode != 0:
                raise PiperError(
                    f"ffmpeg could not turn piper's wav into mp3: {_tail(converted.stderr)}"
                )
            if not part.is_file() or part.stat().st_size == 0:
                raise PiperError(
                    f"ffmpeg exited 0 but produced no mp3 from piper's wav: "
                    f"{_tail(converted.stderr)}"
                )
            # The rename is the last step, so the clip at out_path is either the previous
            # one or a whole new one — never a half-converted file, which `core.narrate`
            # would cache as done and the video would ship truncated.
            os.replace(part, out_path)
        finally:
            _discard(wav)
            _discard(part)
        return out_path

    def voices(self) -> list[Voice]:
        """What is on disk. Empty is a normal answer — the models are a separate download."""
        out = []
        for onnx, config in models():
            info = _read_config(config)
            language = _section(info, "language")
            code = language.get("code") or _section(info, "espeak").get("voice") or ""
            try:
                speakers = int(info.get("num_speakers") or 1)
            except (TypeError, ValueError):
                speakers = 1

            detail = []
            if config is None:
                detail.append("no .onnx.json, so piper cannot load it")
            rate = _section(info, "audio").get("sample_rate")
            if rate:
                detail.append(f"{rate} Hz")
            if speakers > 1:
                detail.append(f"{speakers} speakers — pick one with speaker=N")
            root = _root_of(onnx)
            where = onnx.parent.relative_to(root) if root else onnx.parent
            if str(where) != ".":
                detail.append(f"in {where}")

            out.append(Voice(
                id=onnx.stem,
                # The config's "dataset" is the voice's actual name — lessac, amy, ryan —
                # where the id is the whole locale-name-quality filename.
                name=str(info.get("dataset") or onnx.stem),
                languages=(code,) if code else (),
                detail=", ".join(detail),
            ))
        return out
