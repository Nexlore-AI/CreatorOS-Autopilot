# SPDX-License-Identifier: AGPL-3.0-or-later
"""The acceptance test for Phase 1: a fresh clone renders with no API keys."""
import shutil
from pathlib import Path

import pytest

from creatoros_autopilot.core import media
from creatoros_autopilot.render import engine

FIXTURE = Path(__file__).parent / "fixtures" / "mini.json"
EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "specs"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                  reason="ffmpeg and ffprobe are not installed")


@pytest.fixture
def spec(tmp_path):
    """A copy, because rendering writes siblings next to the spec."""
    target = tmp_path / "01_mini.json"
    target.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    return target


class TestPackageData:
    def test_fonts_ship_with_the_package(self):
        """They must resolve from `__file__`, not from the repo root, or a pip install
        renders nothing."""
        for name in ("InterDisplay-Black.otf", "Inter-Bold.otf", "Inter-Regular.otf",
                     "Inter-SemiBold.otf", "DejaVuSansMono-Bold.ttf"):
            assert (Path(engine.FONTS) / name).is_file(), name

    def test_the_engine_ships_no_brand_asset(self):
        """The renderer used to open `nexlore-icon-512.png` unconditionally at startup,
        so any brand without that exact file crashed before the first frame. A logo now
        belongs to a brand kit, and having none is a supported answer."""
        assert not Path(engine.ASSETS).is_dir() or not list(Path(engine.ASSETS).glob("*.png"))

    def test_a_brand_kit_carries_its_own_logo(self):
        from creatoros_autopilot.core import brand as brand_mod

        kit = brand_mod.load("nexlore-ai")
        assert kit.logo and kit.logo.is_file()

    def test_the_music_library_does_not(self):
        """Music is user content from the workspace. Bundling it would mean shipping
        audio whose licence we cannot vouch for."""
        assert not list(Path(engine.FONTS).parent.rglob("*.mp3"))


class TestKeylessRender:
    @needs_ffmpeg
    def test_preview_needs_no_keys(self, spec, monkeypatch):
        for name in ("ELEVENLABS_API_KEY", "ZERNIO_API_KEY", "OPENAI_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        out = engine.run("preview", spec)
        assert Path(out).is_file() and Path(out).stat().st_size > 10_000

    @needs_ffmpeg
    def test_render_produces_a_playable_vertical_mp4(self, spec, monkeypatch):
        for name in ("ELEVENLABS_API_KEY", "ZERNIO_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        out = Path(engine.run("render", spec))
        assert out.is_file() and out.stat().st_size > 50_000
        assert media.duration(str(out)) > 1.0

    @needs_ffmpeg
    def test_a_cover_frame_is_written(self, spec):
        engine.run("render", spec)
        assert (spec.parent / "01_mini.cover.jpg").is_file()

    @needs_ffmpeg
    def test_scene_intermediates_are_cleaned_up(self, spec):
        engine.run("render", spec)
        assert not (spec.parent / "01_mini_scenes").exists()

    @needs_ffmpeg
    def test_output_path_can_be_chosen(self, spec, tmp_path):
        target = tmp_path / "elsewhere" / "video.mp4"
        target.parent.mkdir()
        assert Path(engine.run("render", spec, str(target))) == target
        assert target.is_file()


class TestShippedExamples:
    @pytest.mark.parametrize("name", ["kv-cache.json", "transformer-from-scratch.json"])
    def test_the_examples_are_valid_specs(self, name):
        from creatoros_autopilot.core import spec as sp

        assert sp.validate_render(sp.load(EXAMPLES / name)) == []

    @needs_ffmpeg
    @pytest.mark.parametrize("name", ["kv-cache.json", "transformer-from-scratch.json"])
    def test_the_examples_preview_without_keys(self, name, tmp_path):
        out = engine.run("preview", EXAMPLES / name, str(tmp_path / f"{name}.jpg"))
        assert Path(out).stat().st_size > 10_000


class TestFailures:
    def test_a_missing_ffmpeg_says_how_to_install_it(self, monkeypatch):
        monkeypatch.setattr(media.shutil, "which", lambda _: None)
        with pytest.raises(media.FFmpegMissing) as exc:
            media.require("ffmpeg")
        assert "install" in str(exc.value).lower()

    def test_an_invalid_spec_is_refused_before_any_frame_is_drawn(self, tmp_path):
        from creatoros_autopilot.core import spec as sp

        bad = tmp_path / "bad.json"
        bad.write_text('{"scenes": [{"type": "trampoline"}]}')
        with pytest.raises(sp.SpecError):
            engine.run("render", bad)


class TestReadOnlySpec:
    """A spec may be read-only — in a container, in site-packages, in a shared folder.

    Everything written must hang off the output path the user named, never off the input.
    Docker found this: `render /app/examples/specs/x.json -o /workspace/out.mp4` died
    trying to create `/app/examples/specs/x_scenes/`.
    """

    @needs_ffmpeg
    def test_nothing_is_written_beside_a_read_only_spec(self, tmp_path):
        spec_dir = tmp_path / "readonly"
        spec_dir.mkdir()
        spec = spec_dir / "01_mini.json"
        spec.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        before = {p.name for p in spec_dir.iterdir()}

        out_dir = tmp_path / "out"
        engine.run("render", spec, str(out_dir / "video.mp4"))

        assert {p.name for p in spec_dir.iterdir()} == before, \
            "the spec's folder must be untouched when -o is given"
        assert (out_dir / "video.mp4").is_file()
        assert (out_dir / "video.cover.jpg").is_file()

    @needs_ffmpeg
    def test_the_output_directory_is_created(self, spec, tmp_path):
        target = tmp_path / "deep" / "nested" / "video.mp4"
        engine.run("render", spec, str(target))
        assert target.is_file()

    @needs_ffmpeg
    def test_preview_also_respects_the_output_path(self, tmp_path):
        spec_dir = tmp_path / "ro"
        spec_dir.mkdir()
        spec = spec_dir / "01_mini.json"
        spec.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        before = {p.name for p in spec_dir.iterdir()}
        engine.run("preview", spec, str(tmp_path / "sheet" / "p.jpg"))
        assert {p.name for p in spec_dir.iterdir()} == before
        assert (tmp_path / "sheet" / "p.jpg").is_file()
