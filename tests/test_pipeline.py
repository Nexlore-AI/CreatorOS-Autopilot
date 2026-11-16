# SPDX-License-Identifier: AGPL-3.0-or-later
"""The day's run: which videos publish where, and what the ledger learns.

The publisher is a fake, so these assert the orchestration the shell used to hold — slot
decisions, idempotency, partial failure — without a network call.
"""
import datetime as dt
import json

import pytest

from creatoros_autopilot.core import pipeline
from creatoros_autopilot.core.ledger import Ledger
from creatoros_autopilot.core.settings import Settings


class Result:
    """A publisher answer, with per-platform detail like Zernio's."""

    def __init__(self, ok=True, platforms=()):
        self.ok, self.platforms, self.post_id, self.dry_run = ok, list(platforms), "p1", False

    def url_for(self, platform):
        return f"https://example/{platform}"


class FakePublisher:
    """Stands in for `providers.publish.zernio`.

    `raises` is a transport failure — the whole call died, nothing was created.
    `platform_errors` is the subtler case this pipeline has to get right: the post was
    created but one platform inside it failed, which is what Zernio's HTTP 207 means.
    """

    def __init__(self, raises=(), platform_errors=()):
        self.calls = []
        self.raises = set(raises)
        self.platform_errors = set(platform_errors)

    def publish(self, video, platforms, mode, at=None, tz="UTC", **kw):
        self.calls.append({"video": video, "platforms": list(platforms), "mode": mode, "at": at})
        if self.raises & set(platforms):
            raise RuntimeError("the upload connection died")
        return Result(ok=True, platforms=[
            {"platform": p, "status": "failed" if p in self.platform_errors else "published",
             **({"error": "the platform rejected it"} if p in self.platform_errors else {})}
            for p in platforms])


@pytest.fixture
def day(tmp_path):
    """A workspace with two rendered videos for 2026-11-17."""
    settings = Settings(workspace=str(tmp_path), timezone="Asia/Kolkata")
    settings.publish.platforms = ["instagram", "youtube"]
    settings.publish.mode = "auto"
    settings.publish.post_times = ["09:00", "13:00"]
    settings.publish.offset_days = 1
    settings.ensure_dirs()
    job_dir = settings.job_dir("2026-11-17")
    job_dir.mkdir(parents=True, exist_ok=True)
    for slot, slug in ((1, "alpha"), (2, "beta")):
        (job_dir / f"{slot:02d}_{slug}.mp4").write_bytes(b"\x00" * 1024)
        (job_dir / f"{slot:02d}_{slug}.json").write_text(json.dumps({
            "id": f"2026-11-17_{slot:02d}_{slug}",
            "scenes": [{"type": "hook", "voice": "x"}],
            "platforms": {"youtube": {"title": f"Video {slot}"},
                          "instagram": {"caption": "c"}}}))
    (job_dir / "02_beta_scenes").mkdir()
    (job_dir / "02_beta_scenes" / "01.mp4").write_bytes(b"\x00")
    return settings


class TestDiscovery:
    def test_only_slot_prefixed_finished_videos_are_published(self, day):
        found = pipeline.rendered_videos(day.job_dir("2026-11-17"))
        assert [slot for slot, _ in found] == [1, 2]
        assert all("_scenes" not in str(path) for _, path in found)

    def test_an_empty_day_is_not_an_error(self, day):
        result = pipeline.publish_day(day, "2026-11-20")
        assert result.state == "nothing-to-publish"


class TestPublishing:
    def test_each_video_goes_to_its_own_slot(self, day):
        publisher = FakePublisher()
        pipeline.publish_day(day, "2026-11-17", publisher=publisher)
        assert [c["at"] for c in publisher.calls] == ["2026-11-18T09:00:00", "2026-11-18T13:00:00"]

    def test_the_offset_puts_slots_on_the_next_day(self, day):
        publisher = FakePublisher()
        pipeline.publish_day(day, "2026-11-17", publisher=publisher)
        assert all(c["at"].startswith("2026-11-18") for c in publisher.calls)

    def test_future_slots_schedule_rather_than_publish(self, day):
        """The A-4 regression, at the level the user sees it."""
        day.publish.offset_days = 3650          # a decade out: unambiguously future
        publisher = FakePublisher()
        pipeline.publish_day(day, "2026-11-17", publisher=publisher)
        assert {c["mode"] for c in publisher.calls} == {"schedule"}

    def test_draft_mode_never_schedules_or_publishes(self, day):
        day.publish.mode = "draft"
        publisher = FakePublisher()
        pipeline.publish_day(day, "2026-11-17", publisher=publisher)
        assert {c["mode"] for c in publisher.calls} == {"draft"}
        assert all(c["at"] is None for c in publisher.calls)

    def test_empty_platforms_renders_but_posts_nothing(self, day):
        day.publish.platforms = []
        publisher = FakePublisher()
        result = pipeline.publish_day(day, "2026-11-17", publisher=publisher)
        assert result.state == "nothing-to-publish" and publisher.calls == []


