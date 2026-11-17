# SPDX-License-Identifier: AGPL-3.0-or-later
"""Uploading to YouTube with the Data API v3, with nothing in the middle.

    creatoros publish VIDEO.mp4 --platforms youtube

Zernio reaches YouTube too, and stays the Nexlore preset's publisher. This backend exists
so that posting to your own channel needs nothing but a Google OAuth client you own: no
third-party subscription sitting between the render and the upload.

**It cannot declare the AI narration.** The Zernio payload always carries
`containsSyntheticMedia: true`, because every video this pipeline makes is voiced by a
synthetic voice and disclosure is an obligation rather than a preference. The Data API has
no equivalent field — altered-content disclosure exists only in Studio — so a video
uploaded through this backend arrives undeclared. `publish()` prints that on every call,
and the line is not there to be deleted: tick Studio > Edit > Details > Altered content
after the upload, or publish through a backend that can send the flag.

Two things about this endpoint are worth knowing before reading the code:

* `publishAt` is honoured only when `privacyStatus` is `private`. Send `public` with a
  `publishAt` and the API answers 200, drops the schedule, and the video is live
  immediately — the single most common mistake with this API. So a scheduled upload here
  is always uploaded private, and YouTube makes it **public** at `publishAt`. There is no
  scheduled private and no scheduled unlisted, so `visibility: private` with
  `mode: schedule` is refused rather than reinterpreted: it was accepted silently once,
  and a video somebody had asked to keep private went public at `publishAt`.
* Resumption is not a bandwidth optimisation. The upload session is the only idempotency
  this API offers: a failed upload restarted by opening a *new* session publishes the
  video twice whenever the first attempt actually got there. So a dropped connection is
  answered by asking the existing session how many bytes it holds and continuing from
  that byte, never by starting again — including on the attempt that gives up, which is
  exactly when the answer matters most.

There is no draft on YouTube. `mode: draft` uploads the video as private and says so.

Credentials come from the environment (`YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`,
`YOUTUBE_REFRESH_TOKEN`); nothing here writes or echoes them. The one-time browser consent
that mints the refresh token belongs to `creatoros init`, not to a publisher that may be
running unattended from a 06:30 scheduled job.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message
from pathlib import Path

from ...core import schedule as sched
from ...interfaces.availability import Availability
from ...interfaces.publish import (
    DRAFT,
    NOW,
    SCHEDULE,
    PlatformOutcome,
    PublishRequest,
    PublishResult,
)

# Shared with the Zernio backend on purpose. A video must get the same title whichever
# backend posts it, or the ledger records one title and the channel shows another.
from .zernio import derive_tags, derive_title, load_spec

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = ("https://www.googleapis.com/upload/youtube/v3/videos"
              "?uploadType=resumable&part=snippet,status")
ENV = ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN")

# 0 is this module's code for "the transport failed" (see `_req`), the same convention the
# Zernio backend uses after a BrokenPipeError mid-upload escaped as a traceback and cost a
# day's first Short. 308 is YouTube's "resume incomplete": on the data PUT it means fewer
# bytes arrived than were promised, which is the one case resumption exists for.
RETRYABLE = {0, 308, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4
# `tags_total` is the sum of every tag's length, and it is the limit people trip over:
# individually valid tags adding up to 520 characters reject the whole upload.
LIMITS = {"title": 100, "description": 5000, "tag": 100, "tags_total": 500,
          "bytes": 256 * 1024 * 1024 * 1024}
CATEGORY_SCI_TECH = "28"
# A socket timeout, not a deadline for the transfer: it bounds one blocking read or write.
UPLOAD_TIMEOUT = 600
# How long to keep asking a session that holds every byte but has not yet named the video.
# A 308 covering the whole file is YouTube finalising an upload that arrived, so the only
# correct move is to wait for the id: there is nothing left to send.
FINALISE_WAITS = (2, 5, 10)
# YAML and JSON both produce real booleans for `true`, but a spec written by hand says
# "false" or "no" often enough to matter, and these values are declarations rather than
# preferences — so each one is read from a closed set and anything else is refused.
TRUTHY = frozenset({"true", "yes", "y", "on", "1"})
FALSY = frozenset({"false", "no", "n", "off", "0"})
# What a Google error reason means in practice, since the API's own message rarely says.
HINTS = {
    "quotaExceeded": "an upload spends 1600 of the default 10,000 daily quota units, so "
                     "six a day is the ceiling until Google raises it",
    "uploadLimitExceeded": "the channel has hit its own daily upload limit; tomorrow it resets",
    "youtubeSignupRequired": "that Google account has no YouTube channel yet",
    "forbidden": "the token may not carry the youtube.upload scope; run `creatoros init youtube`",
    "invalidPublishAt": "publishAt must be in the future, and privacyStatus must be private",
}


class PublishError(RuntimeError):
    pass


def _json(raw: bytes) -> dict:
    """The body as a dict, whatever actually came back.

    A proxy's HTML error page is a real answer to a real request, and it should read as an
    error rather than as an AttributeError three frames from the call that got it.
    """
    try:
        parsed = json.loads(raw or b"{}")
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    return {"error": raw[:300].decode(errors="replace")}


def _req(method: str, url: str, *, token: str = "", json_body: dict | None = None,
         form: dict | None = None, data=None, headers: dict | None = None,
         timeout: int = 60) -> tuple[int, dict, Message]:
    """One HTTP call. Returns (status, parsed body, response headers).

    The headers are returned as the `Message` urllib gives us rather than a dict, because
    the two headers that matter here — `Location` and `Range` — are read back
    case-insensitively and Google does not promise their casing.
    """
    hdrs = {"Accept": "application/json"}
    body = None
    if json_body is not None:
        body = json.dumps(json_body).encode()
        hdrs["Content-Type"] = "application/json; charset=UTF-8"
    elif form is not None:
        body = urllib.parse.urlencode(form).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    elif data is not None:
        body = data
    if token:
        hdrs["Authorization"] = "Bearer " + token
    hdrs.update(headers or {})
    request = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, _json(resp.read()), resp.headers
    except urllib.error.HTTPError as exc:
        # The resume probe's answer *is* an HTTPError: urllib treats anything outside 2xx
        # as one, and a 308 carries the `Range` header the probe is asking for.
        return exc.code, _json(exc.read()), exc.headers or Message()
    except (urllib.error.URLError, OSError) as exc:
        # A reset socket, a timeout, a broken pipe part-way through 50 MB. Reported as
        # status 0 so callers see a failure they can retry rather than an exception that
        # ends the whole day's run.
        return 0, {"error": f"{type(exc).__name__}: {exc}"}, Message()


def _call(method: str, url: str, *, label: str, retryable=RETRYABLE,
          **kw) -> tuple[int, dict, Message]:
    """`_req` with backoff on a transport failure or a transient server error.

    `label` is printed in place of the URL. An upload session URI is a capability — anyone
    holding it can write to the channel — so it has no business in a log or a scrollback.

    `retryable` is a parameter because 308 is a failure to the data PUT and the expected
    answer to the resume probe, and retrying the probe four times before reading the
    answer it already gave is three sleeps and a minute of nothing.
    """
    status, payload, headers = 0, {"error": "no attempt"}, Message()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        status, payload, headers = _req(method, url, **kw)
        if status not in retryable:
            return status, payload, headers
        if attempt < MAX_ATTEMPTS:
            wait = min(60, 3 * 2 ** (attempt - 1))
            # Only a failure is ever printed here: a 2xx returns above, so a successful
            # token exchange never reaches a line that would show the access token.
            print(f"!! {label} -> {status} {str(payload)[:120]}; "
                  f"retry {attempt}/{MAX_ATTEMPTS - 1} in {wait}s")
            time.sleep(wait)
    return status, payload, headers


def _api_error(status: int, payload: dict) -> str:
    """A Google API error as one line, with what the reason actually means."""
    error = payload.get("error")
    if not isinstance(error, dict):
        return f"HTTP {status} {str(payload)[:200]}"
    reason = str((error.get("errors") or [{}])[0].get("reason") or "")
    parts = [f"HTTP {status}", reason, str(error.get("message") or "")]
    if reason in HINTS:
        parts.append(f"— {HINTS[reason]}")
    return " ".join(p for p in parts if p)


def _oauth_error(status: int, payload: dict) -> str:
    """A token-exchange failure, built from named fields only.

    Never the whole body: echoing a response from an auth endpoint into a log is how a
    credential ends up in a scrollback, and `invalid_grant` needs no body to explain.
    """
    code = str(payload.get("error") or status)
    if code == "invalid_grant":
        return ("the YouTube refresh token was rejected (invalid_grant): it has expired, "
                "been revoked, or belongs to a different OAuth client. Run "
                "`creatoros init youtube` to grant access again.")
    detail = str(payload.get("error_description") or "")
    return f"token exchange failed: HTTP {status} {code} {detail}".strip()


def _plain(text: str) -> str:
    """Snippet text the API will accept: it rejects `<` and `>` outright.

    A title holding either fails with `invalidTitle` and no mention of which character did
    it, so it reads like a metadata bug rather than a stray chevron.
    """
    return text.replace("<", "").replace(">", "")


def _flag(value, key: str) -> bool:
    """A spec value that must be a boolean, read strictly.

    `bool("false")` is True, and this is how `made_for_kids: "false"` declared a video
    made for kids: a legal self-declaration to YouTube that then turns off its comments,
    notifications, personalised ads and end screens. Guessing at a string is not worth
    that, so anything outside the two sets is refused and names itself.
    """
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in TRUTHY:
        return True
    if text in FALSY:
        return False
    raise PublishError(f"platforms.youtube.{key} must be true or false, not {value!r}")


def _tags(tags) -> list[str]:
    kept, total = [], 0
    for tag in tags:
        tag = _plain(str(tag)).strip()[:LIMITS["tag"]]
        if not tag:
            continue
        if total + len(tag) > LIMITS["tags_total"]:
            break
        kept.append(tag)
        total += len(tag)
    return kept


def _rfc3339(at: str, tz_name: str) -> str:
    """`at` is naive wall time in `tz_name`; `publishAt` wants an instant in UTC."""
    try:
        moment = dt.datetime.fromisoformat(at)
    except ValueError as exc:
        raise PublishError(f"could not read the schedule time {at!r}: {exc}") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=sched.zone(tz_name))
    if moment <= dt.datetime.now(dt.timezone.utc):
        raise PublishError(
            f"publishAt {at!r} ({tz_name}) is in the past; YouTube rejects that. Publish "
            "with mode 'now' instead."
        )
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resume_point(received: str, size: int) -> int:
    """The next byte to send, from a 308's `Range: bytes=0-N` header.

    `N` is the last byte stored, so the next one is N+1. No `Range` at all means nothing
    arrived, which is a start from zero. A value past the end of the file is clamped to
    `size` rather than trusted: it can only mean the session holds everything.
    """
    if "-" not in received:
        return 0
    try:
        last = int(received.rsplit("-", 1)[1])
    except ValueError as exc:
        raise PublishError(f"could not read the resume position from {received!r}") from exc
    return min(last + 1, size)


def _fail(request: PublishRequest, error: str) -> PublishResult:
    print(f"publish failed: {error}")
    return PublishResult(ok=False, mode=request.mode, error=error, dry_run=request.dry_run,
                         outcomes=[PlatformOutcome("youtube", False, error=error)])


class YouTube:
    """The YouTube Data API v3, as a `Publisher`."""

    name = "youtube"
    platforms = ("youtube",)
    third_party = False
    records_publication = True

    def __init__(self) -> None:
        self._token = ""
        self._token_until = 0.0

    def available(self) -> Availability:
        """Whether the three OAuth values are present.

        Deliberately offline. `doctor` is the command people run when nothing works yet,
        often with no network to hand, and a refresh exchange is a round trip Google rate-
        limits. So a token revoked since it was granted is reported at publish time, where
        `_oauth_error` names `invalid_grant` and what to do about it.
        """
        missing = [name for name in ENV if not os.environ.get(name, "").strip()]
        if missing:
            return Availability.no(
                f"{', '.join(missing)} not set",
                "run `creatoros init youtube`, which does the Google consent once, or put "
                "the values in .env beside creatoros.yaml:\n"
                "    YOUTUBE_CLIENT_ID=...\n"
                "    YOUTUBE_CLIENT_SECRET=...\n"
                "    YOUTUBE_REFRESH_TOKEN=...",
            )
        return Availability.yes("OAuth client and refresh token set")

    def requires(self, platform: str) -> tuple[str, ...]:
        """The Data API rejects a video with no title, and a Short with no description or
        tags is invisible to search."""
        return ("title", "description", "tags") if platform == "youtube" else ()

    def publish(self, request: PublishRequest) -> PublishResult:
        print("!! the Data API has no containsSyntheticMedia field, so this upload carries "
              "no AI-narration disclosure. Tick Studio > Edit > Details > Altered content, "
              "or publish through a backend that can send it.")
        if "youtube" not in request.platforms:
            return _fail(request, f"this backend posts to youtube only, not "
                                  f"{', '.join(request.platforms) or '(nothing)'}")
        video = Path(request.video).expanduser()
        if not video.is_file():
            return _fail(request, f"no such file: {video}")
        size = video.stat().st_size
        if not size:
            return _fail(request, f"{video.name} is 0 bytes")
        if size > LIMITS["bytes"]:
            return _fail(request, f"{size / 1e9:.1f} GB is over YouTube's 256 GB limit")

        spec = request.spec or load_spec(str(video))
        meta = (spec.get("platforms") or {}).get("youtube") or {}
        try:
            snippet = self._snippet(spec, meta, video)
            status = self._status(request, meta)
        except PublishError as exc:
            return _fail(request, str(exc))
        print(f"title:   {snippet['title']!r} ({len(snippet['title'])} chars)")
        print(f"tags:    {snippet['tags']}")
        print(f"status:  {status}")

        if request.dry_run:
            print(json.dumps({"snippet": snippet, "status": status, "bytes": size},
                             indent=2, ensure_ascii=False))
            return PublishResult(ok=True, mode=request.mode, dry_run=True,
                                 outcomes=[PlatformOutcome("youtube", True)])

        try:
            token = self._access_token()
            session = self._begin(token, snippet, status, size)
            video_id = self._send(session, video, size, token)
        except PublishError as exc:
            return _fail(request, str(exc))
        url = f"https://www.youtube.com/watch?v={video_id}"
        print(f"uploaded {size / 1e6:.1f} MB -> {url}")
        return PublishResult(ok=True, mode=request.mode, post_id=video_id,
                             outcomes=[PlatformOutcome("youtube", True, url=url,
                                                       post_id=video_id)])

    def _snippet(self, spec: dict, meta: dict, video: Path) -> dict:
        """The `snippet` part: what the spec says, trimmed to what the API accepts."""
        caption_file = video.with_suffix(".caption.txt")
        caption = caption_file.read_text(encoding="utf-8").strip() if caption_file.is_file() else ""
        if not meta.get("title"):
            print("!! no platforms.youtube.title in the spec — deriving one from the hook, "
                  "which makes a weak search title. See "
                  "prompts/playbooks/03-platform-metadata.md")
        title = str(meta.get("title") or derive_title(caption, video.stem, spec))
        description = str(meta.get("description") or caption)
        if len(description) > LIMITS["description"]:
            print(f"!! description is {len(description)} chars; truncating to "
                  f"{LIMITS['description']}")
        return {
            "title": _plain(title)[:LIMITS["title"]],
            "description": _plain(description)[:LIMITS["description"]],
            "tags": _tags(meta.get("tags") or derive_tags(caption)),
            "categoryId": str(meta.get("category_id") or meta.get("categoryId")
                              or CATEGORY_SCI_TECH),
        }

    def _status(self, request: PublishRequest, meta: dict) -> dict:
        """The `status` part, which is where this API punishes a wrong guess.

        `asked` is what the spec said and `wanted` is what gets sent, kept apart because
        an *explicit* visibility that the mode cannot honour is a conflict to report,
        while the same value arrived at by default is just the default.
        """
        asked = str(meta.get("visibility") or meta.get("privacyStatus") or "").strip().lower()
        wanted = asked or "public"
        if wanted not in ("public", "unlisted", "private"):
            raise PublishError(f"privacyStatus must be public, unlisted or private, "
                               f"not {wanted!r}")
        # Both spellings, as with category_id/categoryId above: the camelCase one is what
        # the API calls it and what the older per-platform JSON files hold.
        kids_key = "made_for_kids" if "made_for_kids" in meta else "madeForKids"
        kids = meta.get(kids_key)
        status = {"privacyStatus": "private",
                  "selfDeclaredMadeForKids": False if kids is None
                  else _flag(kids, kids_key)}
        if request.mode == DRAFT:
            print("YouTube has no draft: uploading as private. Publish it from Studio when "
                  "you have watched it, or re-run with mode schedule or now.")
            if asked and asked != "private":
                print(f"!! platforms.youtube.visibility is {asked!r}, but mode 'draft' "
                      f"uploads private whatever it says. Use mode 'now' to upload {asked}.")
        elif request.mode == SCHEDULE:
            if not request.at:
                raise PublishError("mode 'schedule' needs a timestamp")
            # A scheduled upload goes up private and YouTube makes it *public* at
            # publishAt; there is no "private at publishAt" and no scheduled unlisted.
            # So `visibility: private` plus `mode: schedule` used to upload private, send
            # the schedule, and hand someone who had asked for a private video a public
            # one — silently, because privacyStatus in the payload did say private. The
            # combination is refused rather than reinterpreted: dropping publishAt instead
            # would unschedule a publish the user did ask for, which is the same bug
            # pointing the other way.
            if asked in ("private", "unlisted"):
                raise PublishError(
                    f"platforms.youtube.visibility is {asked!r} and mode is 'schedule', "
                    f"but YouTube makes a scheduled video public at publishAt — it cannot "
                    f"schedule one to become {asked}. Either drop the visibility key to "
                    f"schedule it public, or use mode 'draft', which uploads it private "
                    f"and leaves it for you to publish from Studio.")
            status["publishAt"] = _rfc3339(request.at, request.tz)
            print(f"private until {status['publishAt']}, then public")
        elif request.mode == NOW:
            status["privacyStatus"] = wanted
        else:
            raise PublishError(f"unknown mode {request.mode!r}; "
                               f"expected draft, schedule or now")
        return status

    def _access_token(self) -> str:
        """A bearer token, exchanged once per process rather than once per video."""
        if self._token and time.monotonic() < self._token_until:
            return self._token
        missing = [name for name in ENV if not os.environ.get(name, "").strip()]
        if missing:
            raise PublishError(f"{', '.join(missing)} not set. Run `creatoros init youtube`, "
                               "or add them to .env beside creatoros.yaml.")
        # The form carries the client secret and the refresh token. Neither is ever printed:
        # `_call` logs its label and a response body, never a request.
        status, payload, _ = _call(
            "POST", TOKEN_URL, label="token exchange",
            form={"client_id": os.environ["YOUTUBE_CLIENT_ID"].strip(),
                  "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"].strip(),
                  "refresh_token": os.environ["YOUTUBE_REFRESH_TOKEN"].strip(),
                  "grant_type": "refresh_token"})
        if status != 200 or not payload.get("access_token"):
            raise PublishError(_oauth_error(status, payload))
        self._token = str(payload["access_token"])
        # Google issues an hour. A minute of slack, so a long upload is not begun on a
        # token that expires half way through it.
        lifetime = max(120, int(payload.get("expires_in") or 3600))
        self._token_until = time.monotonic() + lifetime - 60
        return self._token

    def _begin(self, token: str, snippet: dict, status: dict, size: int) -> str:
        """Open an upload session and return its URI."""
        code, payload, headers = _call(
            "POST", UPLOAD_URL, label="upload session", token=token,
            json_body={"snippet": snippet, "status": status},
            headers={"X-Upload-Content-Length": str(size),
                     "X-Upload-Content-Type": "video/mp4"})
        if code != 200:
            raise PublishError(f"could not open an upload session: {_api_error(code, payload)}")
        session = headers.get("Location", "")
        if not session:
            raise PublishError("the upload session came back without a Location header, so "
                               "there is nowhere to send the bytes")
        return session

    def _send(self, session: str, video: Path, size: int, token: str) -> str:
        """PUT the file into an open session, resuming rather than restarting."""
        offset, code, payload = 0, 0, {"error": "no attempt"}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            code, payload, _ = self._put(session, video, size, offset, token)
            if code in (200, 201):
                video_id = str(payload.get("id") or "")
                if not video_id:
                    raise PublishError(f"the upload finished with no video id: "
                                       f"{str(payload)[:200]}")
                return video_id
            if code not in RETRYABLE:
                break
            final = attempt == MAX_ATTEMPTS
            # A 308 is not a server complaint, it is "you stopped early", so there is
            # nothing to wait out before picking up where the bytes ran out.
            if code != 308 and not final:
                wait = min(60, 3 * 2 ** (attempt - 1))
                print(f"!! upload -> {code} {str(payload)[:120]}; "
                      f"retry {attempt}/{MAX_ATTEMPTS - 1} in {wait}s")
                time.sleep(wait)
            # The probe runs on the last attempt as well. Giving up without it skipped the
            # one case resumption exists for — every byte arrived and only the response was
            # lost — precisely when the run stops trying, so a video that was fully
            # uploaded was reported as failed and uploaded again by the next run.
            offset, video_id = self._probe(session, size, token)
            if video_id:
                # Every byte had arrived; only the answer was lost. Starting over here is
                # what publishes the same video twice.
                return video_id
            if final:
                break
            print(f"resuming at {offset / 1e6:.1f} of {size / 1e6:.1f} MB "
                  f"({100 * offset / size:.0f}%)")
        raise PublishError(f"upload failed at byte {offset} of {size}: "
                           f"{_api_error(code, payload)}")

    def _put(self, session: str, video: Path, size: int, offset: int,
             token: str) -> tuple[int, dict, Message]:
        headers = {"Content-Length": str(size - offset), "Content-Type": "video/mp4"}
        if offset:
            # Only a resumed PUT carries a Content-Range: it says which bytes these are, so
            # the ones already stored are not written a second time.
            headers["Content-Range"] = f"bytes {offset}-{size - 1}/{size}"
        # The handle is passed through rather than read: a 50 MB file does not need to be
        # in memory to be sent, and seeking is how a resumed attempt starts where it left off.
        with video.open("rb") as handle:
            handle.seek(offset)
            return _req("PUT", session, token=token, data=handle, headers=headers,
                        timeout=UPLOAD_TIMEOUT)

    def _probe(self, session: str, size: int, token: str) -> tuple[int, str]:
        """Ask the session how much it holds. Returns (next byte to send, video id if done).

        A completed upload does not always answer `200` with an id. While YouTube finalises
        one it answers `308` with a `Range` covering the whole file, which is not an error
        and not something to resume: every byte is there and the id is moments away. That
        answer used to come back as `(size, "")`, and the caller, assuming a finished
        upload is always `200`, sent a PUT from byte `size` — an inverted `Content-Range`
        the API rejects with 400. So an upload that had succeeded failed hard and was
        uploaded a second time. The wait below is the whole fix: there is nothing to send,
        so keep asking for the id.
        """
        waits = list(FINALISE_WAITS)
        while True:
            code, payload, headers = _call(
                "PUT", session, label="resume probe", retryable=RETRYABLE - {308},
                token=token, data=b"",
                headers={"Content-Length": "0", "Content-Range": f"bytes */{size}"})
            if code in (200, 201):
                video_id = str(payload.get("id") or "")
                if not video_id:
                    raise PublishError("the session says the upload is complete but names "
                                       "no video; look for it in Studio before re-running")
                return size, video_id
            if code in (404, 410):
                raise PublishError("the upload session is gone (YouTube keeps one for about "
                                   "a week): re-run to open a fresh one and upload again")
            if code != 308:
                raise PublishError(f"could not ask where the upload got to: "
                                   f"{_api_error(code, payload)}")
            offset = _resume_point(headers.get("Range", ""), size)
            if offset < size:
                return offset, ""
            if not waits:
                # Still unnamed after the waits. Reported as a failure, because it is one —
                # but the message has to say that the bytes arrived: this is the one error
                # in this backend where re-running is what publishes the video twice.
                raise PublishError(
                    f"all {size} bytes reached YouTube and the upload session has still "
                    "not named the video. It is most likely processing and will appear in "
                    "Studio: look there before re-running, because another run uploads it "
                    "again.")
            pause = waits.pop(0)
            print(f"all {size / 1e6:.1f} MB are stored; waiting {pause}s for YouTube to "
                  "finish the upload and name the video")
            time.sleep(pause)
