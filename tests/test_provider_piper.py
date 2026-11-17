# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Piper backend: where its voices live, and what it does when piper says nothing.

Both defects behind these tests are the shape this project keeps finding. The voice
directory defaulted to `./piper-voices`, so it was right whenever a human looked at it and
wrong in the 06:30 run nobody watches. And a piper that exited 0 having spoken nothing
produced a clip that was returned as finished, cached as finished, and shipped silent.

The fakes here are a stub `piper` and a stub `ffmpeg` rather than mocks: every one of these
failures lives in what a child process did, and a patched `subprocess.run` would have
agreed with whatever the code already believed.
"""
import os
import shlex
import shutil
import sys
import wave
from pathlib import Path

import pytest

from creatoros_autopilot.core import media
from creatoros_autopilot.providers.tts import piper

VOICE = "en_US-lessac-medium"

# Everything the search path can be built from, on any of the three platforms. A test that
# left one of these pointing at the real machine would plant a voice in the user's own
# config directory.
HOME_VARS = ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME",
             "XDG_DATA_HOME", "CREATOROS_WORKSPACE", "PIPER_VOICE_DIR", "PIPER_BIN")

# A stub piper: reads stdin like the real one, writes a WAV of FRAMES frames. Zero frames
# is piper's clean-exit-with-nothing-said, which is the whole point of one of these tests.
SPEAKS = '''
import sys, wave
out, args = None, sys.argv[1:]
while args:
    if args[0] == "--output_file":
        out, args = args[1], args[2:]
    else:
        args = args[1:]
sys.stdin.buffer.read()
with wave.open(out, "wb") as handle:
    handle.setnchannels(1)
    handle.setsampwidth(2)
    handle.setframerate(22050)
    handle.writeframes(b"\\x00\\x01" * FRAMES)
'''

SLEEPS = '''
import sys, time
sys.stdin.buffer.read()
time.sleep(30)
'''

NO_OP = "import sys\nsys.stdin.buffer.read() if not sys.stdin.isatty() else None\n"

FAILS = 'import sys\nsys.stderr.write("stub failure\\n")\nsys.exit(1)\n'

WRITES_NOTHING = "import sys\nsys.exit(0)\n"


def stub(bin_dir: Path, name: str, body: str) -> Path:
    """An executable called `name` that runs `body` as Python, on this OS.

    A shebang is enough on POSIX. Windows ignores it, so the launcher is a `.cmd` that
    calls the interpreter — `shutil.which`, which is how this module finds piper, resolves
    that through PATHEXT.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        script = bin_dir / f"{name}_stub.py"
        script.write_text(body, encoding="utf-8")
        launcher = bin_dir / f"{name}.cmd"
        launcher.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        launcher = bin_dir / name
        launcher.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
        launcher.chmod(0o755)
    return launcher


def plant(directory: Path, name: str = VOICE, config: bool = True) -> Path:
    """A voice as piper sees it: two files, the second one not optional."""
    directory.mkdir(parents=True, exist_ok=True)
    onnx = directory / f"{name}.onnx"
    onnx.write_bytes(b"\0" * 2048)
    if config:
        (directory / f"{name}.onnx.json").write_text(
            '{"dataset": "lessac", "language": {"code": "en_US"},'
            ' "audio": {"sample_rate": 22050}, "num_speakers": 1}', encoding="utf-8")
    return onnx


@pytest.fixture
def elsewhere(tmp_path, monkeypatch):
    """A machine whose home, config and workspace all sit inside tmp_path.

    Returned as the home directory. Asserts the redirection took, because a search path
    that still pointed at the real machine would make every test below pass by accident.
    """
    home = tmp_path / "home"
    home.mkdir()
    for name in HOME_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    for directory in piper.search_path():
        assert home in directory.parents, f"{directory} is outside the fake home"
    return home


