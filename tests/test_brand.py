# SPDX-License-Identifier: AGPL-3.0-or-later
"""Brand kits, and the promise that a missing asset never costs you a day's run."""
import shutil

import pytest

from creatoros_autopilot.core import brand as brand_mod

from .conftest import REPO

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


class TestLoading:
    def test_the_default_is_a_default_not_an_identity(self):
        kit = brand_mod.load()
        assert kit.name == "CreatorOS"
        assert kit.logo is None
        assert kit.cta_label == "Follow CreatorOS"

    def test_the_shipped_kits_load(self):
        assert "nexlore-ai" in brand_mod.available()
        kit = brand_mod.load("nexlore-ai")
        assert kit.name == "Nexlore AI" and kit.logo and kit.logo.is_file()

    @pytest.mark.parametrize("preset", ["ember-fitness", "claro-es"])
    def test_every_shipped_preset_carries_a_working_brand(self, preset):
        """A preset folder is self-contained: config, brand and pillars travel together."""
        kit = brand_mod.load(str(REPO / "examples" / preset / "brand.yaml"))
        assert kit.name and kit.palette.accent

    def test_an_unknown_kit_lists_what_exists(self):
        with pytest.raises(brand_mod.BrandError) as exc:
            brand_mod.load("nope")
        assert "Available:" in str(exc.value)

    def test_a_kit_with_only_a_name_is_valid(self, tmp_path):
        (tmp_path / "tiny.yaml").write_text("name: Tiny\n")
        kit = brand_mod.load(str(tmp_path / "tiny.yaml"))
        assert kit.name == "Tiny" and kit.palette.accent == brand_mod.Palette.build().accent

    def test_a_typo_in_a_colour_name_is_an_error(self, tmp_path):
        (tmp_path / "k.yaml").write_text("name: K\npalette: {acent: '#FF0000'}\n")
        with pytest.raises(brand_mod.BrandError, match="acent"):
            brand_mod.load(str(tmp_path / "k.yaml"))

    def test_a_malformed_colour_is_an_error(self, tmp_path):
        (tmp_path / "k.yaml").write_text("name: K\npalette: {accent: 'red'}\n")
        with pytest.raises(brand_mod.BrandError, match="colour"):
            brand_mod.load(str(tmp_path / "k.yaml"))

    def test_an_unknown_top_level_key_is_an_error(self, tmp_path):
        (tmp_path / "k.yaml").write_text("name: K\ncolours: {}\n")
        with pytest.raises(brand_mod.BrandError, match="colours"):
            brand_mod.load(str(tmp_path / "k.yaml"))

    def test_short_hex_is_accepted(self):
        assert brand_mod.parse_colour("#f00") == (255, 0, 0)


class TestDegradesRatherThanFails:
    """A missing asset is a plainer video, never an exception."""

    def test_a_missing_logo_falls_back_to_the_wordmark(self, tmp_path, capsys):
        (tmp_path / "k.yaml").write_text("name: K\nlogo: gone.png\n")
        kit = brand_mod.load(str(tmp_path / "k.yaml"))
        assert kit.logo is None
        assert "wordmark" in capsys.readouterr().out

    @needs_ffmpeg
    def test_a_missing_logo_still_renders(self, tmp_path):
        from creatoros_autopilot.render import engine

        (tmp_path / "k.yaml").write_text("name: No Logo Here\nlogo: gone.png\n")
        kit = brand_mod.load(str(tmp_path / "k.yaml"))
        out = engine.run("preview", REPO / "examples" / "specs" / "kv-cache.json",
                         str(tmp_path / "p.jpg"), kit=kit)
        assert (tmp_path / "p.jpg").stat().st_size > 10_000

    @needs_ffmpeg
    def test_a_missing_font_falls_back_to_the_bundled_one(self, tmp_path, capsys):
        from creatoros_autopilot.render import engine

        (tmp_path / "k.yaml").write_text("name: K\nfonts: {black: NotHere.otf}\n")
        kit = brand_mod.load(str(tmp_path / "k.yaml"))
        engine.run("preview", REPO / "examples" / "specs" / "kv-cache.json",
                   str(tmp_path / "p.jpg"), kit=kit)
        assert "falling back" in capsys.readouterr().out
        assert (tmp_path / "p.jpg").is_file()


