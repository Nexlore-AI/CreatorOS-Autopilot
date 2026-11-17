# SPDX-License-Identifier: AGPL-3.0-or-later
"""The `say` backend: what it must refuse, and what it must not quietly decide.

Every bug this file pins down had the same shape — `say` exits 0, writes a valid file and
speaks less than it was given. So the fake `say` here is not a stub that says yes: it
declines scripts the way the real binary declines them (0.0116 s of audio, exit 0), and it
parses a leading hyphen as an option the way the real binary does. A fake that always
succeeded would have passed every version of this module, including the broken ones.

The measurements the module's comments rest on are checked against the real `say` too, in
`TestOnThisMac`, which skips everywhere else.
"""
import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

from creatoros_autopilot.core import media
from creatoros_autopilot.interfaces.availability import Availability
from creatoros_autopilot.providers.tts import say as say_mod
from creatoros_autopilot.runtime import is_macos, is_windows

HINDI = "नमस्ते दोस्तों आज हम बात करेंगे पैसे के बारे में"
JAPANESE = "今日は新しいモデルについて話します"
RUSSIAN = "Сегодня мы поговорим о деньгах"
KOREAN = "오늘은 돈에 대해 이야기하겠습니다"
ENGLISH = "Today we talk about what this actually costs."

# The fake Mac's voice list, shaped like the real one: the Siri voices carry a
# parenthesised locale in their name, two voices share the en_IN locale and disagree about
# Devanagari, and Geeta introduces herself in English although she reads Telugu.
VOICES = [
    ("Samantha", "en_US", "Hello! My name is Samantha."),
    ("Rishi", "en_IN", "Hello! My name is Rishi."),
    ("Tara (English (India))", "en_IN", "Hello! My name is Tara."),
    ("Aman (English (India))", "en_IN", "Hello! My name is Aman."),
    ("Lekha", "hi_IN", "नमस्ते, मेरा नाम लेखा है।"),
    ("Kyoko", "ja_JP", "こんにちは! 私の名前はKyokoです。"),
    ("Geeta", "te_IN", "Hello! My name is Geeta."),
    ("Eddy (English (US))", "en_US", "Hello! My name is Eddy."),
]
READS = {
    "Tara (English (India))": ["DEVANAGARI"],
    "Aman (English (India))": ["DEVANAGARI"],
    "Lekha": ["DEVANAGARI"],
    "Kyoko": ["HIRAGANA", "KATAKANA", "CJK"],
    "Geeta": ["TELUGU"],
}