@pytest.fixture
def speaking_piper(tmp_path, monkeypatch, elsewhere):
    """A piper that really writes a WAV, with a voice installed where the module looks."""
    plant(piper.search_path()[0])

    def install(frames: int = 22050, body: str = SPEAKS) -> Path:
        bin_dir = tmp_path / "bin"
        stub(bin_dir, "piper", body.replace("FRAMES", str(frames)))
        monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
        return bin_dir

    return install


class TestWhereVoicesLive:
    """Defect 1: the default was `Path.cwd() / "piper-voices"`."""

    def test_the_search_path_does_not_depend_on_the_working_directory(self, tmp_path,
                                                                      monkeypatch, elsewhere):
        """The regression itself. launchd runs with cwd=/ and systemd with cwd=$HOME, and
        `runtime/units.py` sets neither, so a relative default is resolved against a
        directory the user never chose."""
        (tmp_path / "project").mkdir()
        monkeypatch.chdir(tmp_path / "project")
        from_project = piper.search_path()
        monkeypatch.chdir(tmp_path)
        assert piper.search_path() == from_project
        assert all(path.is_absolute() for path in from_project)

    def test_the_working_directory_is_never_searched(self, tmp_path, monkeypatch, elsewhere):
        """A voice under the old default must not make `available()` say yes: it would say
        yes by hand and no on the schedule, which is how this stayed hidden."""
        monkeypatch.chdir(tmp_path)
        plant(tmp_path / piper.DEFAULT_VOICE_DIR)
        assert Path.cwd() / piper.DEFAULT_VOICE_DIR not in piper.search_path()
        assert piper.models() == []

    @pytest.mark.parametrize("index", [0, -1])
    def test_a_voice_is_found_from_any_directory(self, tmp_path, monkeypatch, elsewhere, index):
        """Both searched directories, from two working directories each."""
        plant(piper.search_path()[index])
        bin_dir = tmp_path / "bin"
        stub(bin_dir, "piper", NO_OP)
        stub(bin_dir, "ffmpeg", NO_OP)
        monkeypatch.setenv("PATH", str(bin_dir))
        for where in (tmp_path, elsewhere):
            monkeypatch.chdir(where)
            assert piper.Piper().available().ok, f"not available from {where}"

    def test_piper_voice_dir_overrides_both(self, tmp_path, monkeypatch, elsewhere):
        plant(piper.search_path()[0])
        mine = tmp_path / "my-voices"
        plant(mine, name="hi_IN-pratham-medium")
        monkeypatch.setenv("PIPER_VOICE_DIR", str(mine))
        assert piper.search_path() == [mine]
        assert [onnx.stem for onnx, _ in piper.models()] == ["hi_IN-pratham-medium"]

    @pytest.mark.parametrize("index", [0, -1])
    def test_a_voice_named_in_config_is_found_wherever_it_sits(self, monkeypatch,
                                                               elsewhere, index):
        """`tts.voice_id` is matched against the whole search path. Resolving it only in
        the directory a download would go to would report an installed voice as missing."""
        plant(piper.search_path()[index], name="hi_IN-pratham-medium")
        found, config = piper._resolve("hi_IN-pratham-medium", "")
        assert found.stem == "hi_IN-pratham-medium" and config.is_file()

    def test_the_download_line_survives_a_space_in_the_path(self, tmp_path):
        """The default directory contains "Application Support" on macOS. Unquoted, curl
        read two half-paths and dropped a 60 MB model into a file called `Application`."""
        directory = tmp_path / "Application Support" / "creatoros" / "piper-voices"
        line = next(l for l in piper._download_fix(directory).splitlines()
                    if l.strip().startswith("curl"))
        argv = shlex.split(line)
        assert argv[argv.index("-o") + 1] == str(directory / f"{VOICE}.onnx")
        assert argv[-2] == str(directory / f"{VOICE}.onnx.json")

    def test_the_download_line_names_an_absolute_directory(self, elsewhere):
        """It used to write into whatever directory the user happened to be in."""
        assert piper.voice_dir().is_absolute()
        assert str(piper.voice_dir()) in piper._download_fix(piper.voice_dir())

    def test_voices_left_in_the_old_default_are_named(self, tmp_path, monkeypatch, elsewhere):
        """Upgrading must not turn 60 MB of model that is right there into a second
        download."""
        monkeypatch.chdir(tmp_path)
        plant(tmp_path / piper.DEFAULT_VOICE_DIR)
        bin_dir = tmp_path / "bin"
        stub(bin_dir, "piper", NO_OP)
        stub(bin_dir, "ffmpeg", NO_OP)
        monkeypatch.setenv("PATH", str(bin_dir))
        answer = piper.Piper().available()
        assert not answer.ok
        assert str(tmp_path / piper.DEFAULT_VOICE_DIR) in answer.fix
        assert "PIPER_VOICE_DIR" in answer.fix

    def test_a_no_always_carries_a_fix(self, tmp_path, monkeypatch, elsewhere):
        """The protocol's one requirement of `available()`."""
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        assert not piper.Piper().available().ok and piper.Piper().available().fix

        bin_dir = tmp_path / "bin"
        stub(bin_dir, "piper", NO_OP)
        stub(bin_dir, "ffmpeg", NO_OP)
        monkeypatch.setenv("PATH", str(bin_dir))
        no_voices = piper.Piper().available()
        assert not no_voices.ok and "curl" in no_voices.fix

        plant(piper.search_path()[0], config=False)
        half = piper.Piper().available()
        assert not half.ok and ".onnx.json" in half.fix

    def test_nothing_reads_the_environment_at_import_time(self, tmp_path, monkeypatch):
        """`voice_dir` is read per call, so the daily run sees the environment it has."""
        monkeypatch.setenv("PIPER_VOICE_DIR", str(tmp_path / "a"))
        assert piper.search_path() == [tmp_path / "a"]
        monkeypatch.setenv("PIPER_VOICE_DIR", str(tmp_path / "b"))
        assert piper.search_path() == [tmp_path / "b"]

    def test_voices_are_listed_with_where_they_are(self, monkeypatch, elsewhere):
        plant(piper.search_path()[0] / "en" / "en_US" / "lessac" / "medium")
        listed = piper.Piper().voices()
        assert [voice.id for voice in listed] == [VOICE]
        assert listed[0].name == "lessac"
        assert "in en" in listed[0].detail and "22050 Hz" in listed[0].detail


