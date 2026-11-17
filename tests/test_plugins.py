# SPDX-License-Identifier: AGPL-3.0-or-later
"""The plugin system, and the Phase 2 acceptance criterion.

The criterion is: *a new TTS provider can be added in one file plus one entry point,
without touching core/*. `TestAcceptance` proves it by actually doing it — writing a
provider, registering it, narrating a spec through it, and then asserting that core and the
CLI contain no reference to it.
"""
import ast
from pathlib import Path

import pytest

from creatoros_autopilot import plugins
from creatoros_autopilot.core import narrate
from creatoros_autopilot.interfaces import (
    Availability, Gate, LLM, Publisher, PublishRequest, ResearchSource, Style, TTS,
)

from .conftest import PACKAGE, code_only, tone_bytes


class TestRegistry:
    def test_every_kind_has_a_group(self):
        assert set(plugins.GROUPS) == {"tts", "llm", "publish", "research", "style", "gate"}

    @pytest.mark.parametrize("kind", sorted(plugins.GROUPS))
    def test_the_shipped_providers_are_discoverable(self, kind):
        """They register through the public entry points, like any third-party plugin."""
        assert plugins.names(kind), f"nothing registered for {kind}"

    def test_an_unknown_name_lists_what_is_installed(self):
        with pytest.raises(plugins.PluginError) as exc:
            plugins.load("tts", "pipr")
        message = str(exc.value)
        assert "pipr" in message and "Installed:" in message and "entry point" in message

    def test_an_unknown_kind_is_rejected(self):
        with pytest.raises(plugins.PluginError):
            plugins.load("transcription", "whisper")

    def test_registering_adds_without_an_install(self):
        class Dummy:
            name = "dummy"
            paid = False

            def available(self):
                return Availability.yes("dummy")

            def synthesize(self, text, out_path, *, voice="", model="", **o):
                Path(out_path).write_bytes(b"\0" * 32)
                return Path(out_path)

            def voices(self):
                return []

        plugins.register("tts", "dummy", Dummy)
        try:
            assert "dummy" in plugins.names("tts")
            assert plugins.load("tts", "dummy").name == "dummy"
        finally:
            plugins.unregister("tts", "dummy")
        assert "dummy" not in plugins.names("tts")


PROTOCOLS = {"tts": TTS, "llm": LLM, "publish": Publisher, "research": ResearchSource,
             "style": Style, "gate": Gate}


class TestShippedProviders:
    @pytest.mark.parametrize("kind,name", [(k, n) for k in sorted(plugins.GROUPS)
                                           for n in plugins.names(k)])
    def test_it_loads_and_satisfies_its_protocol(self, kind, name):
        backend = plugins.load(kind, name)
        assert isinstance(backend, PROTOCOLS[kind]), f"{kind}/{name} is not a {kind}"
        assert getattr(backend, "name", None), f"{kind}/{name} has no name attribute"

    @pytest.mark.parametrize("kind,name", [(k, n) for k in ("tts", "llm", "publish", "research")
                                           for n in plugins.names(k)])
    def test_available_returns_an_availability_with_a_fix(self, kind, name):
        """Never a bool. When it says no, it says what to do — that is half the
        first-run experience of a tool like this."""
        ready = plugins.load(kind, name).available()
        assert isinstance(ready, Availability), f"{kind}/{name}.available() returned {ready!r}"
        if not ready.ok:
            assert ready.detail, f"{kind}/{name} says no with no reason"
            assert ready.fix, f"{kind}/{name} says no with no fix"

    @pytest.mark.parametrize("name", plugins.names("tts"))
    def test_a_paid_backend_declares_itself(self, name):
        backend = plugins.load("tts", name)
        assert isinstance(backend.paid, bool), f"{name} does not declare `paid`"

    def test_there_is_at_least_one_free_local_tts(self):
        """The zero-cost path is the feature that decides whether anyone tries this."""
        free = [n for n in plugins.names("tts") if not plugins.load("tts", n).paid]
        assert free, "no free TTS backend is installed"

    def test_there_is_a_publisher_that_needs_no_third_party_account(self):
        local = [n for n in plugins.names("publish")
                 if not plugins.load("publish", n).third_party]
        assert local, "every publisher needs a third-party account"

    def test_the_first_run_publisher_is_always_available(self):
        """local_folder is what makes "install it and see it work" true with no account."""
        assert plugins.load("publish", "local_folder").available().ok


class TestRouting:
    def test_a_platform_resolves_to_a_backend_that_reaches_it(self):
        backend = plugins.publisher_for("youtube")
        assert "youtube" in backend.platforms

    def test_config_order_wins(self):
        assert plugins.publisher_for("youtube", ["zernio"]).name == "zernio"
        assert plugins.publisher_for("youtube", ["youtube"]).name == "youtube"

    def test_a_direct_backend_is_preferred_over_a_third_party_one(self):
        """A new user should not be routed through a paid relay by default."""
        assert not plugins.publisher_for("youtube").third_party

    def test_an_unreachable_platform_says_so(self, monkeypatch):
        """With `local_folder` installed every platform is reachable, so this is the
        no-publishers-at-all case — which is what a stripped install looks like."""
        monkeypatch.setattr(plugins, "names", lambda kind: [])
        with pytest.raises(plugins.PluginError, match="tiktok"):
            plugins.publisher_for("tiktok")


