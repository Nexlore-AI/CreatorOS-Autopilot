# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ledger is the only thing between a re-run and a duplicate post."""
import json

import pytest

from creatoros_autopilot.core.ledger import Entry, Ledger, read_legacy


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "ledger.jsonl")


class TestIdempotency:
    def test_a_published_job_is_not_published_again(self, ledger):
        ledger.record_ok("job-1", "youtube")
        assert ledger.done("job-1", "youtube")
        assert ledger.pending("job-1", ["youtube"]) == []

    def test_identity_is_per_platform(self, ledger):
        """A run that posted to YouTube and failed on Instagram must retry Instagram only
        — not the whole post, which would duplicate the YouTube upload."""
        ledger.record_ok("job-1", "youtube")
        ledger.record_fail("job-1", "instagram", "HTTP 500")
        assert ledger.pending("job-1", ["instagram", "youtube"]) == ["instagram"]

    def test_a_failure_does_not_count_as_done(self, ledger):
        ledger.record_fail("job-1", "youtube", "HTTP 502")
        assert not ledger.done("job-1", "youtube")
        assert ledger.pending("job-1", ["youtube"]) == ["youtube"]

    def test_identity_survives_the_file_moving(self, ledger):
        """The old ledgers keyed on a path, so moving the output folder re-armed every
        video for publishing. The job id does not move."""
        ledger.record_ok("job-1", "youtube", file="/old/place/01_a.mp4")
        assert ledger.done("job-1", "youtube")
        ledger.record_ok("job-2", "youtube", file="/old/place/01_a.mp4")
        assert ledger.done("job-2", "youtube")
        assert ledger.pending("job-1", ["youtube"]) == []

    def test_pending_keeps_the_callers_order(self, ledger):
        assert ledger.pending("x", ["instagram", "youtube"]) == ["instagram", "youtube"]
        assert ledger.pending("x", ["youtube", "instagram"]) == ["youtube", "instagram"]


class TestDurability:
    def test_a_corrupt_line_does_not_hide_the_rest(self, ledger):
        """An unreadable ledger would make every video look unpublished, and the next run
        would post the lot again. One broken line must cost one row, not the file."""
        ledger.record_ok("job-1", "youtube")
        with ledger.path.open("a") as fh:
            fh.write("{ this is not json\n")
        ledger.record_ok("job-2", "youtube")
        assert ledger.done("job-1", "youtube")
        assert ledger.done("job-2", "youtube")

    def test_blank_lines_and_non_objects_are_skipped(self, ledger):
        ledger.path.write_text('\n[]\n"x"\n{"no":"job id"}\n')
        assert list(ledger.entries()) == []

    def test_unknown_fields_are_kept_not_dropped(self, ledger):
        """A newer version's columns must survive a round-trip through an older reader."""
        ledger.path.write_text(json.dumps(
            {"job_id": "j", "platform": "youtube", "ok": True, "future_field": 7}) + "\n")
        entry = next(iter(ledger.entries()))
        assert entry.extra["future_field"] == 7

    def test_missing_file_reads_as_empty(self, tmp_path):
        assert list(Ledger(tmp_path / "nope.jsonl").entries()) == []

    def test_it_appends_and_never_rewrites(self, ledger):
        for i in range(5):
            ledger.record_ok(f"job-{i}", "youtube")
        assert len(ledger.path.read_text().strip().splitlines()) == 5

    def test_a_row_needs_an_identity(self, ledger):
        with pytest.raises(ValueError):
            ledger.record(Entry(job_id="", platform="youtube", ok=True))
        with pytest.raises(ValueError):
            ledger.record(Entry(job_id="j", platform="", ok=True))

    def test_false_is_written_not_omitted(self, ledger):
        """`ok: false` must survive serialisation, or a failure reads back as success."""
        ledger.record_fail("job-1", "youtube", "boom")
        assert json.loads(ledger.path.read_text().strip())["ok"] is False


class TestLegacyImport:
    def test_shell_ledger_rows_fan_out_per_platform(self, tmp_path):
        """The shell wrote one row per post covering every platform; the new ledger wants
        one row per platform."""
        old = tmp_path / "old.jsonl"
        old.write_text(json.dumps({
            "ts": "2026-11-16T06:03:46Z", "slot": 2, "file": "/x/02_learning-rate.mp4",
            "mode": "schedule", "platforms": "instagram,youtube", "ok": True,
            "title": "Learning rate"}) + "\n")
        rows = read_legacy(old, job_id_for=lambda p: "2026-11-16_02_learning-rate")
        assert [r.platform for r in rows] == ["instagram", "youtube"]
        assert all(r.ok and r.job_id == "2026-11-16_02_learning-rate" for r in rows)

    def test_unresolvable_rows_are_returned_not_dropped(self, tmp_path):
        """Dropping a row would let an already-published video publish twice, so the
        caller has to see it and decide."""
        old = tmp_path / "old.jsonl"
        old.write_text(json.dumps({"file": "/gone/01_a.mp4", "platform": "youtube", "ok": True}) + "\n")
        rows = read_legacy(old, job_id_for=lambda p: None)
        assert len(rows) == 1 and rows[0].job_id == ""