class TestIdempotency:
    def test_a_second_run_publishes_nothing(self, day):
        first = FakePublisher()
        pipeline.publish_day(day, "2026-11-17", publisher=first)
        second = FakePublisher()
        result = pipeline.publish_day(day, "2026-11-17", publisher=second)
        assert second.calls == [], "a re-run must not post again"
        assert result.published == 2

    def test_one_platform_failing_inside_a_post_retries_only_that_platform(self, day):
        """Zernio can create the post and still fail one platform inside it (HTTP 207).
        Recording a blanket result would re-post the platform that succeeded."""
        pipeline.publish_day(day, "2026-11-17",
                             publisher=FakePublisher(platform_errors={"instagram"}))
        retry = FakePublisher()
        pipeline.publish_day(day, "2026-11-17", publisher=retry)
        assert retry.calls, "the failed platform must be retried"
        assert all(c["platforms"] == ["instagram"] for c in retry.calls), \
            "the platform that succeeded must not be posted twice"

    def test_a_platform_error_is_recorded_against_that_platform_only(self, day):
        pipeline.publish_day(day, "2026-11-17",
                             publisher=FakePublisher(platform_errors={"instagram"}))
        rows = {(r.platform, r.ok) for r in Ledger(day.ledger_path).entries()}
        assert ("youtube", True) in rows
        assert ("instagram", False) in rows

    def test_a_dead_connection_fails_every_platform_in_that_call(self, day):
        """Nothing was created, so nothing may be marked done."""
        result = pipeline.publish_day(day, "2026-11-17", publisher=FakePublisher(raises={"youtube"}))
        assert result.state == "partial" and result.failed == 2
        rows = list(Ledger(day.ledger_path).entries())
        assert rows and all(not r.ok and r.error for r in rows)
        retry = FakePublisher()
        pipeline.publish_day(day, "2026-11-17", publisher=retry)
        assert all(sorted(c["platforms"]) == ["instagram", "youtube"] for c in retry.calls)

    def test_dry_run_writes_nothing_to_the_ledger(self, day):
        pipeline.publish_day(day, "2026-11-17", dry_run=True, publisher=FakePublisher())
        assert not day.ledger_path.exists()

    def test_the_done_stamp_is_only_written_on_a_clean_day(self, day):
        stamp = day.job_dir("2026-11-17") / pipeline.STAMP
        pipeline.publish_day(day, "2026-11-17", publisher=FakePublisher(raises={"youtube"}))
        assert not stamp.exists()
        pipeline.publish_day(day, "2026-11-17", publisher=FakePublisher())
        assert stamp.is_file()


class TestGates:
    def test_a_run_that_is_not_due_does_nothing(self, day, monkeypatch):
        monkeypatch.setattr(pipeline.sched, "is_due", lambda *a, **k: False)
        result = pipeline.run(day, date="2026-11-17", if_due=True, skip_agent=True)
        assert result.state == "not-due"

    def test_a_finished_day_is_skipped(self, day):
        (day.job_dir("2026-11-17") / pipeline.STAMP).write_text("done")
        result = pipeline.run(day, date="2026-11-17", if_due=True, skip_agent=True)
        assert "already published" in result.message

    def test_two_runs_cannot_overlap(self, day):
        from creatoros_autopilot.core.lock import Lock

        held = Lock(day.lock_path).acquire()
        try:
            assert pipeline.run(day, date="2026-11-17", skip_agent=True).state == "busy"
        finally:
            held.release()