STUB = '''\
"""A `say` that fails the way the real one fails. Configured through $SAY_STUB."""
import json, os, sys, unicodedata, wave

RATE = 22050
DECLINED = 0.0116          # what macOS writes when a voice will not read the script
PER_CHAR = 0.03            # measured: real speech runs 0.031-0.044 s per character


def scripts(text):
    found = set()
    for char in text:
        if not char.isalpha():
            continue
        try:
            name = unicodedata.name(char)
        except ValueError:
            continue
        script = name.split()[0].split("-")[0]
        if script != "LATIN":
            found.add(script)
    return found


def main(argv):
    config = json.loads(os.environ["SAY_STUB"])
    log = os.environ.get("SAY_STUB_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(argv) + "\\n")

    voice, out, message, index = config["default_voice"], None, None, 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--":
            message = " ".join(argv[index + 1:])
            break
        if arg in ("-v", "-o", "-r", "-f"):
            value = argv[index + 1] if index + 1 < len(argv) else ""
            if arg == "-v":
                voice = value
            if arg == "-o":
                out = value
            index += 2
            continue
        if arg.startswith("--data-format="):
            index += 1
            continue
        if arg.startswith("-"):
            sys.stderr.write("say: invalid option -- %s\\n" % arg.lstrip("-")[:1])
            sys.stderr.write("Usage: say [-v voice] [-o out] [-f in | message]\\n")
            return 1
        message, index = arg, index + 1

    if voice == "?":
        for name, lang, example in config["voices"]:
            sys.stdout.write("%-18s %-8s # %s\\n" % (name, lang, example))
        return 0
    names = [name for name, _, _ in config["voices"]]
    if voice and voice not in names:
        sys.stderr.write("Voice '%s' not found\\n" % voice)
        return 1
    if message is None:
        sys.stderr.write("say: no message\\n")
        return 1

    reads = set(config["reads"].get(voice, []))
    speaks = scripts(message) <= reads
    seconds = max(DECLINED, PER_CHAR * len(message)) if speaks else DECLINED
    if out:
        with wave.open(out, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(RATE)
            handle.writeframes(b"\\0\\0" * int(RATE * seconds))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''


class FakeMac:
    """A `say` on PATH, plus the knobs the tests need: the listing and the system voice."""

    def __init__(self, monkeypatch, tmp_path):
        self._monkeypatch = monkeypatch
        folder = tmp_path / "fakebin"
        folder.mkdir()
        script = folder / "say_stub.py"
        script.write_text(STUB, encoding="utf-8")
        launcher = folder / "say"
        launcher.write_text(f"#!{sys.executable}\n"
                            f"import runpy, sys\n"
                            f"sys.argv[0] = 'say'\n"
                            f"runpy.run_path({str(script)!r}, run_name='__main__')\n",
                            encoding="utf-8")
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self.log = tmp_path / "say-calls.jsonl"
        monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ.get('PATH', '')}")
        monkeypatch.setenv("SAY_STUB_LOG", str(self.log))
        monkeypatch.setattr(say_mod, "is_macos", lambda: True)
        self.configure()

    def configure(self, voices=VOICES, reads=READS, default_voice="Samantha"):
        """Which voices this Mac has, what each really reads, and the System Settings one."""
        self._monkeypatch.setenv("SAY_STUB", json.dumps(
            {"voices": voices, "reads": reads, "default_voice": default_voice}))
        return say_mod.Say()

    @property
    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def spoken(self):
        """The messages `say` was actually asked to speak, in order."""
        said = []
        for call in self.calls:
            if "--" in call and call[-1] != "?":
                said.append(call[call.index("--") + 1])
        return said


# The fake Mac is a shebang script, which PATH lookup on Windows will not run, and both
# the probe and the mp3 conversion need ffmpeg. Neither is worth a weaker test.
pytestmark = pytest.mark.skipif(
    is_windows() or not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="needs a POSIX PATH for the fake `say`, and ffmpeg/ffprobe for the probe",
)


@pytest.fixture
def mac(monkeypatch, tmp_path):
    return FakeMac(monkeypatch, tmp_path)


class TestScriptDetection:
    def test_it_names_every_writing_system_in_the_line(self):
        assert say_mod.scripts_in(HINDI) == ("DEVANAGARI",)
        assert set(say_mod.scripts_in(JAPANESE)) == {"CJK", "HIRAGANA", "KATAKANA"}
        assert say_mod.scripts_in(RUSSIAN) == ("CYRILLIC",)
        assert say_mod.scripts_in(KOREAN) == ("HANGUL",)

    def test_latin_and_punctuation_need_no_voice_chosen_for_them(self):
        assert say_mod.scripts_in(ENGLISH) == ()
        assert say_mod.scripts_in("-20%, down from 2024 — see [1].") == ()

    def test_a_hinglish_line_is_found_through_its_english(self):
        """The real narration is mixed, and the Devanagari in it is the part at risk."""
        assert say_mod.scripts_in("Revenue grew बीस परसेंट last quarter.") == ("DEVANAGARI",)


class TestAutoSelection:
    def test_another_script_is_not_handed_to_a_hindi_voice(self, mac):
        """The bug: the preference list was consulted before the line was looked at, so
        every script got Lekha and `say` returned 0.0116 s of nothing with exit 0."""
        backend = mac.configure(default_voice="Samantha")
        assert backend._voice_for("", JAPANESE) == "Kyoko"
        assert backend._voice_for("", HINDI) == "Lekha"

    def test_the_clip_it_produces_is_audible(self, mac, tmp_path):
        backend = mac.configure(default_voice="Samantha")
        clip = backend.synthesize(JAPANESE, tmp_path / "jp.mp3")
        assert media.duration(str(clip)) > 0.2, "a Japanese line came back as near-silence"

    def test_it_leaves_the_system_voice_alone_for_a_latin_line(self, mac):
        """Overriding the user's System Settings voice is what the explicit path refuses to
        do; auto-selection used to do it on every line, English ones included."""
        backend = mac.configure(default_voice="Samantha")
        assert backend._voice_for("", ENGLISH) == ""
        assert mac.spoken() == [], "an English line cost a synthesis probe"

    def test_it_keeps_the_system_voice_when_that_voice_can_read_the_line(self, mac):
        backend = mac.configure(default_voice="Lekha")
        assert backend._voice_for("", HINDI) == ""

    def test_it_refuses_rather_than_guessing_when_no_voice_reads_the_script(self, mac, tmp_path):
        backend = mac.configure(default_voice="Samantha")
        with pytest.raises(say_mod.SayError) as refusal:
            backend.synthesize(KOREAN, tmp_path / "ko.mp3")
        assert "Hangul" in str(refusal.value)
        assert not (tmp_path / "ko.mp3").exists(), "a refused line still left a clip behind"

    def test_it_measures_each_voice_once_per_script(self, mac, tmp_path):
        """A probe is half a second of a render, and narration comes through here per scene."""
        backend = mac.configure(default_voice="Samantha")
        for index in range(4):
            backend.synthesize(HINDI, tmp_path / f"{index}.mp3")
        probes = [said for said in mac.spoken() if said != HINDI]
        assert len(probes) <= 2, f"{len(probes)} probes for four lines of one script"


class TestLocaleIsNotCapability:
    def test_a_mac_with_tara_but_no_lekha_still_reads_hindi(self, mac):
        """Lekha is an optional download. Treating `hi_*` as required refused Tara, which
        reads Devanagari in 3.03 s, and failed every Hinglish line on such a machine."""
        voices = [voice for voice in VOICES if voice[1] != "hi_IN"]
        backend = mac.configure(voices=voices, default_voice="Samantha")
        assert backend._voice_for("", HINDI) == "Tara (English (India))"

    def test_such_a_mac_is_not_told_it_has_no_hindi_voice(self, mac):
        voices = [voice for voice in VOICES if voice[1] != "hi_IN"]
        backend = mac.configure(voices=voices)
        ready = backend.available()
        assert ready.ok
        assert "Tara (English (India))" in ready.detail
        assert "none" not in ready.detail

    def test_the_same_locale_can_still_be_refused(self, mac, tmp_path):
        """Rishi is en_IN too and writes 0.0116 s, so the locale cannot be the test in
        either direction — only the measurement can."""
        backend = mac.configure()
        with pytest.raises(say_mod.SayError) as refusal:
            backend.synthesize(HINDI, tmp_path / "hi.mp3", voice="Rishi")
        message = str(refusal.value)
        assert "Rishi" in message and "Devanagari" in message
        assert "0.0116" in message, "the refusal does not say what was measured"
        assert "Lekha" in message, "the refusal does not name a voice that would work"

    def test_an_en_in_siri_voice_is_accepted_when_it_is_asked_for(self, mac, tmp_path):
        backend = mac.configure()
        clip = backend.synthesize(HINDI, tmp_path / "hi.mp3", voice="Tara")
        assert media.duration(str(clip)) > 0.2

    def test_a_bare_siri_name_resolves_to_the_voice_say_lists(self, mac):
        """macOS lists it as `Tara (English (India))`; nobody writes that in a config file,
        and the module's own preference list did not either."""
        backend = mac.configure()
        assert backend._voice_for("Tara", HINDI) == "Tara (English (India))"

    def test_an_ambiguous_name_is_not_resolved_silently(self, mac):
        voices = VOICES + [("Eddy (English (UK))", "en_GB", "Hello! My name is Eddy.")]
        backend = mac.configure(voices=voices)
        with pytest.raises(say_mod.SayError, match="2 voices"):
            backend._voice_for("Eddy", ENGLISH)

    def test_an_unknown_name_still_names_the_way_to_list_them(self, mac):
        backend = mac.configure()
        with pytest.raises(say_mod.SayError, match="say -v"):
            backend._voice_for("Nonesuch", ENGLISH)

    def test_nominees_are_narrowed_by_the_script_not_by_the_project(self, mac):
        backend = mac.configure()
        voices = backend.voices()
        devanagari = say_mod.nominees(("DEVANAGARI",), voices)
        assert devanagari[0] == "Lekha"
        assert "Tara (English (India))" in devanagari
        assert "Kyoko" not in devanagari
        assert "Kyoko" in say_mod.nominees(("CJK", "HIRAGANA"), voices)
        assert "Lekha" not in say_mod.nominees(("CJK", "HIRAGANA"), voices)

    def test_a_voice_whose_example_is_english_is_nominated_by_its_locale(self, mac):
        """Geeta reads Telugu and says hello in English, so the listing proves nothing and
        the nomination has to come from somewhere else."""
        backend = mac.configure(voices=VOICES + [("Shruti", "te_IN", "తెలుగు మాట్లాడతాను.")])
        assert "Geeta" in say_mod.nominees(("TELUGU",), backend.voices())


class TestArgumentHandling:
    def test_a_line_starting_with_a_hyphen_is_spoken_not_parsed(self, mac, tmp_path):
        """`say -o out "-20 percent..."` exits 1 with `invalid option -- 2`, which killed
        the render and named neither the scene nor the hyphen."""
        backend = mac.configure()
        line = "-20 percent is what it actually costs."
        clip = backend.synthesize(line, tmp_path / "hyphen.mp3")
        assert media.duration(str(clip)) > 0.2
        assert mac.spoken() == [line]

    def test_the_terminator_is_in_the_command_line(self, mac, tmp_path):
        backend = mac.configure()
        backend.synthesize(ENGLISH, tmp_path / "en.mp3")
        argv = mac.calls[-1]
        assert argv[-2] == "--" and argv[-1] == ENGLISH

    def test_an_option_shaped_line_is_still_narration(self, mac, tmp_path):
        backend = mac.configure()
        line = "--data-format is a flag, not a sentence, but read it anyway."
        assert backend.synthesize(line, tmp_path / "flag.mp3").exists()
        assert mac.spoken() == [line]


class TestEmbeddedCommands:
    @pytest.mark.parametrize("line", [
        "[[slnc 3000]] pause then speak",
        "Revenue grew [[emph +]] fast.",
        "Mind the [[ gap.",
    ])
    def test_a_line_say_would_execute_is_refused(self, mac, tmp_path, line):
        """`[[slnc 3000]]` buys three seconds of dead air and `[[emph +]]` is swallowed,
        both with exit 0. Model-written narration is where these come from, so the refusal
        has to name the marker it found."""
        backend = mac.configure()
        with pytest.raises(say_mod.SayError) as refusal:
            backend.synthesize(line, tmp_path / "bad.mp3")
        assert "[[" in str(refusal.value)
        assert mac.spoken() == [], "the line reached `say` anyway"

    def test_ordinary_brackets_are_left_alone(self, mac, tmp_path):
        backend = mac.configure()
        line = "The paper [1] says costs fell by half."
        assert backend.synthesize(line, tmp_path / "ok.mp3").exists()
        assert mac.spoken() == [line]


class TestAvailability:
    def test_it_is_an_availability_with_a_fix_when_it_says_no(self, monkeypatch):
        monkeypatch.setattr(say_mod, "is_macos", lambda: False)
        ready = say_mod.Say().available()
        assert isinstance(ready, Availability) and not ready.ok
        assert ready.detail and ready.fix

    def test_a_mac_with_no_speech_data_says_where_to_add_it(self, mac):
        backend = mac.configure(voices=[])
        ready = backend.available()
        assert not ready.ok and "System Settings" in ready.fix

    def test_it_does_not_synthesise_anything(self, mac):
        """`synthesize` calls this on every line and `doctor` calls it on a cold machine."""
        backend = mac.configure()
        backend.available()
        assert mac.spoken() == []

    def test_no_devanagari_voice_is_a_yes_that_says_so(self, mac):
        latin_only = [voice for voice in VOICES
                      if voice[1] not in ("hi_IN", "en_IN") and voice[0] != "Kyoko"]
        ready = mac.configure(voices=latin_only).available()
        assert ready.ok and "Devanagari" in ready.detail


class TestNoMacNoCrash:
    def test_with_no_say_on_path_the_listing_is_empty_not_an_error(self, monkeypatch):
        """`doctor` constructs every plugin on every platform, so none of this may raise."""
        monkeypatch.setenv("PATH", "")
        assert say_mod.installed_voices() == []
        assert say_mod.Say().voices() == []
        assert say_mod.nominees(("DEVANAGARI",), []) == []


MAC_ONLY = pytest.mark.skipif(not is_macos() or not shutil.which("say"),
                              reason="needs the real macOS `say`")


@MAC_ONLY
class TestOnThisMac:
    """The measurements this module's decisions rest on, against the real binary."""

    @staticmethod
    def _spoken(tmp_path, voice, line, rate=185):
        out = Path(tmp_path) / "real.aiff"
        argv = say_mod._speech_argv(rate, out, voice, line)
        say_mod._run(argv, "say")
        return media.duration(str(out))

    def _needs(self, voice):
        if voice not in {v.id for v in say_mod.installed_voices()}:
            pytest.skip(f"{voice} is not installed on this Mac")

    @pytest.mark.parametrize("voice", ["Lekha", "Tara (English (India))"])
    def test_these_voices_really_read_devanagari(self, tmp_path, voice):
        self._needs(voice)
        assert self._spoken(tmp_path, voice, HINDI) > 2.5

    @pytest.mark.parametrize("voice", ["Samantha", "Rishi"])
    def test_these_voices_really_do_not(self, tmp_path, voice):
        """Rishi is the reason a locale cannot be trusted: en_IN, and silent."""
        self._needs(voice)
        assert self._spoken(tmp_path, voice, HINDI) < 0.1

    def test_embedded_commands_really_are_executed(self, tmp_path):
        self._needs("Samantha")
        plain = self._spoken(tmp_path, "Samantha", "pause then speak")
        with_command = self._spoken(tmp_path, "Samantha", "[[slnc 3000]] pause then speak")
        assert with_command - plain > 2.5, "no dead air, so the refusal has nothing to stop"
        emphasised = self._spoken(tmp_path, "Samantha", "Revenue grew [[emph +]] fast.")
        assert abs(emphasised - self._spoken(tmp_path, "Samantha", "Revenue grew fast.")) < 0.05

    def test_a_hyphen_line_renders_end_to_end(self, tmp_path):
        """A line starting with `-` must be spoken, not parsed by `say` as an option.

        Measured against a control sentence of similar length rather than an absolute
        duration. This test has no `_needs(voice)` guard because it deliberately uses the
        default voice — and a GitHub macOS runner's default voice produced a 0.005 s clip
        for every input, which failed the build for a reason that had nothing to do with
        hyphens. If the control is silent the runner has no usable voice, and there is
        nothing here to test.
        """
        backend = say_mod.Say()
        control = backend.synthesize("20 percent is what it costs.", tmp_path / "c.mp3")
        baseline = media.duration(str(control))
        if baseline < 0.5:
            pytest.skip(f"the default voice produced {baseline:.3f}s for a plain line — "
                        f"this machine has no voice that speaks it")
        clip = backend.synthesize("-20 percent is what it costs.", tmp_path / "h.mp3")
        assert media.duration(str(clip)) > baseline * 0.5
