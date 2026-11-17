# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Instagram publisher, against a fake Graph API on localhost.

Every call here is a real HTTP round trip to a server these tests start, because both bugs
they hold down are about *which calls were made* and what was reported when they were not:
a mode that returned success without ever calling `media_publish`, and a mode that called
nothing and then told the user to fix it with a setting that does not exist. A mocked
method would have agreed with both.

The pipeline tests go through `pipeline.publish_day`, not through `publish()` alone,
because the cost of the first bug was in the ledger rather than in the result: a draft was
recorded as published, so the real publish skipped that job forever.
"""
import datetime as dt
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from creatoros_autopilot.core import pipeline
from creatoros_autopilot.core.ledger import Ledger
from creatoros_autopilot.core.settings import Settings
from creatoros_autopilot.interfaces import Publisher, PublishRequest
from creatoros_autopilot.interfaces.publish import DRAFT, NOW, SCHEDULE
from creatoros_autopilot.providers.publish import instagram as module
from creatoros_autopilot.providers.publish.instagram import Instagram

ACCOUNT = "17841400000000000"
CONTAINER = "container-1"
MEDIA = "media-1"
PERMALINK = "https://www.instagram.com/reel/abc123/"
TOKEN = "test-token-never-in-a-url"
VIDEO_URL = "https://media.example.com/01_alpha.mp4"


class FakeGraph:
    """A Graph API that records every call and can be told to fail."""

    def __init__(self):
        self.calls: list[dict] = []
        self.container_status = "FINISHED"
        self.publish_status = 200

    @property
    def created(self) -> list[dict]:
        return [c for c in self.calls if c["path"].endswith("/media")]

    @property
    def posted(self) -> list[dict]:
        return [c for c in self.calls if c["path"].endswith("/media_publish")]


def _handler(fake: FakeGraph):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def _reply(self, code: int, body: dict) -> None:
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _record(self, method: str, path: str, params: dict) -> None:
            fake.calls.append({"method": method, "path": path, "params": params,
                               "query": urlparse(self.path).query,
                               "auth": self.headers.get("Authorization", "")})

        def do_POST(self):
            parsed = urlparse(self.path)
            size = int(self.headers.get("Content-Length") or 0)
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(size).decode()).items()}
            self._record("POST", parsed.path, form)
            if parsed.path.endswith("/media"):
                return self._reply(200, {"id": CONTAINER})
            if parsed.path.endswith("/media_publish"):
                if fake.publish_status != 200:
                    return self._reply(fake.publish_status,
                                       {"error": {"message": "please retry", "code": 2}})
                return self._reply(200, {"id": MEDIA})
            return self._reply(404, {"error": {"message": f"unexpected POST {parsed.path}"}})

        def do_GET(self):
            parsed = urlparse(self.path)
            self._record("GET", parsed.path,
                         {k: v[0] for k, v in parse_qs(parsed.query).items()})
            if parsed.path.endswith("/" + CONTAINER):
                return self._reply(200, {"status_code": fake.container_status,
                                         "status": "a fake container"})
            if parsed.path.endswith("/" + MEDIA):
                return self._reply(200, {"permalink": PERMALINK})
            if parsed.path.endswith("/" + ACCOUNT):
                return self._reply(200, {"username": "nexloreai", "media_count": 7})
            return self._reply(404, {"error": {"message": f"unexpected GET {parsed.path}"}})

        def log_message(self, *args):
            """Silence, so a passing run does not print a request log."""

    return Handler


@pytest.fixture
def graph(monkeypatch):
    fake = FakeGraph()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(fake))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(module, "HOST", f"http://127.0.0.1:{server.server_port}")
    # The real first poll waits five seconds, which is right for a video Instagram is
    # still fetching and pointless against a server that answers instantly.
    monkeypatch.setattr(module, "POLL_FIRST", 0.01)
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", ACCOUNT)
    # A developer's own public-host settings must not change what these assert.
    for name in ("CREATOROS_PUBLIC_BASE_URL", "CREATOROS_PUBLIC_ROOT",
                 "INSTAGRAM_GRAPH_VERSION"):
        monkeypatch.delenv(name, raising=False)
    yield fake
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def ask(mode: str, *, at: str | None = None, dry_run: bool = False) -> PublishRequest:
    """One request for one Reel, with the URL already public so only `mode` is in play."""
    return PublishRequest(video=VIDEO_URL, job_id="2026-11-17_01_alpha",
                          platforms=("instagram",), mode=mode, at=at, tz="Asia/Kolkata",
                          dry_run=dry_run,
                          spec={"platforms": {"instagram": {"caption": "a caption"}}})


def day(tmp_path, *, mode: str, offset_days: int, date: str,
        post_times=("09:00",)) -> Settings:
    """A workspace with one rendered video, routed at Instagram directly."""
    settings = Settings(workspace=str(tmp_path), timezone="Asia/Kolkata")
    settings.publish.platforms = ["instagram"]
    settings.publish.backends = ["instagram"]
    settings.publish.mode = mode
    settings.publish.offset_days = offset_days
    settings.publish.post_times = list(post_times)
    settings.ensure_dirs()
    job_dir = settings.job_dir(date)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "01_alpha.mp4").write_bytes(b"\x00" * 1024)
    (job_dir / "01_alpha.json").write_text(json.dumps({
        "id": f"{date}_01_alpha",
        "scenes": [{"type": "hook", "voice": "x"}],
        "platforms": {"instagram": {"caption": "a caption", "video_url": VIDEO_URL}},
    }), encoding="utf-8")
    return settings


def test_the_backend_satisfies_the_protocol():
    assert isinstance(Instagram(), Publisher)


def test_constructing_it_touches_nothing(monkeypatch):
    """No key read and no call at import or construction time."""
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", raising=False)
    assert Instagram().requires("instagram") == ("caption",)


class TestDraftIsNotAThing:
    """Defect 1: a container is not a draft, and must never be recorded as published."""

    def test_draft_is_refused_and_nothing_is_created(self, graph):
        result = Instagram().publish(ask(DRAFT))
        assert not result.ok
        assert not result.ok_for("instagram")
        # The old behaviour created a container here and returned ok. Refusing before any
        # call also means no 24-hour orphan is left behind on the account.
        assert graph.calls == []

    def test_the_refusal_names_backends_that_can_hold_a_draft(self, graph):
        error = Instagram().publish(ask(DRAFT)).error
        assert "local_folder" in error and "zernio" in error
        assert "publish.offset_days: 0" in error

    def test_a_dry_run_in_draft_mode_refuses_too(self, graph):
        """A dry run that passed in a mode that can never publish is how this shipped."""
        result = Instagram().publish(ask(DRAFT, dry_run=True))
        assert not result.ok
        assert graph.calls == []

    def test_a_draft_day_records_nothing_and_stays_pending(self, graph, tmp_path):
        """The blocker, at the level the user saw it: `published: 1` and nothing posted."""
        settings = day(tmp_path, mode="draft", offset_days=1, date="2026-11-17")
        result = pipeline.publish_day(settings, "2026-11-17")

        assert (result.state, result.published, result.failed) == ("partial", 0, 1)
        assert graph.posted == []
        ledger = Ledger(settings.ledger_path)
        assert not ledger.done("2026-11-17_01_alpha", "instagram")
        assert ledger.pending("2026-11-17_01_alpha", ["instagram"]) == ["instagram"]
        row = ledger.last("2026-11-17_01_alpha", "instagram")
        assert row is not None and not row.ok and "no draft" in row.error

    def test_a_later_real_publish_of_the_same_job_is_not_skipped(self, graph, tmp_path):
        """What the ok row cost: the same job must still publish afterwards."""
        date = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        settings = day(tmp_path, mode="draft", offset_days=0, date=date)
        pipeline.publish_day(settings, date)
        assert graph.posted == []

        settings.publish.mode = "auto"      # the slot is yesterday's, so the decision is now
        result = pipeline.publish_day(settings, date)
        assert (result.state, result.published) == ("ok", 1)
        assert len(graph.posted) == 1


class TestSchedulingIsNotAThing:
    """Defect 2: the automated path refused every video, and named an unusable fix."""

    def test_a_future_slot_is_refused_without_calling_anything(self, graph):
        result = Instagram().publish(ask(SCHEDULE, at="2026-11-18T09:00:00"))
        assert not result.ok
        assert graph.calls == []
        assert "2026-11-18T09:00:00" in result.error

    def test_the_refusal_names_settings_that_exist(self, graph):
        error = Instagram().publish(ask(SCHEDULE, at="2026-11-18T09:00:00")).error
        # `publish.mode` is draft|auto, so the old advice — install the daily job and let
        # it publish with `mode: now` — could not be expressed in creatoros.yaml at all,
        # and the installed unit runs `creatoros run --if-due` once a day regardless.
        assert "creatoros schedule" not in error
        assert "publish.offset_days: 0" in error
        assert "creatoros publish <video> --mode now" in error
        assert "publish.backends: [zernio]" in error

    def test_the_shipped_offset_refuses_every_video(self, graph, tmp_path):
        settings = day(tmp_path, mode="auto", offset_days=1, date="2026-11-17")
        result = pipeline.publish_day(settings, "2026-11-17")

        assert (result.state, result.published, result.failed) == ("partial", 0, 1)
        assert graph.calls == []
        row = Ledger(settings.ledger_path).last("2026-11-17_01_alpha", "instagram")
        assert row is not None and not row.ok

    def test_offset_days_0_really_publishes(self, graph, tmp_path):
        """The fix the refusal recommends, run end to end. Otherwise it is just words."""
        yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        settings = day(tmp_path, mode="auto", offset_days=0, date=yesterday)
        result = pipeline.publish_day(settings, yesterday)

        assert (result.state, result.published, result.failed) == ("ok", 1, 0)
        assert len(graph.posted) == 1
        row = Ledger(settings.ledger_path).last(f"{yesterday}_01_alpha", "instagram")
        assert row is not None and row.ok
        assert (row.post_id, row.post_url) == (MEDIA, PERMALINK)


class TestPublishingNow:
    def test_the_three_calls_happen_in_order(self, graph):
        result = Instagram(poll_timeout=5.0).publish(ask(NOW))
        assert result.ok and result.post_id == MEDIA
        assert result.url_for("instagram") == PERMALINK
        assert [c["method"] for c in graph.calls][:3] == ["POST", "GET", "POST"]
        assert graph.created and graph.posted
        assert graph.created[0]["params"]["media_type"] == "REELS"
        assert graph.created[0]["params"]["video_url"] == VIDEO_URL

    def test_a_container_that_errored_is_never_published(self, graph):
        graph.container_status = "ERROR"
        result = Instagram(poll_timeout=5.0).publish(ask(NOW))
        assert not result.ok and graph.posted == []

    def test_media_publish_is_not_retried(self, graph):
        """A retry can duplicate a Reel that did go live, so one attempt only."""
        graph.publish_status = 500
        result = Instagram(poll_timeout=5.0).publish(ask(NOW))
        assert not result.ok
        assert len(graph.posted) == 1
        assert CONTAINER in result.error

    def test_the_token_never_reaches_a_url(self, graph):
        Instagram(poll_timeout=5.0).publish(ask(NOW))
        assert graph.calls
        for call in graph.calls:
            assert TOKEN not in call["path"] and TOKEN not in call["query"]
            assert TOKEN not in json.dumps(call["params"])
            assert call["auth"] == "Bearer " + TOKEN


class TestAvailability:
    def test_without_keys_it_says_which_and_how(self, monkeypatch):
        monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", raising=False)
        check = Instagram().available()
        assert not check
        assert "INSTAGRAM_ACCESS_TOKEN" in check.detail
        assert "INSTAGRAM_BUSINESS_ACCOUNT_ID" in check.fix

    def test_with_keys_it_reports_the_account(self, graph):
        check = Instagram().available()
        assert check and "nexloreai" in check.detail
