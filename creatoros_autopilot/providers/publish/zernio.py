#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Publish a rendered video to Instagram and/or YouTube Shorts through the Zernio API.

    creatoros publish VIDEO.mp4 --platforms instagram,youtube --mode draft

Zernio is one publisher among several. From Phase 2 it sits behind a `Publisher`
interface next to the direct YouTube Data API and Instagram Graph API backends, which are
the documented default for new users; Zernio stays fully supported and stays the Nexlore
preset's publisher. Three other copies of this client used to exist in the repo
(`engine/zernio.py`, `publish_queue.py`, `promote_instagram_backlog.py`) and had already
drifted apart; collapsing them into this one is Phase 2 work.

Modes (Zernio semantics):
    draft     isDraft=true     -> saved in Zernio, never published. Safe default.
    schedule  scheduledFor=..  -> published automatically at that time.
    now       publishNow=true  -> goes live immediately; returns platformPostUrl.

Caption/description comes from the sibling <name>.caption.txt.
YouTube Shorts are detected from the vertical aspect ratio; there is no Shorts flag.
Reads ZERNIO_API_KEY from the environment (source .env first).
"""
import argparse, json, mimetypes, os, re, sys, time, urllib.error, urllib.request, uuid
from dataclasses import dataclass, field

from ...core import spec as spec_mod

API = os.environ.get("ZERNIO_API_BASE", "https://zernio.com/api/v1").rstrip("/")
# 0 is this module's code for "the connection itself failed" (see req): a dropped socket or
# a broken pipe mid-upload. On 2026-11-16 a BrokenPipeError during the media PUT escaped as
# an unhandled traceback and cost that day's first Short entirely, so it retries like a 503.
RETRYABLE = {0, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4
LIMITS = {"instagram": {"caption": 2200, "bytes": 300 * 1024 * 1024},
          "youtube":   {"caption": 5000, "bytes": 256 * 1024 * 1024 * 1024, "title": 100}}
YT_CATEGORY_SCI_TECH = "28"   # Science & Technology


def req(method, url, key=None, body=None, raw=None, ctype=None, idem=None):
    headers, data = {"Accept": "application/json"}, None
    if body is not None:
        data = json.dumps(body).encode(); headers["Content-Type"] = "application/json"
    if raw is not None:
        data = raw; headers["Content-Type"] = ctype or "application/octet-stream"
    if key:
        headers["Authorization"] = "Bearer " + key
        if idem: headers["Idempotency-Key"] = idem
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=600) as x:
            p = x.read()
            return x.status, (json.loads(p) if p.strip().startswith(b"{") else p[:200])
    except urllib.error.HTTPError as e:
        d = e.read()
        try: d = json.loads(d)
        except Exception: d = d[:500].decode(errors="replace")
        return e.code, d
    except (urllib.error.URLError, OSError) as e:
        # A transport failure, not an HTTP answer: a reset socket, a timeout, or a broken
        # pipe part-way through a 10 MB upload. Returned as status 0 so every caller sees a
        # failure it can retry instead of an exception that kills the whole run.
        return 0, {"error": "%s: %s" % (type(e).__name__, e)}


def call(method, url, key=None, body=None, raw=None, ctype=None, idem=None, label=""):
    """req() with backoff on transport failures and transient server errors."""
    st, payload = 0, {"error": "no attempt"}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        st, payload = req(method, url, key=key, body=body, raw=raw, ctype=ctype, idem=idem)
        if st not in RETRYABLE:
            return st, payload
        if attempt < MAX_ATTEMPTS:
            wait = min(60, 3 * (2 ** (attempt - 1)))
            print(f"!! {label or url} -> {st} {str(payload)[:120]}; "
                  f"retry {attempt}/{MAX_ATTEMPTS - 1} in {wait}s")
            time.sleep(wait)
    return st, payload


def load_spec(video):
    """The video's spec JSON, which sits beside it.

    The old version also globbed `<repo>/out/*/` for an archived copy. That path only
    existed when the code ran from the repo root, so it is gone: job output now lives in
    the workspace and the spec is always the video's sibling.
    """
    cand = os.path.splitext(video)[0] + ".json"
    if os.path.exists(cand):
        try:
            return spec_mod.load(cand)
        except Exception:
            return {}
    return {}


def spec_hook(video, spec=None):
    """The reel's designed hook line — a good Instagram opener, a weak YouTube title."""
    spec = load_spec(video) if spec is None else spec
    for sc in spec.get("scenes") or []:
        if sc.get("type") == "hook" and sc.get("lines"):
            txt = " ".join((l["text"] if isinstance(l, dict) else str(l)) for l in sc["lines"]).strip()
            if txt:
                return txt
    return ""