@needs_ffmpeg
class TestRendering:
    """The refactor had to change nothing for the channel that already exists."""

    def _preview(self, kit, tmp_path, name):
        from creatoros_autopilot.render import engine
        return engine.run("preview", REPO / "examples" / "specs" / "kv-cache.json",
                          str(tmp_path / f"{name}.png"), kit=kit)

    def test_two_brands_produce_different_pixels(self, tmp_path):
        import numpy as np
        from PIL import Image

        a = self._preview(brand_mod.load("nexlore-ai"), tmp_path, "a")
        b = self._preview(
            brand_mod.load(str(REPO / "examples" / "ember-fitness" / "brand.yaml")),
            tmp_path, "b")
        pixels = [np.asarray(Image.open(p).convert("RGB"), dtype=np.int16) for p in (a, b)]
        assert (pixels[0] != pixels[1]).any(), "a different brand rendered identically"

    def test_rendering_the_same_brand_twice_is_deterministic(self, tmp_path):
        import numpy as np
        from PIL import Image

        kit = brand_mod.load("nexlore-ai")
        a = self._preview(kit, tmp_path, "one")
        b = self._preview(kit, tmp_path, "two")
        pixels = [np.asarray(Image.open(p).convert("RGB"), dtype=np.int16) for p in (a, b)]
        assert (pixels[0] == pixels[1]).all()

    def test_switching_brand_does_not_leak_the_previous_palette(self, tmp_path):
        """The background, grid and gradient bar are cached and built from the palette.
        Keeping them across a brand change would draw the previous channel's colours
        under the new one's text."""
        import numpy as np
        from PIL import Image

        ember = REPO / "examples" / "ember-fitness" / "brand.yaml"
        first = self._preview(brand_mod.load(str(ember)), tmp_path, "e1")
        self._preview(brand_mod.load("nexlore-ai"), tmp_path, "n1")
        second = self._preview(brand_mod.load(str(ember)), tmp_path, "e2")
        a, b = (np.asarray(Image.open(p).convert("RGB"), dtype=np.int16)
                for p in (first, second))
        assert (a == b).all(), "the cached background survived a brand change"


class TestPresets:
    """Three presets, because one leaves the abstractions untested.

    The point of each: Nexlore is a real channel with a logo, a mixed language and a
    third-party publisher. Ember is English-only, free providers, no logo. Claro is neither
    English nor mixed, which is the case that proves the language gate is genuinely
    optional rather than something every channel has to satisfy.
    """

    PRESETS = ["nexlore-ai", "ember-fitness", "claro-es"]

    @pytest.mark.parametrize("preset", PRESETS)
    def test_the_preset_loads_and_resolves_its_own_files(self, preset):
        from creatoros_autopilot.core import settings as settings_mod

        settings = settings_mod.load(
            REPO / "examples" / preset / "creatoros.yaml", use_env=False)
        kit = brand_mod.load(settings.brand_path)
        assert kit.name
        assert settings.pillars(), f"{preset} resolved no pillars"

    @pytest.mark.parametrize("preset", PRESETS)
    def test_relative_paths_do_not_depend_on_the_working_directory(self, preset, tmp_path,
                                                                   monkeypatch):
        """A scheduled run does not start where you do. `pillars_dir: pillars` must mean
        the folder beside the config, not beside whoever launched the process."""
        from creatoros_autopilot.core import settings as settings_mod

        monkeypatch.chdir(tmp_path)
        settings = settings_mod.load(
            REPO / "examples" / preset / "creatoros.yaml", use_env=False)
        assert settings.pillars()
        assert brand_mod.load(settings.brand_path).name

    @pytest.mark.parametrize("preset", ["ember-fitness", "claro-es"])
    def test_every_preset_but_the_live_one_publishes_nothing_by_default(self, preset):
        from creatoros_autopilot.core import settings as settings_mod

        settings = settings_mod.load(
            REPO / "examples" / preset / "creatoros.yaml", use_env=False)
        assert settings.publish.mode == "draft"

    def test_a_single_language_preset_has_no_mix_to_gate(self):
        """The language gate skips itself rather than failing a correct configuration."""
        from creatoros_autopilot.core import gates, settings as settings_mod

        settings = settings_mod.load(
            REPO / "examples" / "claro-es" / "creatoros.yaml", use_env=False)
        kit = brand_mod.load(settings.brand_path)
        report = gates.run({"scenes": [{"voice": "una idea al día"}]}, ["langmix"], kit)
        assert report.results == [] and report.ok

    def test_the_mixed_language_preset_measures_its_narration(self):
        from creatoros_autopilot.core import gates, settings as settings_mod

        settings = settings_mod.load(
            REPO / "examples" / "nexlore-ai" / "creatoros.yaml", use_env=False)
        kit = brand_mod.load(settings.brand_path)
        report = gates.run(
            {"scenes": [{"voice": "मॉडल का लॉस slow हो जाता है तो step बढ़ाओ"}]},
            settings.gates, kit)
        assert len(report.results) == 1
        assert report.results[0].score is not None
