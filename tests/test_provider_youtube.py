# SPDX-License-Identifier: AGPL-3.0-or-later
"""The YouTube Data API backend, and especially its resume path.

The resume path is the only code in this project that can publish a video twice, and it
had no tests at all. Everything here is about the two ways that happens: giving up on an
upload whose bytes all arrived, and mistaking YouTube's "finalising" answer for a failure.
Both are assertions about the *order of requests* — did the client ask the session what it
holds before it gave up, and did it ever send bytes it had no business sending — so the
fake below records every request and answers from a script rather than modelling YouTube.

The status tests cover the other half: a payload that says one thing and means another.
`visibility: private` with a `publishAt` reads as private and goes public, and
`made_for_kids: "false"` used to declare the video made for kids.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from creatoros_autopilot.interfaces.availability import Availability
from creatoros_autopilot.interfaces.publish import Publisher, PublishRequest
from creatoros_autopilot.providers.publish import youtube as yt


class FakeUpload:
    """A scripted resumable-upload session: one reply per request, in order.

    A reply is `{"code": int, "json": str, "headers": dict}`, or `{"drop": True}` to close
    the connection without answering — a mid-upload disconnect, which is what the retry
    and resume logic exists for and what no mock of `_req` reproduces faithfully.
    """

    def __init__(self, replies, size):
        self.replies = list(replies)
        self.size = size
        self.seen = []                 # one dict per request received, in order
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass               # the test output is the assertions, not an access log

            def do_PUT(self):
                sent = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(sent) if sent else b""
                asked = self.headers.get("Content-Range", "")
                fake.seen.append({"range": asked, "bytes": sent, "body": body,
                                  "probe": asked == f"bytes */{fake.size}"})
                if not fake.replies:
                    raise AssertionError(f"unscripted request: {asked!r}")
                reply = fake.replies.pop(0)
                if reply.get("drop"):
                    self.close_connection = True
                    return
                payload = (reply.get("json") or "{}").encode()
                self.send_response(reply["code"])
                for key, value in (reply.get("headers") or {}).items():
                    self.send_header(key, value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_POST = do_PUT

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/upload/session"

    @property
    def puts(self) -> list:
        """The requests that carried bytes, as opposed to the probes."""
        return [call for call in self.seen if not call["probe"]]

    @property
    def probes(self) -> list:
        return [call for call in self.seen if call["probe"]]


SIZE = 1000
RETRY = {"code": 503, "json": '{"error":{"message":"Backend Error"}}'}


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """The backoff and the finalise wait are real seconds; no test needs to live them."""
    monkeypatch.setattr(yt.time, "sleep", lambda seconds: None)


@pytest.fixture
def clip(tmp_path):
    """A file whose bytes differ at every offset, so a resumed PUT can be checked."""
    path = tmp_path / "0930-clip.mp4"
    path.write_bytes(bytes((i * 7 + 11) % 256 for i in range(SIZE)))
    return path


def spec_for(**platform) -> dict:
    meta = {"title": "Why Rust ate the CLI", "description": "notes", "tags": ["rust"]}
    meta.update(platform)
    return {"id": "2026-11-17-01", "platforms": {"youtube": meta}}


def request_for(clip, **kw) -> PublishRequest:
    kw.setdefault("spec", spec_for())
    kw.setdefault("platforms", ("youtube",))
    return PublishRequest(video=str(clip), job_id="2026-11-17-01", **kw)


class TestContract:
    def test_it_is_a_publisher(self):
        assert isinstance(yt.YouTube(), Publisher)

    def test_it_claims_to_record_publications(self):
        """Unlike local_folder: a success here really is a video on a channel, so the
        pipeline is right to write a ledger row for it."""
        assert yt.YouTube().records_publication is True
        assert yt.YouTube().platforms == ("youtube",)

    def test_availability_says_what_to_do_when_the_keys_are_missing(self, monkeypatch):
        for name in yt.ENV:
            monkeypatch.delenv(name, raising=False)
        answer = yt.YouTube().available()
        assert isinstance(answer, Availability) and not answer
        assert "YOUTUBE_REFRESH_TOKEN" in answer.detail
        assert "creatoros init youtube" in answer.fix

    def test_availability_is_offline_and_never_echoes_a_secret(self, monkeypatch):
        for name in yt.ENV:
            monkeypatch.setenv(name, "s3cret-value")
        monkeypatch.setattr(yt, "_req", lambda *a, **kw: pytest.fail("available() called out"))
        answer = yt.YouTube().available()
        assert answer and "s3cret-value" not in (answer.detail + answer.fix)

    def test_a_dry_run_makes_no_request(self, clip, monkeypatch):
        monkeypatch.setattr(yt, "_req", lambda *a, **kw: pytest.fail("dry run called out"))
        result = yt.YouTube().publish(request_for(clip, mode=yt.NOW, dry_run=True))
        assert result.ok and result.dry_run and result.ok_for("youtube")


class TestResume:
    """Defects 1 and 2: the two ways a finished upload was reported as a failure."""

    def test_the_last_attempt_still_asks_the_session_what_it_holds(self, clip):
        """Three 503s, then the connection drops after every byte arrived.

        The run gives up here, and giving up used to mean breaking out of the loop without
        a final probe — so the one answer that says "it is already uploaded" was never
        asked for, the publish was reported failed, and the next run posted the video a
        second time.
        """
        replies = [
            RETRY, {"code": 308, "headers": {"Range": "bytes=0-99"}},
            RETRY, {"code": 308, "headers": {"Range": "bytes=0-199"}},
            RETRY, {"code": 308, "headers": {"Range": "bytes=0-299"}},
            {"drop": True},                              # attempt 4: all of it got there
            {"code": 200, "json": '{"id":"aBc123"}'},    # the probe that used to be skipped
        ]
        with FakeUpload(replies, SIZE) as fake:
            assert yt.YouTube()._send(fake.url, clip, SIZE, "tok") == "aBc123"
            assert len(fake.puts) == 4, "every attempt should have sent bytes"
            assert fake.seen[-1]["probe"], "the final attempt must end in a probe"
            assert not fake.replies, "the scripted final probe was never made"

    def test_a_finalising_session_is_waited_out_not_re_sent(self, clip):
        """A 308 whose Range covers the whole file is YouTube finalising, not an error.

        It used to be read as `resume from byte 1000 of 1000`, which built
        `Content-Range: bytes 1000-999/1000` — a range YouTube rejects with 400. A
        successful upload therefore failed hard and was uploaded again.
        """
        replies = [
            RETRY,
            {"code": 308, "headers": {"Range": f"bytes=0-{SIZE - 1}"}},   # finalising
            {"code": 308, "headers": {"Range": f"bytes=0-{SIZE - 1}"}},   # still finalising
            {"code": 201, "json": '{"id":"xYz789"}'},
        ]
        with FakeUpload(replies, SIZE) as fake:
            assert yt.YouTube()._send(fake.url, clip, SIZE, "tok") == "xYz789"
        assert len(fake.puts) == 1, "nothing should be sent once every byte is stored"
        assert all(f"bytes {SIZE}-" not in call["range"] for call in fake.seen), \
            "an inverted Content-Range was sent"

    def test_a_session_that_never_names_the_video_fails_without_re_uploading(self, clip):
        """The failure a user must not answer by re-running, so it has to say so."""
        replies = [RETRY] + [{"code": 308, "headers": {"Range": f"bytes=0-{SIZE - 1}"}}
                             for _ in range(len(yt.FINALISE_WAITS) + 1)]
        with FakeUpload(replies, SIZE) as fake:
            with pytest.raises(yt.PublishError) as exc:
                yt.YouTube()._send(fake.url, clip, SIZE, "tok")
        assert "Studio" in str(exc.value) and "uploads it again" in str(exc.value)
        assert len(fake.puts) == 1
        assert len(fake.probes) == len(yt.FINALISE_WAITS) + 1

    def test_a_partial_upload_continues_from_the_stored_byte(self, clip):
        """Resumption is the idempotency: the tail is sent, not the file again."""
        replies = [
            RETRY, {"code": 308, "headers": {"Range": "bytes=0-399"}},
            {"code": 200, "json": '{"id":"tail-ok"}'},
        ]
        with FakeUpload(replies, SIZE) as fake:
            assert yt.YouTube()._send(fake.url, clip, SIZE, "tok") == "tail-ok"
        resumed = fake.puts[1]
        assert resumed["range"] == f"bytes 400-{SIZE - 1}/{SIZE}"
        assert resumed["bytes"] == SIZE - 400
        assert resumed["body"] == clip.read_bytes()[400:], "the wrong bytes were resumed"

    def test_an_upload_with_no_range_header_starts_from_zero(self, clip):
        """308 and no Range means nothing arrived at all."""
        replies = [RETRY, {"code": 308}, {"code": 200, "json": '{"id":"from-zero"}'}]
        with FakeUpload(replies, SIZE) as fake:
            assert yt.YouTube()._send(fake.url, clip, SIZE, "tok") == "from-zero"
        assert fake.puts[1]["range"] == "", "a restart from zero sends no Content-Range"
        assert fake.puts[1]["bytes"] == SIZE

    def test_a_vanished_session_is_named_as_such(self, clip):
        replies = [RETRY, {"code": 404, "json": '{"error":{"message":"Not Found"}}'}]
        with FakeUpload(replies, SIZE) as fake:
            with pytest.raises(yt.PublishError, match="session is gone"):
                yt.YouTube()._send(fake.url, clip, SIZE, "tok")

    def test_a_200_with_no_video_id_is_a_failure(self, clip):
        """A 200 is not a success on its own: the id is what the ledger records."""
        with FakeUpload([{"code": 200, "json": '{"kind":"youtube#video"}'}], SIZE) as fake:
            with pytest.raises(yt.PublishError, match="no video id"):
                yt.YouTube()._send(fake.url, clip, SIZE, "tok")

    def test_a_non_retryable_error_stops_without_four_attempts(self, clip):
        quota = '{"error":{"message":"quota","errors":[{"reason":"quotaExceeded"}]}}'
        with FakeUpload([{"code": 403, "json": quota}], SIZE) as fake:
            with pytest.raises(yt.PublishError, match="quotaExceeded"):
                yt.YouTube()._send(fake.url, clip, SIZE, "tok")
            assert len(fake.seen) == 1

    @pytest.mark.parametrize("header, expected", [
        ("bytes=0-0", 1),
        ("bytes=0-499", 500),
        ("", 0),
        (f"bytes=0-{SIZE + 50}", SIZE),         # past the end can only mean "all of it"
    ])
    def test_the_resume_point_is_read_from_the_range(self, header, expected):
        assert yt._resume_point(header, SIZE) == expected

    def test_an_unreadable_range_is_not_guessed_at(self):
        with pytest.raises(yt.PublishError, match="resume position"):
            yt._resume_point("bytes=0-bananas", SIZE)


class TestStatus:
    """Defects 3 and 4: a payload that says one thing to the API and another to the user."""

    def test_an_explicit_private_plus_a_schedule_is_refused(self, clip):
        """It used to be accepted: privacyStatus private, publishAt set, public at publishAt.

        The payload read as private at every point a human would look, and the video went
        public anyway.
        """
        request = request_for(clip, mode=yt.SCHEDULE, at="2027-01-01T09:00:00", tz="UTC",
                              spec=spec_for(visibility="private"), dry_run=True)
        result = yt.YouTube().publish(request)
        assert not result.ok and not result.ok_for("youtube")
        assert "private" in result.error and "publishAt" in result.error
        assert "mode 'draft'" in result.error, "the error has to say what to do instead"

    def test_an_explicit_unlisted_plus_a_schedule_is_refused(self, clip):
        request = request_for(clip, mode=yt.SCHEDULE, at="2027-01-01T09:00:00", tz="UTC",
                              spec=spec_for(visibility="unlisted"), dry_run=True)
        assert not yt.YouTube().publish(request).ok

    def test_a_schedule_with_no_visibility_asked_for_still_works(self, clip):
        """The default is public, and public at publishAt is what a schedule means."""
        status = yt.YouTube()._status(
            request_for(clip, mode=yt.SCHEDULE, at="2027-01-01T09:00:00", tz="UTC"), {})
        assert status == {"privacyStatus": "private", "selfDeclaredMadeForKids": False,
                          "publishAt": "2027-01-01T09:00:00Z"}

    def test_an_explicit_public_schedule_is_not_refused(self, clip):
        """`visibility: public` is what a scheduled upload becomes, so it is no conflict."""
        status = yt.YouTube()._status(
            request_for(clip, mode=yt.SCHEDULE, at="2027-01-01T09:00:00", tz="UTC"),
            {"visibility": "public"})
        assert status["publishAt"] == "2027-01-01T09:00:00Z"

    def test_a_past_schedule_is_refused(self, clip):
        request = request_for(clip, mode=yt.SCHEDULE, at="2020-01-01T09:00:00", tz="UTC")
        with pytest.raises(yt.PublishError, match="in the past"):
            yt.YouTube()._status(request, {})

    def test_draft_uploads_private_whatever_the_visibility_says(self, clip):
        status = yt.YouTube()._status(request_for(clip, mode=yt.DRAFT),
                                      {"visibility": "public"})
        assert status["privacyStatus"] == "private" and "publishAt" not in status

    def test_mode_now_honours_the_visibility(self, clip):
        status = yt.YouTube()._status(request_for(clip, mode=yt.NOW),
                                      {"visibility": "unlisted"})
        assert status["privacyStatus"] == "unlisted"

    @pytest.mark.parametrize("key", ["made_for_kids", "madeForKids"])
    @pytest.mark.parametrize("value, declared", [
        (True, True), (False, False),
        ("true", True), ("false", False),     # "false" used to declare it made for kids
        ("False", False), ("TRUE", True),
        ("yes", True), ("no", False),
        (1, True), (0, False),
    ])
    def test_made_for_kids_is_read_as_a_boolean(self, clip, key, value, declared):
        status = yt.YouTube()._status(request_for(clip, mode=yt.NOW), {key: value})
        assert status["selfDeclaredMadeForKids"] is declared

    def test_made_for_kids_defaults_to_false_when_unset(self, clip):
        status = yt.YouTube()._status(request_for(clip, mode=yt.NOW), {})
        assert status["selfDeclaredMadeForKids"] is False

    @pytest.mark.parametrize("value", ["maybe", "", "kids", 2, 1.5, [], {"a": 1}])
    def test_an_ambiguous_made_for_kids_is_refused(self, clip, value):
        """It is a legal self-declaration that turns off comments, notifications,
        personalised ads and end screens. A guess is not good enough."""
        with pytest.raises(yt.PublishError, match="must be true or false"):
            yt.YouTube()._status(request_for(clip, mode=yt.NOW), {"made_for_kids": value})

    def test_an_unknown_visibility_is_refused(self, clip):
        with pytest.raises(yt.PublishError, match="public, unlisted or private"):
            yt.YouTube()._status(request_for(clip, mode=yt.NOW), {"visibility": "hidden"})

    def test_the_dry_run_payload_is_what_would_be_sent(self, clip, capsys):
        result = yt.YouTube().publish(request_for(
            clip, mode=yt.NOW, dry_run=True,
            spec=spec_for(visibility="private", made_for_kids="false",
                          tags=["rust", "cli"])))
        assert result.ok
        printed = capsys.readouterr().out
        payload = json.loads(printed[printed.index('{\n  "snippet"'):])
        assert payload["status"] == {"privacyStatus": "private",
                                    "selfDeclaredMadeForKids": False}
        assert payload["snippet"]["tags"] == ["rust", "cli"]
        assert "no AI-narration disclosure" in printed, "the disclosure note is not optional"


class TestGuards:
    def test_another_platform_is_refused(self, clip):
        result = yt.YouTube().publish(request_for(clip, platforms=("instagram",)))
        assert not result.ok and "youtube only" in result.error

    def test_a_missing_file_is_refused(self, tmp_path):
        result = yt.YouTube().publish(PublishRequest(
            video=str(tmp_path / "gone.mp4"), job_id="x", platforms=("youtube",)))
        assert not result.ok and "no such file" in result.error

    def test_an_empty_file_is_refused(self, tmp_path):
        empty = tmp_path / "empty.mp4"
        empty.write_bytes(b"")
        result = yt.YouTube().publish(PublishRequest(
            video=str(empty), job_id="x", platforms=("youtube",)))
        assert not result.ok and "0 bytes" in result.error

    def test_a_rejected_refresh_token_says_what_to_do(self, clip, monkeypatch):
        for name in yt.ENV:
            monkeypatch.setenv(name, "x")
        body = '{"error":"invalid_grant","error_description":"Token has been expired"}'
        with FakeUpload([{"code": 400, "json": body}], SIZE) as fake:
            monkeypatch.setattr(yt, "TOKEN_URL", fake.url)
            result = yt.YouTube().publish(request_for(clip, mode=yt.NOW))
        assert not result.ok and "creatoros init youtube" in result.error
        assert "Token has been expired" not in result.error, \
            "an auth endpoint's body does not belong in a log"

    def test_a_session_without_a_location_header_is_refused(self, clip, monkeypatch):
        """There is nowhere to send the bytes, and a 200 alone is not a session."""
        with FakeUpload([{"code": 200, "json": '{"kind":"youtube#video"}'}], SIZE) as fake:
            monkeypatch.setattr(yt, "UPLOAD_URL", fake.url)
            with pytest.raises(yt.PublishError, match="Location"):
                yt.YouTube()._begin("tok", {"title": "t"}, {"privacyStatus": "private"},
                                    SIZE)

    def test_a_platform_requires_its_search_metadata(self):
        assert yt.YouTube().requires("youtube") == ("title", "description", "tags")
        assert yt.YouTube().requires("instagram") == ()