def derive_title(caption, fallback, video=None, spec=None):
    """Prefer the spec's hook; else the caption's first sentence. <=100 chars, word boundary."""
    first = (spec_hook(video, spec) if video else "") \
        or next((l.strip() for l in caption.splitlines() if l.strip()), "") \
        or fallback
    first = re.sub(r"#\S+", "", first).strip(" .,-—")
    if len(first) <= LIMITS["youtube"]["title"]:
        return first
    cut = first[:LIMITS["youtube"]["title"]]
    return cut[:cut.rfind(" ")] if " " in cut else cut


def derive_tags(caption, cap_total=500, cap_each=100):
    tags, total = [], 0
    for t in dict.fromkeys(re.findall(r"#(\w+)", caption)):
        t = t[:cap_each]
        if total + len(t) > cap_total: break
        tags.append(t); total += len(t)
    return tags


@dataclass
class Result:
    """What one publish call did, per platform, for the ledger to record."""
    ok: bool
    mode: str
    post_id: str = ""
    platforms: list = field(default_factory=list)   # [{platform, status, platformPostUrl, error}]
    error: str = ""
    dry_run: bool = False

    def url_for(self, platform):
        for entry in self.platforms:
            if entry.get("platform") == platform:
                return entry.get("platformPostUrl", "")
        return ""


class PublishError(RuntimeError):
    pass


