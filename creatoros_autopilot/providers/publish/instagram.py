# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reels straight to Instagram, with nobody in between.

This is the publisher that needs no paid third party: an Instagram Business or Creator
account linked to a Facebook Page, a token, and that is the bill. It is also the one with
a constraint the others do not have, so the constraint comes first.

**The Graph API never takes your bytes.** `POST /{ig-user-id}/media` takes a `video_url`
and Instagram's own servers fetch it; there is no upload endpoint for Reels. A file on
your laptop therefore cannot be published this way at all — not slowly, not with a flag.
Zernio hides this by presigning a bucket and uploading for you, which is the real reason
direct Instagram publishing is harder than Zernio even though it is cheaper. Here the
video has to be reachable from the internet before `publish()` is called, and the job of
this module is to turn a rendered path into that URL honestly:

  * `platforms.instagram.video_url` in the spec wins, for a renderer that already knows
    where its output is served;
  * otherwise a local path is mapped through `public_base_url` and the local root it
    serves (`CREATOROS_PUBLIC_BASE_URL`, `CREATOROS_PUBLIC_ROOT`) — rsync the output
    folder to a bucket or a static host, point those two at it, and a path becomes a URL;
  * otherwise the publish is refused, with those instructions. The alternative is handing
    Instagram a path it cannot fetch and getting back "media fetch error" thirty seconds
    later, which reads like a permissions problem and is not one.

**Creating a Reel is three calls, and the middle one is where people lose a day.** The
container returned by step one is processed asynchronously and can take minutes for a
heavy file. Calling `media_publish` before `status_code` reaches `FINISHED` fails, and it
fails with a generic error rather than "not ready yet" — so the obvious implementation,
two calls in a row, works on a short test clip and then breaks on a real one. Step two
polls with backoff up to `poll_timeout`, and reports a container still `IN_PROGRESS`
differently from one that came back `ERROR`: the first is worth waiting on again with the
id this prints, the second never will be.

`media_publish` is the one call that is never retried. Instagram has no idempotency key,
so a retry after a dropped socket may publish a Reel that already went live, and a
duplicate post costs reach on an account that cannot delete-and-repost cleanly. A failure
there returns the container id instead, for a human to check and finish.

Two things the Graph API simply does not have: **scheduled publishing** for Reels, and a
draft state. Both modes are refused here rather than approximated, because both plausible
approximations are worse than an error.

`mode: schedule` cannot be honoured at all: a container goes live when `media_publish` is
called and not before. Refusing it was only half the job, though. With the shipped
`offset_days: 1` every slot is a day away, so `core.schedule.decide()` returns `schedule`
for every video and this backend refuses all of them — and the fix it printed, "let the OS
job publish at the slot time with `mode: now`", is not something a config file can say:
`publish.mode` is `draft` or `auto`, and the installed job runs `creatoros run --if-due`
once a day. The error now names the two settings that do work, because an error whose
instructions cannot be followed is the same as no error at all.

`mode: draft` looks honourable — create the container, validate it, publish nothing — and
that is what this module did. But a container is not a draft: it is invisible in the app
and expires in 24 hours, and it came back `ok`, so the ledger recorded instagram as
published, the slot was marked done, and the real publish skipped that job forever. A
day's Reels went nowhere while the run printed `published: 1`. So draft refuses too, and
names what does work: `local_folder` for a practice run that records nothing, Zernio for a
genuinely parked draft, `offset_days: 0` to post from here.

The token goes in an `Authorization` header, never in a query string, so it cannot end up
in a printed URL or in the text of a transport error.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ...interfaces.availability import Availability
from ...interfaces.publish import (
    DRAFT,
    NOW,
    SCHEDULE,
    PlatformOutcome,
    PublishRequest,
    PublishResult,
)

