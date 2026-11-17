# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one Zernio HTTP client.

There used to be four. `publish_zernio.py`, `engine/zernio.py`, `publish_queue.py` and
`promote_instagram_backlog.py` each carried their own auth, retry policy, limits table and
presign/upload/post sequence — about 1,170 lines between them — and they had already
drifted. Only one of them treated a broken pipe mid-upload as retryable, which is why a
dropped socket during a 10 MB PUT cost a day's first Short before anyone noticed; only one
knew that `publishNow` is asynchronous for video.

Everything that talks to Zernio now goes through `ZernioAPI`. The publisher plugin, the
batch queue and the two promotion tools are callers, not reimplementations.

Secrets: the key comes from `ZERNIO_API_KEY` in the environment and is never logged,
printed or put in an exception message. Zernio allows two connected accounts per Zernio
account, so a setup spanning more platforms needs more keys — hence `key_env`.
"""
from __future__ import annotations

import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

DEFAULT_BASE = "https://zernio.com/api/v1"

# 0 is this module's code for "the connection itself failed" — a dropped socket or a broken
# pipe part-way through an upload, which arrives as an exception rather than a status. It is
# retryable for the same reason a 503 is: nothing was committed on the other end.
RETRYABLE = {0, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4

LIMITS = {
    "instagram": {"caption": 2200, "bytes": 300 * 1024 * 1024},
    "youtube": {"caption": 5000, "bytes": 256 * 1024 * 1024 * 1024, "title": 100},
}


class ZernioError(RuntimeError):
    """A Zernio call that failed in a way the caller cannot retry past."""


class ZernioAPI:
    """A thin, retrying client. No business logic — that belongs to its callers."""

    def __init__(self, api_key: str | None = None, base: str | None = None,
                 key_env: str = "ZERNIO_API_KEY", timeout: float = 600):
        self._key = (api_key or "").strip()
        self.key_env = key_env
        self.base = (base or os.environ.get("ZERNIO_API_BASE") or DEFAULT_BASE).rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------------ auth
    @property
    def key(self) -> str:
        key = self._key or os.environ.get(self.key_env, "").strip()
        if not key:
            raise ZernioError(
                f"{self.key_env} is not set. Put it in .env beside creatoros.yaml."
            )
        return key

    def has_key(self) -> bool:
        return bool(self._key or os.environ.get(self.key_env, "").strip())

    # ------------------------------------------------------------- transport
    def request(self, method: str, url: str, *, body=None, raw: bytes | None = None,
                ctype: str | None = None, idem: str | None = None,
                authenticated: bool = True) -> tuple[int, object]:
        """One call. HTTP errors come back as a status, they are not raised."""
        headers: dict[str, str] = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if raw is not None:
            data = raw
            headers["Content-Type"] = ctype or "application/octet-stream"
        if authenticated:
            headers["Authorization"] = "Bearer " + self.key
            if idem:
                headers["Idempotency-Key"] = idem
        if not url.startswith("http"):
            url = f"{self.base}/{url.lstrip('/')}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = resp.read()
                return resp.status, (json.loads(payload) if payload.strip().startswith(b"{")
                                     or payload.strip().startswith(b"[") else payload[:200])
        except urllib.error.HTTPError as exc:
            detail = exc.read()
            try:
                detail = json.loads(detail)
            except Exception:
                detail = detail[:500].decode(errors="replace")
            return exc.code, detail
        except (urllib.error.URLError, OSError) as exc:
            return 0, {"error": f"{type(exc).__name__}: {exc}"}

    def call(self, method: str, url: str, *, label: str = "", **kw) -> tuple[int, object]:
        """`request` with backoff on transport failures and transient server errors."""
        status, payload = 0, {"error": "no attempt"}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            status, payload = self.request(method, url, **kw)
            if status not in RETRYABLE:
                return status, payload
            if attempt < MAX_ATTEMPTS:
                wait = min(60, 3 * (2 ** (attempt - 1)))
                print(f"!! {label or url} -> {status} {str(payload)[:120]}; "
                      f"retry {attempt}/{MAX_ATTEMPTS - 1} in {wait}s")
                time.sleep(wait)
        return status, payload

    def _ok(self, method: str, url: str, *, expect=(200, 201), label: str = "", **kw):
        status, payload = self.call(method, url, label=label, **kw)
        if status not in expect:
            raise ZernioError(f"{label or url} -> HTTP {status} {str(payload)[:400]}")
        return payload

    # ----------------------------------------------------------- operations
    def accounts(self) -> list[dict]:
        payload = self._ok("GET", "/accounts", label="accounts")
        if isinstance(payload, list):
            return payload
        return payload.get("accounts") or payload.get("data") or []

    def account_ids(self) -> dict[str, str]:
        """platform -> account id, for whatever this key can reach."""
        return {a.get("platform"): (a.get("_id") or a.get("id")) for a in self.accounts()
                if a.get("platform")}

    def upload(self, path: str | Path) -> dict:
        """Presign and PUT the bytes. Returns the presign payload with `publicUrl`."""
        path = Path(path)
        size = path.stat().st_size
        ctype = mimetypes.guess_type(str(path))[0] or "video/mp4"
        pre = self._ok("POST", "/media/presign",
                       body={"filename": path.name, "contentType": ctype, "size": size},
                       idem=str(uuid.uuid4()), expect=(200,), label="presign")
        blob = path.read_bytes()
        # The presigned PUT goes to object storage, which does not take our bearer token.
        self._ok("PUT", pre["uploadUrl"], raw=blob, ctype=ctype,
                 authenticated=False, expect=(200, 201), label="upload")
        return pre

    def create_post(self, payload: dict) -> tuple[int, dict]:
        """207 is a real outcome here — created, but a platform inside it did not publish."""
        status, res = self.call("POST", "/posts", body=payload,
                                idem=str(uuid.uuid4()), label="create")
        if status not in (200, 201, 207):
            raise ZernioError(f"POST /posts -> HTTP {status} {json.dumps(res, default=str)[:900]}")
        return status, (res if isinstance(res, dict) else {})

    def get_post(self, post_id: str) -> dict:
        return self._ok("GET", f"/posts/{post_id}", expect=(200,), label=f"post {post_id}")

    def list_posts(self, *, limit: int = 100, sort: str = "created-asc",
                   status: str | None = None) -> list[dict]:
        url = f"/posts?sortBy={sort}&limit={limit}"
        if status:
            url += f"&status={status}"
        payload = self._ok("GET", url, expect=(200,), label="posts")
        if isinstance(payload, list):
            return payload
        return payload.get("posts") or payload.get("data") or []

    def update_post(self, post_id: str, body: dict) -> dict:
        return self._ok("PUT", f"/posts/{post_id}", body=body, expect=(200, 201, 207),
                        label=f"update {post_id}")

    def promote(self, post_id: str, *, publish_now: bool = True,
                scheduled_for: str | None = None, timezone: str | None = None,
                platforms: list[dict] | None = None) -> dict:
        """Take a draft live.

        `platforms` replaces the post's platform list, which is how a draft created for
        several platforms can be published to one of them without touching the others.
        """
        body: dict = {"isDraft": False}
        if platforms is not None:
            body["platforms"] = platforms
        if scheduled_for:
            body["scheduledFor"] = scheduled_for
            if timezone:
                body["timezone"] = timezone
        elif publish_now:
            # Asynchronous for video: this returns while the platform is still processing,
            # so a caller that wants a result has to poll get_post().
            body["publishNow"] = True
        return self.update_post(post_id, body)

    def wait_for_terminal(self, post_id: str, *, timeout: float = 240,
                          interval: float = 15) -> dict:
        """Poll until a post stops being in-flight, or give up and say so.

        `publishNow` is not synchronous for video. Returning the immediate response as a
        result would report success for a post the platform later rejected.
        """
        deadline = time.monotonic() + timeout
        last: dict = {}
        while time.monotonic() < deadline:
            last = self.get_post(post_id)
            data = last.get("post", last)
            states = {str(p.get("status", "")).lower() for p in (data.get("platforms") or [])}
            if states and not (states & {"pending", "processing", "publishing", "queued", ""}):
                return last
            time.sleep(interval)
        return last