def publish(video, platforms=("instagram", "youtube"), mode="draft", at=None,
            tz="UTC", title=None, yt_visibility="public", yt_category="28",
            api_key=None, dry_run=False):
    """Upload one video and create one Zernio post. Returns a Result.

    `mode` is Zernio's own vocabulary: draft (saved, never published), schedule (`at` is a
    naive wall-clock ISO string interpreted in `tz`), now (goes live immediately).
    """
    key = (api_key or os.environ.get("ZERNIO_API_KEY", "")).strip()
    if not key and not dry_run:
        raise PublishError("ZERNIO_API_KEY is not set. Put it in .env beside creatoros.yaml.")
    if mode == "schedule" and not at:
        raise PublishError("mode 'schedule' needs a timestamp")
    a = argparse.Namespace(video=video, platforms=",".join(platforms) if not isinstance(platforms, str) else platforms,
                           mode=mode, at=at, tz=tz, title=title, yt_visibility=yt_visibility)

    vid = os.path.abspath(a.video)
    if not os.path.exists(vid): raise PublishError(f"no such file: {vid}")
    size = os.path.getsize(vid)
    want = [s.strip() for s in a.platforms.split(",") if s.strip()]

    spec = load_spec(vid)
    plat_meta = spec.get("platforms") or {}
    ig_meta, yt_meta = plat_meta.get("instagram") or {}, plat_meta.get("youtube") or {}

    caption = ""
    cap_path = os.path.splitext(vid)[0] + ".caption.txt"
    if os.path.exists(cap_path): caption = open(cap_path).read().strip()
    ig_caption = ig_meta.get("caption") or caption
    yt_description = yt_meta.get("description") or caption
    title = a.title or yt_meta.get("title") or derive_title(caption, os.path.basename(vid), vid, spec)
    tags = yt_meta.get("tags") or derive_tags(caption)
    if not yt_meta.get("title"):
        print("!! no platforms.youtube.title in the spec - deriving from the hook, which makes a "
              "weak search title. See prompts/playbooks/03-platform-metadata.md")

    # An Instagram Reel with no caption gets no reach and cannot be found, so refuse rather
    # than post a blank one. The shorts specs only carried platforms.youtube until
    # 2026-11-16; publishing one to Instagram without this check posted an empty caption.
    if "instagram" in want and not ig_caption.strip():
        raise PublishError(
            "refusing to post to Instagram with an empty caption: the spec has no "
            "platforms.instagram.caption and there is no sibling .caption.txt.\n"
            "Add platforms.instagram.caption to " + os.path.splitext(vid)[0] + ".json")

    # resolve accounts
    if dry_run:
        found = {p: f"dry-run-{p}" for p in want}
        st, d = 200, []
    else:
        st, d = call("GET", f"{API}/accounts", key, label="accounts")
        if st != 200: raise PublishError(f"GET /accounts -> {st} {d}")
    if not dry_run:
        accs = d if isinstance(d, list) else (d.get("accounts") or d.get("data") or [])
        found = {x.get("platform"): (x.get("_id") or x.get("id")) for x in accs}
    targets = []
    for plat in want:
        if plat not in found: raise PublishError(f"'{plat}' is not connected in Zernio (have: {sorted(found)})")
        lim = LIMITS.get(plat, {})
        if lim.get("bytes") and size > lim["bytes"]:
            raise PublishError(f"{size/1e6:.0f} MB exceeds {plat}'s limit")
        body = ig_caption if plat == "instagram" else yt_description
        if lim.get("caption") and len(body) > lim["caption"]:
            print(f"!! text {len(body)} > {plat} max {lim['caption']}; will truncate for {plat}")
        targets.append(plat)
    print(f"targets: {', '.join(f'{t} ({found[t]})' for t in targets)}")
    print(f"title:   {title!r} ({len(title)} chars)")
    print(f"tags:    {tags}")

    if dry_run:
        payload = {"targets": targets, "title": title, "tags": tags, "mode": a.mode,
                   "at": a.at, "tz": a.tz, "bytes": size,
                   "instagram_caption_chars": len(ig_caption), "youtube_description_chars": len(yt_description)}
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return Result(ok=True, mode=a.mode, dry_run=True,
                      platforms=[{"platform": p, "status": "dry-run"} for p in targets])

    # 1. presign + 2. upload
    ctype = mimetypes.guess_type(vid)[0] or "video/mp4"
    st, pre = call("POST", f"{API}/media/presign", key,
                   {"filename": os.path.basename(vid), "contentType": ctype, "size": size},
                   idem=str(uuid.uuid4()), label="presign")
    if st != 200: raise PublishError(f"presign -> {st} {pre}")
    with open(vid, "rb") as f: blob = f.read()
    st, up_err = call("PUT", pre["uploadUrl"], None, raw=blob, ctype=ctype, label="upload")
    if st not in (200, 201): raise PublishError(f"upload -> {st} {str(up_err)[:200]}")
    print(f"uploaded {size/1e6:.1f} MB")

    # 3. build platform entries
    entries = []
    for plat in targets:
        e = {"platform": plat, "accountId": found[plat]}
        if plat == "youtube":
            e["platformSpecificData"] = {"title": title[:LIMITS["youtube"]["title"]],
                                         "visibility": a.yt_visibility,
                                         "categoryId": str(yt_category),
                                         "madeForKids": False,
                                         # the narration is an AI voice, so this is always true
                                         "containsSyntheticMedia": True}
            e["customContent"] = yt_description[:LIMITS["youtube"]["caption"]]
        if plat == "instagram":
            e["customContent"] = ig_caption[:LIMITS["instagram"]["caption"]]
        entries.append(e)

    post = {"content": ig_caption, "title": os.path.basename(vid),
            "mediaItems": [{"url": pre["publicUrl"], "type": "video"}],
            "platforms": entries}
    if tags: post["tags"] = tags
    if a.mode == "draft": post["isDraft"] = True
    elif a.mode == "now": post["publishNow"] = True
    else: post.update(scheduledFor=a.at, timezone=a.tz)

    st, res = call("POST", f"{API}/posts", key, post, idem=str(uuid.uuid4()), label="create")
    print(f"POST /posts -> HTTP {st}")
    if st not in (200, 201, 207): raise PublishError(json.dumps(res, indent=2)[:1500])
    pd = res.get("post", res)
    per_platform = [{k: v for k, v in pl.items()
                     if k in ("platform", "status", "platformPostUrl", "errorCategory", "error")}
                    for pl in (pd.get("platforms") or [])]
    print(json.dumps({"id": pd.get("_id"), "status": pd.get("status"), "mode": a.mode,
                      "platforms": per_platform, "warnings": res.get("warnings")}, indent=2))
    if st == 207:
        print("!! HTTP 207 = created but inline publish incomplete; read status above")
    return Result(ok=True, mode=a.mode, post_id=str(pd.get("_id") or ""), platforms=per_platform)


def main(argv=None):
    p = argparse.ArgumentParser(prog="creatoros publish")
    p.add_argument("video")
    p.add_argument("--platforms", default="instagram,youtube")
    p.add_argument("--mode", default="draft", choices=["draft", "now", "schedule"])
    p.add_argument("--at"); p.add_argument("--tz", default="UTC")
    p.add_argument("--title", default=None)
    p.add_argument("--yt-visibility", default="public", choices=["public", "private", "unlisted"])
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    try:
        res = publish(a.video, [s.strip() for s in a.platforms.split(",") if s.strip()],
                      a.mode, a.at, a.tz, a.title, a.yt_visibility, dry_run=a.dry_run)
    except PublishError as exc:
        print(f"publish failed: {exc}", file=sys.stderr)
        return 1
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
