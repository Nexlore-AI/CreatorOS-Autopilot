#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Batch-publish a day's reels to Instagram and YouTube through Zernio, idempotently.

    python3 publish_queue.py [DATE] [--platforms instagram,youtube]
                             [--mode draft|schedule|publish] [--dir PATH]
                             [--dry-run] [--only SLUG]

DATE defaults to today in Asia/Kolkata. The directory defaults to out/DATE.

Every (file, platform) pair is recorded in publish_ledger.jsonl. A pair that already
succeeded is never posted twice, so this is safe to re-run after a crash, a reboot, or a
partial failure -- which is what makes the daily job restartable.

Modes come from PUBLISH_MODE (default draft):
    draft     saved in Zernio, never published. The safe default.
    schedule  published at the POST_TIMES slot matching the reel's 0N_ prefix, IST.
    publish   goes live immediately.

Reads ZERNIO_API_KEY from the environment. Source config.env and .env first.
"""
import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
from . import zernio as pz  # spec + metadata helpers
from ._api import LIMITS, ZernioAPI

_api = ZernioAPI()


def _req(method, url, key=None, body=None, raw=None, ctype=None, idem=None):
    """The old `publish_zernio.req` signature, backed by the one shared client.

    These three tools each used to carry their own copy of the HTTP layer. They now call
    `_api`, which is where auth, backoff and the "a dropped socket is retryable" rule live.
    `key` is ignored: the client reads ZERNIO_API_KEY itself.
    """
    return _api.call(method, url, body=body, raw=raw, ctype=ctype, idem=idem,
                     authenticated=raw is None, label=url)

API = _api.base
LEDGER = os.path.join(HERE, "publish_ledger.jsonl")
IST = timezone(timedelta(hours=5, minutes=30))

# 0 is publish_zernio.req()'s code for a transport failure (dropped socket, broken pipe
# mid-upload), which is exactly the kind of thing a retry fixes.
RETRYABLE = {0, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4


# ---------------------------------------------------------------- ledger

def rel(path):
    """Repo-relative path, which is the ledger's identity for a reel.

    Keying on the bare filename was wrong: a corrected re-render living in a different
    folder has the same name but different content, and was skipped as already-posted.
    """
    try:
        return os.path.relpath(os.path.abspath(path), HERE)
    except ValueError:
        return os.path.abspath(path)


def ledger_read():
    """Map (relative path, platform) -> last record. Later lines win."""
    out = {}
    if not os.path.exists(LEDGER):
        return out
    with open(LEDGER) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            out[(r.get("file"), r.get("platform"))] = r
    return out


def ledger_write(rec):
    with open(LEDGER, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def already_done(ledger, relpath, platform, mode):
    r = ledger.get((relpath, platform))
    if not r or not r.get("ok"):
        return False
    # A draft does not satisfy a later request to actually publish.
    rank = {"draft": 0, "schedule": 1, "publish": 2}
    return rank.get(r.get("mode"), 0) >= rank.get(mode, 0)


# ---------------------------------------------------------------- http

def call(method, url, key=None, body=None, raw=None, ctype=None, idem=None, label=""):
    """One HTTP call with backoff on transient failures. Returns (status, payload)."""
    last = (None, "no attempt")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        st, payload = _req(method, url, key=key, body=body, raw=raw, ctype=ctype, idem=idem)
        if st not in RETRYABLE:
            return st, payload
        last = (st, payload)
        if attempt < MAX_ATTEMPTS:
            wait = min(60, 3 * (2 ** (attempt - 1)))
            print(f"    {label} HTTP {st}; retry {attempt}/{MAX_ATTEMPTS - 1} in {wait}s")
            time.sleep(wait)
    return last


def stable_idem(relpath, platforms, mode):
    """Same reel + same targets + same mode always reuses one key, so a retry after a
    timeout returns the original post instead of creating a second one.

    Must use the repo-relative PATH, not the basename: a corrected re-render under a
    different folder shares its filename, and a basename seed made Zernio hand back the
    original post instead of creating the corrected one."""
    seed = f"{relpath}|{','.join(sorted(platforms))}|{mode}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "nexlore:" + seed))


# ---------------------------------------------------------------- scheduling

SLOT_ORDER = ["01", "02", "03", "04"]


def scheduled_for(basename, post_times, day):
    """Map the reel's 0N_ prefix onto the matching POST_TIMES slot, in IST."""
    times = [t.strip() for t in (post_times or "").split(",") if t.strip()]
    if not times:
        return None
    prefix = basename[:2]
    idx = SLOT_ORDER.index(prefix) if prefix in SLOT_ORDER else 0
    hhmm = times[idx % len(times)]
    try:
        hh, mm = (int(x) for x in hhmm.split(":"))
    except Exception:
        return None
    when = datetime.combine(day, datetime.min.time(), tzinfo=IST).replace(hour=hh, minute=mm)
    if when <= datetime.now(IST):          # that slot has passed; push to tomorrow
        when += timedelta(days=1)
    return when.strftime("%Y-%m-%dT%H:%M:%S%z")