HOST = "https://graph.facebook.com"
# Pinned, because a Graph version reaching end of life changes behaviour under you. The
# override exists so that outliving this pin does not need a release.
DEFAULT_VERSION = "v21.0"
CAPTION_MAX = 2200
POLL_FIRST = 5.0
POLL_MAX = 30.0
POLL_TIMEOUT = 900.0
# Status 0 is this module's code for a transport failure rather than an HTTP answer.
RETRYABLE = {0, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4

ACCOUNT_REQUIREMENT = (
    "The account must be an Instagram Business or Creator account linked to a Facebook "
    "Page; a personal Instagram account cannot use this API at all."
)

#: The only two ways this backend ever posts, because the Graph API publishes immediately
#: or not at all. Both refusals in `publish()` quote it, so they cannot drift apart and
#: start recommending different things for the same impossibility.
POST_FROM_HERE = (
    "set publish.offset_days: 0 with publish.mode: auto, so the day's run reaches a slot "
    "time already behind it and decides `now`; or run the publish at the slot time "
    "yourself with `creatoros publish <video> --mode now` (add --dry-run to see what "
    "would be sent without sending it)"
)


def _decode(payload: bytes) -> dict:
    try:
        parsed = json.loads(payload)
    except Exception:
        return {"error": {"message": payload[:400].decode(errors="replace")}}
    return parsed if isinstance(parsed, dict) else {"data": parsed}


def _error(status: int, payload: dict) -> str:
    """A Graph error as one line, keeping the fields support asks for.

    `fbtrace_id` is the only thing Meta's own support will act on, and `code` 190 versus
    100 is the difference between "your token died" and "that is not the right id", so
    neither is thrown away.
    """
    err = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(err, dict):
        return f"HTTP {status}: {str(payload)[:300]}"
    parts = [str(err.get("message") or "unknown error")]
    parts += [f"{k}={err[k]}" for k in ("code", "error_subcode", "fbtrace_id") if err.get(k)]
    return f"HTTP {status}: " + " ".join(parts)


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return cut[:space] if space > limit // 2 else cut


class Instagram:
    """Reels through the Instagram Graph API.

    Credentials are read on every call rather than in `__init__`, so a token rotated
    while a long-running process is up is picked up without restarting it, and so that
    constructing this object touches nothing.
    """

    name = "instagram"
    platforms = ("instagram",)
    third_party = False
    records_publication = True

    def __init__(self, *, access_token: str = "", business_account_id: str = "",
                 public_base_url: str = "", public_root: str = "", api_version: str = "",
                 share_to_feed: bool = True, poll_timeout: float = POLL_TIMEOUT) -> None:
        self._access_token = access_token
        self._business_account_id = business_account_id
        self._public_base_url = public_base_url
        self._public_root = public_root
        self._api_version = api_version
        self.share_to_feed = share_to_feed
        self.poll_timeout = poll_timeout

    # ------------------------------------------------------------------ configuration
    def _token(self) -> str:
        return (self._access_token or os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")).strip()

    def _account(self) -> str:
        return (self._business_account_id
                or os.environ.get("INSTAGRAM_BUSINESS_ACCOUNT_ID", "")).strip()

    def _base_url(self) -> str:
        return (self._public_base_url
                or os.environ.get("CREATOROS_PUBLIC_BASE_URL", "")).strip().rstrip("/")

    def _root(self) -> str:
        return (self._public_root or os.environ.get("CREATOROS_PUBLIC_ROOT", "")).strip()

    @property
    def api(self) -> str:
        version = (self._api_version
                   or os.environ.get("INSTAGRAM_GRAPH_VERSION", "")).strip() or DEFAULT_VERSION
        return f"{HOST}/{version}"

    # ------------------------------------------------------------------ transport
    def _call(self, method: str, path: str, token: str, params: dict | None = None,
              timeout: float = 60.0) -> tuple[int, dict]:
        url = f"{self.api}/{path.lstrip('/')}"
        headers = {"Accept": "application/json", "Authorization": "Bearer " + token}
        data = None
        if params and method == "POST":
            data = urllib.parse.urlencode(params).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, _decode(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, _decode(exc.read())
        except (urllib.error.URLError, OSError) as exc:
            return 0, {"error": {"message": f"{type(exc).__name__}: {exc}"}}

    def _retrying(self, method: str, path: str, token: str, params: dict | None = None,
                  timeout: float = 60.0, label: str = "") -> tuple[int, dict]:
        """`_call` with backoff. Only for calls that are safe to repeat."""
        status, payload = 0, {"error": {"message": "no attempt"}}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            status, payload = self._call(method, path, token, params, timeout)
            if status not in RETRYABLE:
                return status, payload
            if attempt < MAX_ATTEMPTS:
                wait = min(60, 3 * 2 ** (attempt - 1))
                print(f"!! {label or path} -> {_error(status, payload)[:140]}; "
                      f"retry {attempt}/{MAX_ATTEMPTS - 1} in {wait}s")
                time.sleep(wait)
        return status, payload

    # ------------------------------------------------------------------ availability
    def available(self) -> Availability:
        token, account = self._token(), self._account()
        missing = [name for name, value in (("INSTAGRAM_ACCESS_TOKEN", token),
                                            ("INSTAGRAM_BUSINESS_ACCOUNT_ID", account))
                   if not value]
        if missing:
            return Availability.no(
                f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} unset",
                f"Put {' and '.join(f'{name}=...' for name in missing)} in .env beside "
                f"creatoros.yaml. " + ACCOUNT_REQUIREMENT + " The id is the "
                "instagram_business_account of your Page: GET /me/accounts"
                "?fields=instagram_business_account",
            )

        status, payload = self._call("GET", account, token,
                                     {"fields": "username,media_count"}, timeout=20.0)
        if status == 200:
            username = payload.get("username") or account
            return Availability.yes(f"@{username}, {payload.get('media_count', '?')} posts")
        if status == 0:
            # The keys are there and may well be fine; this check cannot tell. Reported as
            # unavailable because publishing needs the same network, not as a key problem.
            return Availability.no(
                "cannot reach graph.facebook.com",
                # `publish.backend` (singular) is not a setting: settings reject unknown
                # keys, so the old wording here failed with a config error of its own.
                "Retry when connected. To work offline, set publish.backends: "
                "[local_folder], which writes the video and the metadata and publishes "
                "nothing.",
            )
        detail = _error(status, payload)
        code = (payload.get("error") or {}).get("code") if isinstance(payload, dict) else None
        if code == 190:
            return Availability.no(
                detail,
                "INSTAGRAM_ACCESS_TOKEN is rejected or expired - a long-lived user token "
                "lasts about 60 days. Generate a new one and replace it in .env.",
            )
        return Availability.no(
            detail,
            "Check INSTAGRAM_BUSINESS_ACCOUNT_ID is the Instagram user id, not the Page "
            "id or the @handle (GET /me/accounts?fields=instagram_business_account), and "
            "that the token holds instagram_content_publish. " + ACCOUNT_REQUIREMENT,
        )

    def requires(self, platform: str) -> tuple[str, ...]:
        """The caption, and nothing else. A Reel without one gets no reach.

        `video_url` is deliberately not required: mapping a rendered path through
        `public_base_url` is the common case, and a spec that had to carry the URL could
        only be written after the file was already served.
        """
        return ("caption",) if platform == "instagram" else ()

    # ------------------------------------------------------------------ publishing
    def _video_url(self, video: str) -> tuple[str, str]:
        """(url, error) for the `video_url` Instagram will fetch."""
        if video.startswith(("http://", "https://")):
            return video, ""
        if re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*://", video):
            return "", (f"{video!r} is not something Instagram can fetch: video_url must "
                        "be an http(s) URL. A file:// URL is still a local file to it.")

        path = Path(video).expanduser()
        if not path.is_file():
            return "", f"no such file: {path}"
        base, root = self._base_url(), self._root()
        if not base or not root:
            return "", (
                f"{path} is a local file, and the Instagram Graph API cannot be given "
                "bytes: POST /media takes a video_url that Instagram's servers fetch "
                "themselves, and Reels have no upload endpoint.\n"
                "Serve the output folder somewhere public, then set both of "
                "CREATOROS_PUBLIC_BASE_URL=https://media.example.com and "
                f"CREATOROS_PUBLIC_ROOT={path.parent} (the local folder that URL serves).\n"
                "Or put the URL in the spec as platforms.instagram.video_url, or publish "
                "through the zernio backend, which uploads the file for you."
            )
        if not base.startswith(("http://", "https://")):
            return "", ("CREATOROS_PUBLIC_BASE_URL must be the http(s) URL that serves "
                        f"CREATOROS_PUBLIC_ROOT, such as https://media.example.com "
                        f"(got {base!r})")
        try:
            relative = path.resolve().relative_to(Path(root).expanduser().resolve())
        except ValueError:
            return "", (f"{path} is not under CREATOROS_PUBLIC_ROOT ({root}), so there is "
                        "no public URL for it. Render into that folder, or point the root "
                        "at the folder you actually serve.")
        return f"{base}/{urllib.parse.quote(relative.as_posix())}", ""

    def _caption(self, request: PublishRequest, meta: dict) -> tuple[str, str]:
        """(caption, error). The spec's caption, else the video's `.caption.txt` sibling.

        An empty caption is refused rather than posted: a Reel with no caption gets no
        reach and cannot be found again, and a blank one is not recoverable after the
        fact. The Zernio backend refuses for the same reason.
        """
        caption = str(meta.get("caption") or "").strip()
        sidecar = None
        if not caption and not request.video.startswith(("http://", "https://")):
            sidecar = Path(request.video).expanduser().with_suffix(".caption.txt")
            if sidecar.is_file():
                caption = sidecar.read_text(encoding="utf-8").strip()
        if not caption:
            where = f", or write one into {sidecar}" if sidecar else ""
            return "", ("refusing to post a Reel with an empty caption: the spec has no "
                        f"platforms.instagram.caption. Add it there{where}.")
        if len(caption) > CAPTION_MAX:
            print(f"!! caption is {len(caption)} chars; Instagram's limit is {CAPTION_MAX}, "
                  "so it will be cut at a word boundary")
            caption = _trim(caption, CAPTION_MAX)
        return caption, ""

    def _wait(self, creation_id: str, token: str) -> tuple[str, str]:
        """Poll a container until it settles. Returns (state, detail).

        State is one of finished, error, timeout. `timeout` is not a failure of the
        container — a long Reel really can take minutes — so it is reported separately,
        with the id, because the same container can be published later.
        """
        deadline = time.monotonic() + self.poll_timeout
        wait, last = POLL_FIRST, "IN_PROGRESS"
        while time.monotonic() < deadline:
            time.sleep(min(wait, max(0.0, deadline - time.monotonic())))
            wait = min(POLL_MAX, wait * 1.5)
            status, payload = self._retrying("GET", creation_id, token,
                                             {"fields": "status_code,status"},
                                             timeout=30.0, label="status")
            if status != 200:
                return "error", f"could not read container status: {_error(status, payload)}"
            last = str(payload.get("status_code") or "")
            if last == "FINISHED":
                return "finished", ""
            if last in ("ERROR", "EXPIRED"):
                # `status` carries the human-readable reason; status_code alone does not
                # distinguish a 90-second Reel from a file Instagram could not download.
                return "error", f"container {last}: {payload.get('status') or 'no detail'}"
            print(f"container {creation_id} {last or 'unknown'}; waiting")
        return "timeout", (f"container {creation_id} was still {last} after "
                           f"{self.poll_timeout:g}s of processing")

    def publish(self, request: PublishRequest) -> PublishResult:
        def failed(error: str) -> PublishResult:
            return PublishResult(ok=False, mode=request.mode, error=error,
                                 dry_run=request.dry_run,
                                 outcomes=[PlatformOutcome("instagram", False, error=error)])

        # Both refusals come before the dry run deliberately. A dry run that passes in a
        # mode this backend can never honour is worse than no dry run at all: it is the
        # one check that would have caught a day of Reels going nowhere, and it agreed.
        if request.mode == SCHEDULE:
            slot = f" for {request.at}" if request.at else ""
            return failed(
                "the Instagram Graph API has no scheduled publish for Reels: a container "
                "goes live when media_publish is called and not before, and it expires 24 "
                f"hours after it finishes, so there is nothing to park{slot}. With "
                "publish.offset_days: 1 every slot is a day away, which is why this "
                "refuses every video and not just some.\n"
                f"To publish from here, {POST_FROM_HERE}.\n"
                "To keep publishing onto a later day's slots, use a backend that really "
                "schedules: publish.backends: [zernio] sends scheduledFor."
            )
        if request.mode == DRAFT:
            # The Graph API can create a container and not publish it, which this backend
            # used to report as ok. It is not a draft in any sense a user means: nobody
            # can see it, nothing can be reviewed, and the ledger row it earned marked
            # instagram done, so the real publish skipped the job until the container had
            # expired. A refusal leaves the job pending, which is the recoverable state.
            return failed(
                "Instagram has no draft. The Graph API can create a Reel container and "
                "leave it unpublished, but that container is invisible in the app and "
                "expires in 24 hours, so counting it as published would mark instagram "
                "done in the ledger while nothing was ever posted. Nothing was created.\n"
                "For a practice run that records nothing, set publish.backends: "
                "[local_folder]. For a post parked as a genuine draft you can review, "
                f"publish.backends: [zernio]. To post from here, {POST_FROM_HERE}."
            )
        if request.mode != NOW:
            return failed(f"unknown mode {request.mode!r}: expected draft, schedule or now")
        if "instagram" not in request.platforms:
            return failed(f"this backend only reaches instagram, not {list(request.platforms)}")

        # A renderer that uploads its own output knows the URL already; the spec is where
        # it says so, and it beats anything this module can infer from a path.
        meta = (request.spec.get("platforms") or {}).get("instagram") or {}
        video_url, error = self._video_url(str(meta.get("video_url") or "").strip()
                                           or request.video)
        if error:
            return failed(error)
        caption, error = self._caption(request, meta)
        if error:
            return failed(error)

        share_to_feed = bool(meta.get("share_to_feed", self.share_to_feed))
        if request.dry_run:
            # Everything that can be decided without calling Instagram has been decided
            # by now, which is the only thing that makes a dry run worth running: the URL
            # mapping and the caption are where a real publish fails.
            have_keys = bool(self._token() and self._account())
            print(json.dumps({"platform": "instagram", "mode": request.mode,
                              "job_id": request.job_id, "video_url": video_url,
                              "caption_chars": len(caption), "share_to_feed": share_to_feed,
                              "credentials": "set" if have_keys else "unset"},
                             indent=2, ensure_ascii=False))
            if not have_keys:
                print("!! a dry run does not need the keys; a real publish will stop "
                      "without them. Run `creatoros doctor` to see which are missing.")
            return PublishResult(ok=True, mode=request.mode, dry_run=True,
                                 outcomes=[PlatformOutcome("instagram", True)])

        token, account = self._token(), self._account()
        if not token or not account:
            check = self.available()
            return failed(f"{check.detail}. {check.fix}")

        status, payload = self._retrying(
            "POST", f"{account}/media", token,
            {"media_type": "REELS", "video_url": video_url, "caption": caption,
             "share_to_feed": "true" if share_to_feed else "false"},
            label="create container",
        )
        if status not in (200, 201) or not payload.get("id"):
            return failed(f"creating the Reel container failed: {_error(status, payload)}")
        creation_id = str(payload["id"])
        print(f"container {creation_id} created; Instagram is fetching {video_url}")

        state, detail = self._wait(creation_id, token)
        if state == "timeout":
            return failed(f"{detail}. Nothing has been published. The container id is "
                          f"{creation_id}, and it stays publishable for 24 hours once it "
                          "reaches FINISHED, so this is worth checking rather than "
                          "re-rendering.")
        if state != "finished":
            return failed(detail)

        # Deliberately not retried: Instagram offers no idempotency key, so a second
        # attempt after a dropped socket can publish a Reel that already went live.
        status, payload = self._call("POST", f"{account}/media_publish", token,
                                     {"creation_id": creation_id}, timeout=120.0)
        if status not in (200, 201) or not payload.get("id"):
            return failed(
                f"publishing container {creation_id} failed: {_error(status, payload)}. "
                "Check the account before retrying - this call is not repeated "
                "automatically, because a retry can duplicate a Reel that did go live."
            )
        media_id = str(payload["id"])
        return PublishResult(ok=True, mode=NOW, post_id=media_id,
                             outcomes=[PlatformOutcome("instagram", True, post_id=media_id,
                                                       url=self._permalink(media_id, token))])

    def _permalink(self, media_id: str, token: str) -> str:
        """The Reel's own URL. Absent on failure: the post exists either way."""
        status, payload = self._call("GET", media_id, token, {"fields": "permalink"},
                                     timeout=20.0)
        return str(payload.get("permalink") or "") if status == 200 else ""
