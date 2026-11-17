# SPDX-License-Identifier: AGPL-3.0-or-later
"""The local folder publisher: the sidecar is the product, so the sidecar is what is tested.

This backend posts nothing. Its whole output is a copied video and a `.publish.json` beside
it, and a person reads that file and posts by hand — so a field this backend drops is a
field that never gets posted, and there is no platform to check it against afterwards. Both
bugs pinned down here are of that shape: hashtags from the spec replaced by an empty list,
and a sidecar truncated in place by a write that failed half way.

The half-written sidecar is produced rather than imagined: `_sabotage` truncates the file
it is handed and then raises, which is what `open(path, "w")` followed by a failing write
does on a full disk.
"""
import json
import os
import pathlib
import subprocess
import sys
from pathlib import Path

import pytest

from creatoros_autopilot.interfaces.publish import SCHEDULE, PublishRequest
from creatoros_autopilot.providers.publish import local_folder as module
from creatoros_autopilot.providers.publish.local_folder import LocalFolder

CAPTION = "no tags in the text"
TAGS = ["ai", "hindi", "shorts"]


@pytest.fixture(autouse=True)
def no_env_dir(monkeypatch):
    """The env var wins over everything, so a real one on this machine would redirect
    every test's output into the developer's own publish folder."""
    monkeypatch.delenv(module.ENV_DIR, raising=False)


@pytest.fixture
def video(tmp_path) -> Path:
    """A video in a dated job folder, the way the renderer leaves it."""
    out = tmp_path / "jobs" / "2026-11-17" / "01_alpha.mp4"
    out.parent.mkdir(parents=True)
    out.write_bytes(b"\x00" * 2048)
    return out


def _request(video: Path, spec: dict | None = None, **kw) -> PublishRequest:
    return PublishRequest(
        video=str(video), job_id=kw.pop("job_id", "01_alpha"),
        platforms=kw.pop("platforms", ("instagram",)), spec=spec or {}, **kw)


def _sidecar(result_dir: Path) -> dict:
    found = sorted(result_dir.rglob(f"*{module.METADATA_SUFFIX}"))
    assert len(found) == 1, f"expected one sidecar, found {found}"
    return json.loads(found[0].read_text(encoding="utf-8"))


class TestSpecHashtags:
    """Hashtags named in the spec must reach the sidecar.

    They were computed from the `#` matches in the caption and written over the spec's own
    list, and the `setdefault` pass afterwards could not put it back because the key was
    already there. A caption with no `#` in it — which is exactly when a separate list of
    hashtags is worth having — produced `"hashtags": []`.
    """

    def test_spec_hashtags_survive_a_caption_with_no_hashes(self, video, tmp_path):
        spec = {"platforms": {"instagram": {"caption": CAPTION, "hashtags": list(TAGS)}}}
        result = LocalFolder(tmp_path / "out").publish(_request(video, spec))
        assert result.ok
        block = _sidecar(tmp_path / "out")["platforms"]["instagram"]
        assert block["caption"] == CAPTION
        assert block["hashtags"] == TAGS

    def test_a_caption_with_hashes_still_fills_an_empty_list(self, video, tmp_path):
        """Nothing in the spec means the caption is still mined, as before."""
        spec = {"platforms": {"instagram": {"caption": "ship it #ai #hindi"}}}
        LocalFolder(tmp_path / "out").publish(_request(video, spec))
        block = _sidecar(tmp_path / "out")["platforms"]["instagram"]
        assert block["hashtags"] == ["ai", "hindi"]

    def test_hashes_in_the_spec_list_are_not_doubled(self, video, tmp_path):
        spec = {"platforms": {"instagram": {"caption": CAPTION,
                                            "hashtags": ["#ai", "ai", " #hindi "]}}}
        LocalFolder(tmp_path / "out").publish(_request(video, spec))
        assert _sidecar(tmp_path / "out")["platforms"]["instagram"]["hashtags"] == \
            ["ai", "hindi"]

    def test_youtube_tags_given_as_one_string_are_not_split_into_letters(self, video,
                                                                        tmp_path):
        """`list("ai, hindi")` is thirteen one-letter tags. A spec written by hand says
        `tags: ai, hindi` often enough that this reached the sidecar."""
        spec = {"platforms": {"youtube": {"title": "T", "description": "D",
                                          "tags": "ai, hindi"}}}
        LocalFolder(tmp_path / "out").publish(
            _request(video, spec, platforms=("youtube",)))
        assert _sidecar(tmp_path / "out")["platforms"]["youtube"]["tags"] == ["ai", "hindi"]

    def test_an_empty_spec_list_falls_back_to_the_caption(self, video, tmp_path):
        spec = {"platforms": {"instagram": {"caption": "ship it #ai", "hashtags": []}}}
        LocalFolder(tmp_path / "out").publish(_request(video, spec))
        assert _sidecar(tmp_path / "out")["platforms"]["instagram"]["hashtags"] == ["ai"]