class TestAcceptance:
    """A new TTS provider: one file, one entry point, no core changes."""

    def test_a_third_party_tts_works_end_to_end(self, tmp_path):
        # 1. One file. Nothing imported from creatoros_autopilot except the Availability
        #    type it reports with — a plugin does not subclass or register with anything.
        class ShoutTTS:
            name = "shout"
            paid = False

            def available(self):
                return Availability.yes("always")

            def synthesize(self, text, out_path, *, voice="", model="", **options):
                # Real audio, long enough to be plausible for the text. A fake that wrote
                # a few bytes would pass a size check and fail the duration check that
                # exists because backends do exactly that when they cannot speak a script.
                Path(out_path).write_bytes(tone_bytes(seconds=max(0.5, len(text) * 0.05)))
                return Path(out_path)

            def voices(self):
                return []

        # 2. One entry point. `register` is the in-process equivalent.
        plugins.register("tts", "shout", ShoutTTS)
        try:
            backend = plugins.load("tts", "shout")
            assert isinstance(backend, TTS)

            spec = {"id": "t", "scenes": [{"type": "hook", "voice": "hello"},
                                          {"type": "cta", "voice": "bye"}]}
            spec_path = tmp_path / "01_t.json"
            spec_path.write_text("{}")

            # 3. The shared machinery works for it with no changes: caching, the
            #    manifest, stale-clip cleanup.
            first = narrate.narrate(spec, spec_path, backend)
            assert first.ok, first.problems
            assert first.written == [1, 2]
            assert (tmp_path / "01_t_vo" / "01.mp3").stat().st_size > 0

            second = narrate.narrate(spec, spec_path, backend)
            assert second.written == [] and second.unchanged == [1, 2], \
                "an unchanged line must not be re-synthesised"

            spec["scenes"] = spec["scenes"][:1]
            third = narrate.narrate(spec, spec_path, backend)
            assert not (tmp_path / "01_t_vo" / "02.mp3").exists(), \
                "a removed scene's clip must be cleaned up"
        finally:
            plugins.unregister("tts", "shout")

    def test_switching_backend_re_voices_everything(self, tmp_path):
        """The backend is part of the cache key. Comparing only the text would leave a
        video half in each voice, with nothing anywhere to indicate it."""
        class A:
            name, paid = "a", False
            def available(self): return Availability.yes()
            def synthesize(self, text, out_path, **k):
                Path(out_path).write_bytes(tone_bytes(1.0)); return Path(out_path)
            def voices(self): return []

        class B(A):
            name = "b"

        spec = {"scenes": [{"type": "hook", "voice": "same line"}]}
        spec_path = tmp_path / "01_x.json"
        spec_path.write_text("{}")
        assert narrate.narrate(spec, spec_path, A()).written == [1]
        assert narrate.narrate(spec, spec_path, B()).written == [1]

    def test_core_names_no_provider(self):
        """The structural half of the criterion.

        `core/` may not mention any provider by name — not in an import, not in a
        comparison. If it did, adding a provider would mean editing core, which is the
        thing this phase exists to stop.
        """
        provider_names = {n for kind in plugins.GROUPS for n in plugins.names(kind)}
        provider_names -= {"local"}
        offenders = []
        for path in sorted((PACKAGE / "core").rglob("*.py")):
            code = code_only(path)
            for node in ast.walk(ast.parse(code)):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    text = ast.unparse(node)
                    for name in provider_names:
                        if name in text:
                            offenders.append(f"{path.name}: imports {name}")
                if isinstance(node, ast.Compare):
                    for operand in [node.left, *node.comparators]:
                        items = ([operand] if isinstance(operand, ast.Constant)
                                 else list(getattr(operand, "elts", [])))
                        for item in items:
                            if isinstance(item, ast.Constant) and item.value in provider_names:
                                offenders.append(f"{path.name}: compares against {item.value!r}")
        assert not offenders, "core/ knows about specific providers:\n  " + "\n  ".join(offenders)


class TestWildcardRouting:
    """`local_folder` reaches any platform, but must not quietly take over.

    A config that named `local_folder` in `publish.backends` was routed to the live
    YouTube API instead, because the backend only advertised "local". The user asking for
    a dry practice run got a real upload attempt.
    """

    def test_naming_a_wildcard_backend_wins(self):
        for platform in ("youtube", "instagram", "tiktok"):
            assert plugins.publisher_for(platform, ["local_folder"]).name == "local_folder"

    def test_a_wildcard_does_not_win_by_default(self):
        assert plugins.publisher_for("youtube").name != "local_folder"
        assert plugins.publisher_for("instagram").name != "local_folder"

    def test_a_wildcard_does_not_rescue_an_unknown_platform(self):
        """The safety net a typo needs.

        `platforms: [instgram]` must still fail loudly. Letting the wildcard catch it
        would write a folder and record the post as published for a platform that does
        not exist.
        """
        with pytest.raises(plugins.PluginError, match="instgram"):
            plugins.publisher_for("instgram")
        assert plugins.publisher_for("instgram", ["local_folder"]).name == "local_folder"

    def test_an_explicit_real_backend_still_wins(self):
        assert plugins.publisher_for("youtube", ["zernio"]).name == "zernio"


class TestPracticeRuns:
    """A practice run must not be able to consume a real one."""

    def test_local_folder_does_not_claim_to_publish(self):
        assert plugins.load("publish", "local_folder").records_publication is False

    def test_every_real_publisher_does(self):
        for name in plugins.names("publish"):
            backend = plugins.load("publish", name)
            if name == "local_folder":
                continue
            assert backend.records_publication is True, f"{name} does not record publication"