def wav_bytes(path: Path, frames: int) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(22050)
        handle.writeframes(b"\x00\x01" * frames)
    return path


class TestEmptyWav:
    """Defect 2: a clean exit having spoken nothing."""

    def test_a_header_with_no_samples_is_empty(self, tmp_path):
        path = wav_bytes(tmp_path / "silent.wav", 0)
        assert path.stat().st_size == 44        # the exact file piper leaves behind
        assert piper._wav_is_empty(path)

    def test_audio_is_not_empty(self, tmp_path):
        assert not piper._wav_is_empty(wav_bytes(tmp_path / "spoken.wav", 1))

    @pytest.mark.parametrize("content", [b"", b"not a wav at all", b"RIFF\0\0\0\0WAVE"])
    def test_anything_unparseable_gets_no_opinion(self, tmp_path, content):
        """Refusing to speak over a container we failed to read would be the worse bug."""
        path = tmp_path / "odd.wav"
        path.write_bytes(content)
        assert not piper._wav_is_empty(path)


class TestSynthesize:
    def test_a_clean_exit_with_an_empty_wav_raises_and_writes_nothing(self, tmp_path,
                                                                     speaking_piper):
        """It used to return the path. ffmpeg turns zero samples into a frameless mp3 that
        is not empty either, so the size check passed and the video shipped silent."""
        speaking_piper(frames=0)
        out = tmp_path / "vo" / "01.mp3"
        with pytest.raises(piper.PiperError) as exc:
            piper.Piper().synthesize("Transformers changed how we build models.", out)
        assert "spoke nothing" in str(exc.value)
        assert not out.exists()
        assert list(out.parent.iterdir()) == []

    def test_a_failed_run_leaves_the_previous_clip_alone(self, tmp_path, speaking_piper):
        """The protocol asks for an atomic write: the clip is the old one or a whole new
        one, never a corpse of this attempt."""
        speaking_piper(frames=0)
        out = tmp_path / "vo" / "01.mp3"
        out.parent.mkdir(parents=True)
        out.write_bytes(b"the clip from the last run")
        with pytest.raises(piper.PiperError):
            piper.Piper().synthesize("A line that cannot be spoken.", out)
        assert out.read_bytes() == b"the clip from the last run"
        assert sorted(p.name for p in out.parent.iterdir()) == ["01.mp3"]

    def test_nothing_is_left_behind_when_ffmpeg_fails(self, tmp_path, monkeypatch,
                                                      speaking_piper):
        bin_dir = speaking_piper(frames=22050)
        stub(bin_dir, "ffmpeg", FAILS)
        out = tmp_path / "vo" / "01.mp3"
        with pytest.raises(piper.PiperError) as exc:
            piper.Piper().synthesize("A line worth hearing.", out)
        assert "ffmpeg" in str(exc.value)
        assert not out.exists() and list(out.parent.iterdir()) == []

    def test_ffmpeg_exiting_zero_without_an_mp3_is_not_a_success(self, tmp_path,
                                                                 speaking_piper):
        bin_dir = speaking_piper(frames=22050)
        stub(bin_dir, "ffmpeg", WRITES_NOTHING)
        out = tmp_path / "vo" / "01.mp3"
        with pytest.raises(piper.PiperError) as exc:
            piper.Piper().synthesize("A line worth hearing.", out)
        assert "no mp3" in str(exc.value)
        assert not out.exists()

    def test_a_piper_that_hangs_is_killed_and_explained(self, tmp_path, monkeypatch,
                                                        speaking_piper):
        """A bare TimeoutExpired told the user nothing about which backend stalled."""
        speaking_piper(body=SLEEPS)
        monkeypatch.setattr(piper, "TIMEOUT", 1)
        out = tmp_path / "vo" / "01.mp3"
        with pytest.raises(piper.PiperError) as exc:
            piper.Piper().synthesize("A line worth hearing.", out)
        assert "killed" in str(exc.value)
        assert not out.exists() and list(out.parent.iterdir()) == []

    def test_a_spoken_line_becomes_a_readable_mp3(self, tmp_path, speaking_piper):
        """The happy path, through the real ffmpeg: the guards must not reject real audio."""
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            pytest.skip("ffmpeg and ffprobe are needed to convert piper's wav")
        speaking_piper(frames=22050)
        out = tmp_path / "vo" / "01.mp3"
        assert piper.Piper().synthesize("A line worth hearing.", out) == out
        assert media.duration(str(out)) == pytest.approx(1.0, abs=0.2)
        assert sorted(p.name for p in out.parent.iterdir()) == ["01.mp3"]

    def test_a_short_clip_is_not_this_backends_business(self, tmp_path, speaking_piper):
        """Whether a clip is too short for its text is `core.narrate`'s judgement, made
        once for every backend. Duplicating it here would mean two thresholds to keep in
        step, and this one has no idea what the text was."""
        if not shutil.which("ffmpeg"):
            pytest.skip("ffmpeg is needed to convert piper's wav")
        speaking_piper(frames=6615)         # 0.3s against a line that wants several
        out = tmp_path / "vo" / "01.mp3"
        assert piper.Piper().synthesize("A much longer line than this clip.", out) == out
        assert out.stat().st_size > 0

    def test_an_empty_line_is_refused_before_anything_runs(self, tmp_path, speaking_piper):
        speaking_piper()
        with pytest.raises(piper.PiperError):
            piper.Piper().synthesize("   \n ", tmp_path / "vo" / "01.mp3")