def _sabotage(monkeypatch, suffix: str):
    """Make the next `write_text` of a sidecar fail half way.

    Truncate first and then raise, because that is the order `open(path, "w")` does it in:
    the old content is gone before a single byte of the new content is written. Matched on
    a substring rather than the end of the path so that it still bites whether the module
    writes the sidecar directly or writes `<name>.publish.json.part` first.
    """
    real = pathlib.Path.write_text

    def half(self, data, *args, **kw):
        if suffix in str(self):
            with open(self, "w", encoding="utf-8") as fh:
                fh.write(data[:len(data) // 2])
            raise OSError(28, "No space left on device")
        return real(self, data, *args, **kw)

    monkeypatch.setattr(pathlib.Path, "write_text", half)


class TestSidecarIsWrittenAtomically:
    """A sidecar is rewritten on every re-run, so a failed rewrite must not take the
    previous good one with it. It is the only record of what to post; a truncated one is
    worse than a stale one, because `json.load` on it fails and the caption is gone."""

    def test_a_failed_rewrite_leaves_the_previous_sidecar_intact(self, video, tmp_path,
                                                                monkeypatch):
        backend = LocalFolder(tmp_path / "out")
        spec = {"platforms": {"instagram": {"caption": CAPTION, "hashtags": list(TAGS)}}}
        assert backend.publish(_request(video, spec)).ok
        good = _sidecar(tmp_path / "out")

        _sabotage(monkeypatch, module.METADATA_SUFFIX)
        edited = {"platforms": {"instagram": {"caption": "a much longer caption, edited "
                                                         "since the last run, with #ai"}}}
        result = backend.publish(_request(video, edited))

        assert not result.ok, "a sidecar that was not written must not report success"
        assert "No space left" in result.error
        assert _sidecar(tmp_path / "out") == good

    def test_no_part_file_is_left_behind(self, video, tmp_path, monkeypatch):
        backend = LocalFolder(tmp_path / "out")
        _sabotage(monkeypatch, module.METADATA_SUFFIX)
        backend.publish(_request(video, {"platforms": {"instagram": {"caption": CAPTION}}}))
        leftovers = [p for p in (tmp_path / "out").rglob("*") if p.name.endswith(".part")]
        assert not leftovers, f"interrupted write left {leftovers}"

    def test_the_sidecar_is_rewritten_when_the_caption_changes(self, video, tmp_path):
        """Atomicity must not become "write it once": the video is unchanged on a re-run
        but an edited caption is the reason to run again."""
        backend = LocalFolder(tmp_path / "out")
        backend.publish(_request(video, {"platforms": {"instagram": {"caption": "first"}}}))
        backend.publish(_request(video, {"platforms": {"instagram": {"caption": "second"}}}))
        assert _sidecar(tmp_path / "out")["platforms"]["instagram"]["caption"] == "second"


class TestStillHonestAboutWhatItDid:
    def test_it_does_not_claim_to_publish(self):
        assert LocalFolder.records_publication is False

    def test_available_says_where_it_will_write(self, tmp_path):
        answer = LocalFolder(tmp_path / "out").available()
        assert answer.ok and str(tmp_path / "out") in answer.detail

    def test_a_missing_video_fails_every_platform(self, tmp_path):
        result = LocalFolder(tmp_path / "out").publish(
            _request(tmp_path / "gone.mp4", platforms=("instagram", "youtube")))
        assert not result.ok
        assert [o.ok for o in result.outcomes] == [False, False]

    def test_a_dry_run_writes_nothing(self, video, tmp_path, capsys):
        LocalFolder(tmp_path / "out").publish(
            _request(video, {"platforms": {"instagram": {"caption": CAPTION}}},
                     dry_run=True))
        assert not (tmp_path / "out").exists()
        assert "would write" in capsys.readouterr().out

    def test_the_scheduled_slot_is_written_down(self, video, tmp_path):
        LocalFolder(tmp_path / "out").publish(
            _request(video, mode=SCHEDULE, at="2026-11-18T09:30:00", tz="Asia/Kolkata"))
        payload = _sidecar(tmp_path / "out")
        assert payload["scheduled_for"] == "2026-11-18 09:30 Asia/Kolkata"
        assert payload["mode"] == SCHEDULE


def test_importing_needs_no_service(repo):
    """No key, no binary, no network at import time."""
    env = {k: v for k, v in os.environ.items() if k != module.ENV_DIR}
    done = subprocess.run(
        [sys.executable, "-I", "-c",
         "import creatoros_autopilot.providers.publish.local_folder as m;"
         "print(m.LocalFolder().available().ok)"],
        cwd=repo, env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "True"
