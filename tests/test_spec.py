# SPDX-License-Identifier: AGPL-3.0-or-later
"""Spec validation, split so that rendering never needs a publishing account."""

import pytest

from creatoros_autopilot.core import spec as sp

MINIMAL = {"id": "t", "scenes": [{"type": "hook", "voice": "line", "lines": ["a"]}]}


class TestRenderValidation:
    def test_a_minimal_spec_renders(self):
        assert sp.validate_render(MINIMAL) == []

    def test_render_does_not_need_platform_metadata(self):
        """This is the keyless first-run path: render an example with no accounts. The old
        single validate() demanded a YouTube title before it would draw a frame."""
        assert sp.validate_render({"scenes": [{"type": "cta", "voice": "x"}]}) == []

    def test_a_missing_voice_line_is_a_warning_not_an_error(self):
        warnings = sp.validate_render({"scenes": [{"type": "hook"}]})
        assert len(warnings) == 1 and "minimum duration" in warnings[0]

    def test_unknown_scene_type_is_an_error(self):
        with pytest.raises(sp.SpecError) as exc:
            sp.validate_render({"scenes": [{"type": "trampoline", "voice": "x"}]})
        assert "trampoline" in str(exc.value)

    def test_unknown_visual_type_is_an_error(self):
        with pytest.raises(sp.SpecError):
            sp.validate_render({"scenes": [{"type": "section", "voice": "x",
                                            "visual": {"type": "hologram"}}]})

    def test_every_problem_is_reported_at_once(self):
        with pytest.raises(sp.SpecError) as exc:
            sp.validate_render({"scenes": [{"type": "nope", "voice": "x"},
                                           {"type": "also-nope", "voice": "x"}]})
        assert len(exc.value.problems) == 2

    def test_no_scenes_is_an_error(self):
        for bad in ({}, {"scenes": []}, {"scenes": "lots"}):
            with pytest.raises(sp.SpecError):
                sp.validate_render(bad)


def _requires(platform):
    """Stands in for a publisher's requires(). Core holds no such table any more."""
    return {"youtube": ("title", "description", "tags"),
            "instagram": ("caption",)}.get(platform, ())


class TestPublishValidation:
    def test_youtube_needs_title_description_tags(self):
        with pytest.raises(sp.SpecError) as exc:
            sp.validate_publish(MINIMAL, ["youtube"], requires=_requires)
        assert len(exc.value.problems) == 3

    def test_instagram_needs_a_caption(self):
        """An Instagram Reel with no caption gets no reach and cannot be found."""
        spec = dict(MINIMAL, platforms={"instagram": {"caption": "  "}})
        with pytest.raises(sp.SpecError, match="caption"):
            sp.validate_publish(spec, ["instagram"], requires=_requires)

    def test_requirements_come_from_the_publisher_not_from_core(self):
        """With no publisher to ask, core checks only what it owns: the ledger identity."""
        assert sp.validate_publish(MINIMAL, ["youtube"]) == []

    def test_a_new_platform_needs_no_core_change(self):
        spec = dict(MINIMAL, platforms={"tiktok": {}})
        with pytest.raises(sp.SpecError, match="tiktok.caption"):
            sp.validate_publish(spec, ["tiktok"], requires=lambda p: ("caption",))

    def test_only_the_requested_platforms_are_checked(self):
        spec = dict(MINIMAL, platforms={"instagram": {"caption": "hello"}})
        assert sp.validate_publish(spec, ["instagram"], requires=_requires) == []

    def test_publishing_needs_an_id_for_the_ledger(self):
        spec = {"scenes": MINIMAL["scenes"], "platforms": {"instagram": {"caption": "x"}}}
        with pytest.raises(sp.SpecError, match="id"):
            sp.validate_publish(spec, ["instagram"], requires=_requires)


class TestHelpers:
    def test_job_id_prefers_the_spec_id(self, tmp_path):
        assert sp.job_id({"id": "real"}, tmp_path / "other.json") == "real"

    def test_job_id_falls_back_to_the_filename(self, tmp_path):
        assert sp.job_id({}, tmp_path / "01_kv-cache.json") == "01_kv-cache"

    def test_slot_is_read_from_the_filename_prefix(self):
        assert sp.slot_of("01_kv-cache.mp4") == 1
        assert sp.slot_of("04_enterprise.json") == 4
        assert sp.slot_of("kv-cache.mp4") is None

    def test_vo_dir_sits_beside_the_spec(self, tmp_path):
        assert sp.vo_dir(tmp_path / "01_a.json") == tmp_path / "01_a_vo"

    def test_bad_json_names_the_file(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json")
        with pytest.raises(sp.SpecError, match="broken.json"):
            sp.load(path)

    def test_round_trip_keeps_devanagari(self, tmp_path):
        """The narration is Devanagari; ensure_ascii would mangle it into escapes."""
        path = tmp_path / "s.json"
        sp.save({"id": "x", "scenes": [{"type": "hook", "voice": "मॉडल सीखता है"}]}, path)
        assert "मॉडल" in path.read_text(encoding="utf-8")
        assert sp.load(path)["scenes"][0]["voice"] == "मॉडल सीखता है"
