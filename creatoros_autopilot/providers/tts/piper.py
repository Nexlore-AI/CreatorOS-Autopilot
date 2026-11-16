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
from ...runtime import os_name

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


def voice_dir() -> Path:
    """Where voice models are looked for.

    Read on every call rather than at import: the default is relative to the working
    directory, and the CLI renders from wherever the user happens to be.
    """
    env = os.environ.get("PIPER_VOICE_DIR", "").strip()
    return Path(env).expanduser() if env else Path.cwd() / DEFAULT_VOICE_DIR


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
    """Every `.onnx` under the models directory, each with its config or None.

    The search is recursive because the Hugging Face repo is laid out
    `<language>/<locale>/<name>/<quality>/` and people clone or rsync it whole rather than
    flattening it into one folder.
    """
    models_dir = voice_dir() if models_dir is None else models_dir
    try:
        if not models_dir.is_dir():
            return []
        found = sorted(models_dir.rglob("*.onnx"))
    except OSError:
        return []
    return [(onnx, config_for(onnx)) for onnx in found]


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
        f"  curl -L --create-dirs -o {onnx} {EXAMPLE_URL} -o {onnx}.json {EXAMPLE_URL}.json\n"
        f"the rest, in dozens of languages, are at {VOICES_URL}"
    )


def _find(name: str, models_dir: Path) -> Path | None:
    """One model, named either by path or by model name."""
    candidate = Path(name).expanduser()
    if candidate.suffix == ".onnx":
        return candidate if candidate.is_file() else None
    if candidate.parent != Path("."):
        with_suffix = Path(f"{candidate}.onnx")
        return with_suffix if with_suffix.is_file() else None
    try:
        matches = sorted(models_dir.rglob(f"{name}.onnx")) if models_dir.is_dir() else []
    except OSError:
        return None
    return matches[0] if matches else None


def _resolve(voice: str, model: str) -> tuple[Path, Path]:
    """The model to speak with and its config, or an error naming what is installed."""
    models_dir = voice_dir()
    requested = voice.strip()
    if not requested and model.strip() and _find(model.strip(), models_dir):
        # `tts.model` exists for ElevenLabs, where the model and the voice are separate
        # things, so a config written for it holds something like "eleven_multilingual_v2".
        # For Piper the model *is* the voice, so the field is honoured — but only when it
        # names something on disk, never as a reason to fail.
        requested = model.strip()

    if not requested:
        usable = [onnx for onnx, config in models(models_dir) if config]
        if len(usable) == 1:
            return usable[0], config_for(usable[0])
        if not usable:
            raise PiperError(f"no piper voice models in {models_dir}. {_download_fix(models_dir)}")
        raise PiperError(
            f"{len(usable)} piper voices are installed and none was chosen. Set tts.voice_id "
            f"in creatoros.yaml to one of: {', '.join(sorted(p.stem for p in usable))}"
        )

    found = _find(requested, models_dir)
    if not found:
        installed = ", ".join(sorted(onnx.stem for onnx, _ in models(models_dir))) or "(none)"
        raise PiperError(
            f"no piper voice model {requested!r}: looked for {requested}.onnx under "
            f"{models_dir}, and found {installed}. Models come from {VOICES_URL}; set "
            "PIPER_VOICE_DIR if yours live somewhere else."
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

        models_dir = voice_dir()
        installed = models(models_dir)
        usable = [onnx for onnx, config in installed if config]
        if not usable:
            if installed:
                names = ", ".join(sorted(onnx.name for onnx, _ in installed))
                return Availability.no(
                    f"piper is installed, but no model in {models_dir} has its .onnx.json "
                    f"({names})",
                    f"a voice is two files: fetch <model>.onnx.json next to each model from "
                    f"{VOICES_URL}",
                )
            return Availability.no(
                f"piper is installed but there are no voice models in {models_dir}",
                _download_fix(models_dir),
            )

        if not media.find("ffmpeg"):
            # Piper writes WAV and core.narrate wants NN.mp3. Without ffmpeg this backend
            # can speak and still not deliver a clip, which is not "available".
            return Availability.no(
                "piper and its voices are installed, but ffmpeg is not on PATH and the "
                "clips have to be mp3",
                media.INSTALL_HINT[os_name()],
            )

        return Availability.yes(f"{found}, {len(usable)} voice(s) in {models_dir}")

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
            spoken = subprocess.run(command, input=line.encode("utf-8"),
                                    capture_output=True, timeout=TIMEOUT)
            if spoken.returncode != 0:
                raise PiperError(
                    f"piper exited {spoken.returncode} on {onnx.name}: {_tail(spoken.stderr)}"
                )
            if not wav.is_file() or wav.stat().st_size == 0:
                raise PiperError(f"piper wrote no audio for {onnx.name}: {_tail(spoken.stderr)}")

            # `-f mp3` is explicit because the extension ffmpeg would infer the format
            # from is `.part` at this point.
            converted = subprocess.run(
                [media.require("ffmpeg"), "-y", "-loglevel", "error", "-i", str(wav),
                 "-f", "mp3", "-b:a", "128k", "-ar", "44100", "-ac", "1", str(part)],
                capture_output=True, timeout=TIMEOUT,
            )
            if converted.returncode != 0:
                raise PiperError(
                    f"ffmpeg could not turn piper's wav into mp3: {_tail(converted.stderr)}"
                )
            os.replace(part, out_path)
        finally:
            _discard(wav)
            _discard(part)
        return out_path

    def voices(self) -> list[Voice]:
        """What is on disk. Empty is a normal answer — the models are a separate download."""
        models_dir = voice_dir()
        out = []
        for onnx, config in models(models_dir):
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
            try:
                where = onnx.parent.relative_to(models_dir)
            except ValueError:
                where = onnx.parent
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