# ---------------------------------------------------------------- one reel

def publish_one(vid, accounts, platforms, mode, post_times, day, dry):
    relpath = rel(vid)
    """Upload one reel's media once and create a single post covering `platforms`."""
    basename = os.path.basename(vid)
    key = os.environ["ZERNIO_API_KEY"].strip()
    size = os.path.getsize(vid)

    spec = pz.load_spec(vid)
    pm = spec.get("platforms") or {}
    ig_meta, yt_meta = pm.get("instagram") or {}, pm.get("youtube") or {}

    cap_path = os.path.splitext(vid)[0] + ".caption.txt"
    caption = open(cap_path).read().strip() if os.path.exists(cap_path) else ""
    ig_caption = ig_meta.get("caption") or caption
    yt_description = yt_meta.get("description") or caption
    title = yt_meta.get("title") or pz.derive_title(caption, basename, vid, spec)
    tags = yt_meta.get("tags") or pz.derive_tags(caption)

    for plat in platforms:
        lim = LIMITS.get(plat, {})
        body = ig_caption if plat == "instagram" else yt_description
        if lim.get("bytes") and size > lim["bytes"]:
            return {"fatal": f"{size/1e6:.0f} MB exceeds {plat} limit"}
        if lim.get("caption") and len(body) > lim["caption"]:
            print(f"    ! {plat} text {len(body)} > {lim['caption']}; truncating")

    if dry:
        print(f"    DRY RUN: would post to {', '.join(platforms)} as {mode}")
        print(f"      title: {title!r}")
        print(f"      ig   : {len(ig_caption)} chars | yt: {len(yt_description)} chars | tags: {len(tags)}")
        return {"dry": True}

    # 1-2. presign + upload the bytes once, shared by every platform in this post
    st, pre = call("POST", f"{API}/media/presign", key,
                   {"filename": basename, "contentType": "video/mp4", "size": size},
                   idem=str(uuid.uuid4()), label="presign")
    if st != 200:
        return {"error": f"presign HTTP {st}: {str(pre)[:200]}"}
    with open(vid, "rb") as f:
        blob = f.read()
    st, _ = call("PUT", pre["uploadUrl"], None, raw=blob, ctype="video/mp4", label="upload")
    if st not in (200, 201):
        return {"error": f"upload HTTP {st}"}
    print(f"    uploaded {size/1e6:.1f} MB")

    # 3. one post, every pending platform
    entries = []
    for plat in platforms:
        e = {"platform": plat, "accountId": accounts[plat]}
        if plat == "youtube":
            e["platformSpecificData"] = {
                "title": title[:LIMITS["youtube"]["title"]],
                "visibility": os.environ.get("YT_VISIBILITY", "public"),
                "categoryId": pz.YT_CATEGORY_SCI_TECH,
                "madeForKids": False,
                "containsSyntheticMedia": True,   # AI narration
            }
            e["customContent"] = yt_description[:LIMITS["youtube"]["caption"]]
        else:
            e["customContent"] = ig_caption[:LIMITS["instagram"]["caption"]]
        entries.append(e)

    post = {
        "content": ig_caption,
        "title": basename,
        "mediaItems": [{"url": pre["publicUrl"], "type": "video"}],
        "platforms": entries,
    }
    if tags:
        post["tags"] = tags
    if mode == "draft":
        post["isDraft"] = True
    elif mode == "publish":
        post["publishNow"] = True
        post["isDraft"] = False
    else:
        when = scheduled_for(basename, post_times, day)
        if not when:
            return {"error": "schedule mode but POST_TIMES is empty or malformed"}
        post["scheduledFor"] = when
        post["timezone"] = "Asia/Kolkata"
        # Explicit, even though create defaults isDraft to false: a post that carries no
        # timing at all silently becomes a draft, and a draft never publishes. Saying
        # isDraft:false here means a malformed scheduledFor fails loudly instead of
        # quietly parking the reel in the dashboard, which is how Instagram went
        # 8 days without a single post.
        post["isDraft"] = False

    st, res = call("POST", f"{API}/posts", key, post,
                   idem=stable_idem(relpath, platforms, mode), label="create")

    if st == 409:
        detail = res.get("detail", res) if isinstance(res, dict) else res
        return {"duplicate": True, "detail": str(detail)[:300],
                "existingPostId": (res or {}).get("existingPostId") if isinstance(res, dict) else None}
    if st not in (200, 201, 207):
        return {"error": f"create HTTP {st}: {json.dumps(res)[:300] if isinstance(res, dict) else str(res)[:300]}"}

    pd = (res or {}).get("post", res) or {}
    per = {}
    for pl in pd.get("platforms") or []:
        per[pl.get("platform")] = {"status": pl.get("status"),
                                   "url": pl.get("platformPostUrl"),
                                   "error": pl.get("error") or pl.get("errorCategory")}
    return {"postId": pd.get("_id"), "status": pd.get("status"), "http": st,
            "per_platform": per, "scheduledFor": pd.get("scheduledFor")}


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", default=None)
    ap.add_argument("--dir", default=None)
    ap.add_argument("--platforms", default=os.environ.get("PUBLISH_PLATFORMS", "instagram,youtube"))
    ap.add_argument("--mode", default=os.environ.get("PUBLISH_MODE", "draft"),
                    choices=["draft", "schedule", "publish"])
    ap.add_argument("--only", default=None, help="only reels whose filename contains this")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    key = os.environ.get("ZERNIO_API_KEY", "").strip()
    if not key:
        sys.exit("ZERNIO_API_KEY not set. Run: set -a; source ./config.env; source ./.env; set +a")

    today = datetime.now(IST).date()
    date = a.date or today.isoformat()
    try:
        day = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        sys.exit(f"bad date {date!r}, want YYYY-MM-DD")

    folder = a.dir or os.path.join(HERE, "out", date)
    if not os.path.isdir(folder):
        sys.exit(f"no such folder: {folder}")

    want = [p.strip() for p in a.platforms.split(",") if p.strip()]

    st, d = call("GET", f"{API}/accounts", key, label="accounts")
    if st != 200:
        sys.exit(f"GET /accounts -> {st} {str(d)[:200]}")
    rows = d if isinstance(d, list) else (d.get("accounts") or d.get("data") or [])
    accounts = {r.get("platform"): (r.get("_id") or r.get("id")) for r in rows}
    missing = [p for p in want if p not in accounts]
    if missing:
        sys.exit(f"not connected in Zernio: {missing} (have {sorted(accounts)})")

    videos = sorted(f for f in os.listdir(folder)
                    if f.endswith(".mp4") and (not a.only or a.only in f))
    if not videos:
        print(f"no reels in {folder}")
        return 0

    ledger = ledger_read()
    print(f"{len(videos)} reel(s) in {folder} | mode={a.mode} | platforms={','.join(want)}")
    if a.mode == "publish":
        print("  !! PUBLISH MODE - these go live publicly")

    posted = skipped = failed = 0
    for name in videos:
        vid = os.path.join(folder, name)
        # already_done() is false for any pair recorded as failed, so failures retry by default
        rp = rel(vid)
        pending = [p for p in want if not already_done(ledger, rp, p, a.mode)]
        if not pending:
            print(f"  = {name}: already done on {', '.join(want)}")
            skipped += 1
            continue

        print(f"  > {name} -> {', '.join(pending)}")
        try:
            res = publish_one(vid, accounts, pending, a.mode, os.environ.get("POST_TIMES"), day, a.dry_run)
        except Exception as exc:                       # never let one reel kill the batch
            res = {"error": f"{type(exc).__name__}: {exc}"}

        if res.get("dry"):
            continue
        stamp = datetime.now(timezone.utc).isoformat()
        if res.get("fatal") or res.get("error"):
            msg = res.get("fatal") or res.get("error")
            print(f"    FAILED: {msg}")
            failed += 1
            for p in pending:
                ledger_write({"ts": stamp, "file": rp, "platform": p, "mode": a.mode,
                              "ok": False, "error": msg})
        elif res.get("duplicate"):
            print("    duplicate within 24h - treating as already posted")
            skipped += 1
            for p in pending:
                ledger_write({"ts": stamp, "file": rp, "platform": p, "mode": a.mode,
                              "ok": True, "duplicate": True,
                              "postId": res.get("existingPostId"), "note": res.get("detail")})
        else:
            per = res.get("per_platform") or {}
            print(f"    post {res.get('postId')} status={res.get('status')}"
                  + (f" scheduledFor={res.get('scheduledFor')}" if res.get("scheduledFor") else ""))
            posted += 1
            for p in pending:
                info = per.get(p) or {}
                if info.get("url"):
                    print(f"      {p}: {info['url']}")
                ledger_write({"ts": stamp, "file": rp, "platform": p, "mode": a.mode,
                              "ok": True, "postId": res.get("postId"),
                              "postStatus": res.get("status"),
                              "platformStatus": info.get("status"), "url": info.get("url"),
                              "scheduledFor": res.get("scheduledFor")})

    print(f"\nposted {posted} | skipped {skipped} | failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
