# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ledger import, which is the step of a cutover that can post a video twice."""
import json

import pytest

from creatoros_autopilot.cli import migrate
from creatoros_autopilot.core.ledger import Ledger


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


@pytest.fixture
def old(tmp_path):
    """A source tree shaped like the real one: two ledgers, specs beside the videos."""
    source = tmp_path / "old"
    videos = tmp_path / "videos" / "2026-11-15"
    videos.mkdir(parents=True)
    for slot, slug in ((1, "alpha"), (2, "beta")):
        (videos / f"{slot:02d}_{slug}.mp4").write_bytes(b"\0")
        (videos / f"{slot:02d}_{slug}.json").write_text(json.dumps({
            "id": f"2026-11-15_{slot:02d}_{slug}",
            "scenes": [{"type": "hook", "voice": "x"}]}))
    write_jsonl(source / "shorts" / "ledger.jsonl", [
        {"ts": "2026-11-15T06:00:00Z", "file": str(videos / "01_alpha.mp4"),
         "platforms": "instagram,youtube", "ok": True, "mode": "schedule"},
        # The oldest format: it recorded THAT a video published, not where.
        {"ts": "2026-11-15T06:01:00Z", "file": str(videos / "02_beta.mp4"),
         "ok": True, "mode": "schedule"},
    ])
    return source, [videos]


class TestResolution:
    def test_a_row_resolves_to_the_job_id_in_its_spec(self, old, tmp_path):
        source, roots = old
        result = migrate.plan(migrate.find_legacy(source), Ledger(tmp_path / "new.jsonl"),
                              roots, ["instagram", "youtube"])
        assert {r.job_id for r in result.imported} == {
            "2026-11-15_01_alpha", "2026-11-15_02_beta"}
        assert result.unresolved == []

    def test_a_platformless_row_is_not_lost(self, old, tmp_path):
        """The bug this test exists for: the first version reported 79 imported while the
        ledger rejected 15 of them. Fifteen published videos would have looked unpublished
        and the next run would have posted them again."""
        source, roots = old
        result = migrate.plan(migrate.find_legacy(source), Ledger(tmp_path / "new.jsonl"),
                              roots, ["instagram", "youtube"])
        beta = [r for r in result.imported if r.job_id == "2026-11-15_02_beta"]
        assert sorted(r.platform for r in beta) == ["instagram", "youtube"]
        assert result.assumed == 2

    def test_what_is_planned_is_what_is_written(self, old, tmp_path):
        source, roots = old
        target = Ledger(tmp_path / "new.jsonl")
        result = migrate.plan(migrate.find_legacy(source), target, roots,
                              ["instagram", "youtube"])
        assert migrate.apply(result, target) == len(result.imported)
        assert len(list(target.entries())) == len(result.imported)

    def test_a_platformless_row_with_no_assumption_is_reported_not_dropped(self, old, tmp_path):
        source, roots = old
        result = migrate.plan(migrate.find_legacy(source), Ledger(tmp_path / "new.jsonl"),
                              roots, [])
        assert len(result.unresolved) == 1
        assert not any(r.job_id == "2026-11-15_02_beta" for r in result.imported)


class TestBookkeeping:
    @pytest.mark.parametrize("marker", ["(promote)", "(backlog)"])
    def test_a_marker_row_is_not_an_unresolved_video(self, marker, old, tmp_path):
        """The promotion tools wrote rows recording an action, not a publication. Calling
        them unresolved would tell the user they are about to double-post when they are not."""
        source, roots = old
        write_jsonl(source / "publish_ledger.jsonl",
                    [{"file": marker, "platform": "*", "ok": True}])
        result = migrate.plan(migrate.find_legacy(source), Ledger(tmp_path / "new.jsonl"),
                              roots, ["instagram"])
        assert len(result.bookkeeping) == 1
        assert result.unresolved == []

    def test_a_genuinely_unresolvable_row_is_still_unresolved(self, old, tmp_path):
        source, roots = old
        write_jsonl(source / "publish_ledger.jsonl",
                    [{"file": "/gone/01_vanished.mp4", "platform": "youtube", "ok": True}])
        result = migrate.plan(migrate.find_legacy(source), Ledger(tmp_path / "new.jsonl"),
                              roots, ["instagram"])
        assert len(result.unresolved) == 1


class TestIdempotency:
    def test_importing_twice_adds_nothing_the_second_time(self, old, tmp_path):
        source, roots = old
        target = Ledger(tmp_path / "new.jsonl")
        first = migrate.plan(migrate.find_legacy(source), target, roots, ["instagram"])
        migrate.apply(first, target)
        second = migrate.plan(migrate.find_legacy(source), target, roots, ["instagram"])
        assert second.imported == []

    def test_a_failed_old_row_is_not_imported_as_success(self, old, tmp_path):
        """A failure carries no claim about the platform's state, so importing it as done
        would skip a video that never published."""
        source, roots = old
        write_jsonl(source / "shorts" / "ledger.jsonl", [
            {"file": str(roots[0] / "01_alpha.mp4"), "platforms": "youtube", "ok": False}])
        result = migrate.plan(migrate.find_legacy(source), Ledger(tmp_path / "new.jsonl"),
                              roots, ["youtube"])
        assert result.imported == []


def test_an_imported_row_blocks_a_re_publish(old, tmp_path):
    """The whole point, end to end."""
    source, roots = old
    target = Ledger(tmp_path / "new.jsonl")
    result = migrate.plan(migrate.find_legacy(source), target, roots,
                          ["instagram", "youtube"])
    migrate.apply(result, target)
    assert target.pending("2026-11-15_01_alpha", ["instagram", "youtube"]) == []
    assert target.pending("2026-11-15_02_beta", ["instagram", "youtube"]) == []
