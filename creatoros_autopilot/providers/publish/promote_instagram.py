#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Publish the Instagram backlog: every Zernio draft, Instagram only, paced.

    python3 promote_instagram_backlog.py                 # dry run, shows the exact plan
    python3 promote_instagram_backlog.py --yes           # PUBLISH, publicly, paced
    python3 promote_instagram_backlog.py --yes --spacing 300
    python3 promote_instagram_backlog.py --yes --limit 4         # just the first 4

Why this exists, and why it is not promote_drafts.py: those drafts each carry an
Instagram *and* a YouTube target, and the backlog reels have never been on YouTube --
only the Shorts pipeline posts there. Promoting a draft as-is would push 28 reels onto
the channel as a side effect. PUT /v1/posts/{id} accepts a replacement `platforms`
array, so this drops the YouTube target and publishes Instagram alone.

Duplicates: a re-run created a second draft for most reels (44 drafts, 28 real reels).
publish_ledger.jsonl records which post id the pipeline actually kept, so a title whose
id appears there wins and its twin is skipped. Where neither appears, the newest wins.

Pacing: Zernio caps Instagram at 100 posts/day, so volume is not the constraint -- the
account cooldown is. A post that fails as `user_abuse`, or 10 failures in an hour, puts
the account in a cooldown that doubles each time (10, 20, 40, 80 min, up to 24h). So
this spaces posts out and stops early on repeated failures instead of burning through
the backlog and earning a day-long block.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
from ._api import ZernioAPI

_api = ZernioAPI()


def _req(method, url, key=None, body=None, raw=None, ctype=None, idem=None):
    """The old `publish_zernio.req` signature, backed by the one shared client.

    These three tools each used to carry their own copy of the HTTP layer. They now call
    `_api`, which is where auth, backoff and the "a dropped socket is retryable" rule live.
    `key` is ignored: the client reads ZERNIO_API_KEY itself.
    """
    return _api.call(method, url, body=body, raw=raw, ctype=ctype, idem=idem,
                     authenticated=raw is None, label=url)

LEDGER = os.path.join(HERE, "publish_ledger.jsonl")
STOP_CONSECUTIVE = 2   # two in a row means something systemic; stop before the cooldown
STOP_TOTAL = 3


def ledger_index():
    """Map post id -> the reel path the pipeline recorded for it.

    Two jobs. It resolves a duplicate draft pair to the id the pipeline kept, and it
    recovers each post's real out/DATE/NN_*.mp4 path. Writing that path back into the
    ledger matters: publish_queue.already_done() keys on (path, platform) and ranks
    draft < schedule < publish, so a row filed under a placeholder name would leave
    today's reels still looking un-published, and `run_daily.sh --publish-only` would
    helpfully create a second scheduled post for a reel that is already live.
    """
    idx = {}
    if not os.path.exists(LEDGER):
        return idx
    with open(LEDGER) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            pid, path = r.get("postId"), r.get("file")
            if pid and path and path.endswith(".mp4"):
                idx.setdefault(pid, path)
            elif pid:
                idx.setdefault(pid, None)
    return idx


def ledger_write(rec):
    with open(LEDGER, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def fetch_posts(key, status=None):
    """Posts walked by cursor. Sort by creation: the default scheduled-desc hid a batch."""
    out, cursor = [], None
    while True:
        url = f"{_api.base}/posts?sortBy=created-asc&limit=100"
        if status:
            url += "&status=" + status
        if cursor:
            url += "&cursor=" + cursor
        st, d = _req("GET", url, key)
        if st != 200:
            sys.exit(f"GET /posts -> {st} {str(d)[:300]}")
        posts = d if isinstance(d, list) else (d.get("posts") or d.get("data") or [])
        out.extend(posts)
        page = (d.get("pagination") or {}) if isinstance(d, dict) else {}
        cursor = page.get("nextCursor")
        if not cursor or not posts:
            return out


# A reel whose Instagram post is in any of these states must never be sent again.
SPOKEN_FOR = {"publishing", "published", "partial", "scheduled", "pending_approval"}


def already_live_titles(key):
    """Reel titles that already have a non-draft Instagram post.

    Deduping among drafts alone is not enough. Most reels have two drafts, and publishing
    one removes it from the draft list -- which promotes its untouched twin to being the
    only draft for that reel, so the next run would post the same reel to Instagram a
    second time. Keying on the title across every status closes that.
    """
    live = set()
    for p in fetch_posts(key):
        if p.get("status") not in SPOKEN_FOR:
            continue
        for x in (p.get("platforms") or []):
            if x.get("platform") == "instagram" and x.get("status") != "failed":
                live.add(p.get("title"))
    return live


def account_id(entry):
    """GET returns accountId as an object; PUT needs the bare id."""
    a = entry.get("accountId")
    return a if isinstance(a, str) else (a or {}).get("_id")


def pick(drafts, kept_ids, live_titles):
    """One draft per title: the id the ledger kept, else the newest.

    Titles already live or in flight on Instagram are excluded outright.
    """
    by_title = {}
    for p in drafts:
        if not any(x.get("platform") == "instagram" for x in (p.get("platforms") or [])):
            continue
        by_title.setdefault(p.get("title") or p.get("_id"), []).append(p)

    chosen, dropped, done = [], [], []
    for title, group in by_title.items():
        group.sort(key=lambda p: p.get("createdAt") or "")
        if title in live_titles:
            done.extend(group)
            continue
        winner = next((p for p in group if p.get("_id") in kept_ids), group[-1])
        chosen.append(winner)
        dropped.extend(p for p in group if p is not winner)
    chosen.sort(key=lambda p: p.get("createdAt") or "")
    return chosen, dropped, done


def wait_terminal(key, pid, timeout=240, interval=15):
    """Poll one post until Instagram finishes, or give up and say so.

    publishNow is not synchronous for video: the PUT returns with the platform still
    `processing` and no URL. Trusting that response would record every post as a success
    and hand back no link -- and worse, a post that fails a minute later during Instagram's
    own processing would never trip the early-stop guard, so a systemic failure (expired
    token, say) would be driven through the whole backlog instead of stopping at two.
    """
    deadline = time.time() + timeout
    last = {}
    while True:
        st, d = _req("GET", f"{_api.base}/posts/{pid}", key)
        if st == 200:
            p = (d or {}).get("post", d) if isinstance(d, dict) else {}
            if isinstance(p, dict):
                x = next((e for e in (p.get("platforms") or [])
                          if e.get("platform") == "instagram"), {})
                last = {"postStatus": p.get("status"), "platformStatus": x.get("status"),
                        "url": x.get("platformPostUrl"),
                        "error": x.get("errorMessage") or x.get("errorCategory"),
                        "category": x.get("errorCategory")}
                if x.get("status") in ("published", "failed"):
                    return last
        if time.time() >= deadline:
            last.setdefault("platformStatus", "unknown")
            last["pending"] = True
            return last
        time.sleep(interval)


def promote(key, post, dry):
    """Un-draft one post and publish it to Instagram alone."""
    pid = post.get("_id")
    ig = next((x for x in (post.get("platforms") or []) if x.get("platform") == "instagram"), None)
    if not ig:
        return {"error": "no instagram target"}
    acct = account_id(ig)
    if not acct:
        return {"error": "instagram target has no accountId"}

    entry = {"platform": "instagram", "accountId": acct}
    # Keep the per-platform caption the pipeline wrote; without it Instagram would fall
    # back to the post's top-level content, which is the same text today but need not be.
    if ig.get("customContent"):
        entry["customContent"] = ig["customContent"]

    body = {"isDraft": False, "publishNow": True, "platforms": [entry]}
    if dry:
        return {"dry": True, "body": body}

    st, res = _req("PUT", f"{_api.base}/posts/{pid}", key=key, body=body)
    if st not in (200, 201, 207):
        return {"http": st, "ok": False,
                "error": (res.get("error") if isinstance(res, dict) else None) or str(res)[:300],
                "category": (res.get("details") or {}).get("code") if isinstance(res, dict) else None}

    out = wait_terminal(key, pid)
    out["http"] = st
    # Still processing at the deadline is not a failure: Instagram often finishes a minute
    # later. It is recorded as pending so it does not count against the stop guard.
    out["ok"] = out.get("platformStatus") == "published" or bool(out.get("pending"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="actually publish (default: dry run)")
    ap.add_argument("--spacing", type=int, default=180, help="seconds between posts (default 180)")
    ap.add_argument("--limit", type=int, default=None, help="only the first N reels")
    a = ap.parse_args()

    key = os.environ.get("ZERNIO_API_KEY", "").strip()
    if not key:
        sys.exit("ZERNIO_API_KEY not set. Run: set -a; source ./config.env; source ./.env; set +a")

    drafts = fetch_posts(key, status="draft")
    known = ledger_index()
    chosen, dropped, done = pick(drafts, set(known), already_live_titles(key))
    if a.limit:
        chosen = chosen[:a.limit]

    print(f"{len(drafts)} draft(s) in Zernio -> {len(chosen)} unique reel(s) to publish, "
          f"{len(dropped)} duplicate(s) skipped, {len(done)} already on Instagram")
    print("target: instagram only (the youtube target is dropped, not published)")
    print(f"pacing: {a.spacing}s between posts "
          f"(~{(len(chosen) - 1) * a.spacing // 60} min total)\n")

    for i, p in enumerate(chosen, 1):
        ig = next((x for x in (p.get("platforms") or []) if x.get("platform") == "instagram"), {})
        chars = len(ig.get("customContent") or p.get("content") or "")
        print(f"  {i:>2}. {str(p.get('title'))[:44]:<44} {p.get('createdAt', '')[:10]} "
              f"{p['_id'][-6:]}  {chars} chars")
    if dropped:
        print(f"\n  skipped as duplicates: {', '.join(d['_id'][-6:] for d in dropped)}")

    if not a.yes:
        print(f"\nDRY RUN. Nothing published. Re-run with --yes to publish these "
              f"{len(chosen)} reel(s) to Instagram.")
        return 0

    print(f"\nPUBLISHING {len(chosen)} reel(s) to Instagram. Ctrl-C stops between posts.\n")
    ok = fail = 0
    streak = 0
    for i, p in enumerate(chosen, 1):
        title = str(p.get("title"))[:44]
        print(f"  [{i}/{len(chosen)}] {title} ...", flush=True)
        try:
            res = promote(key, p, dry=False)
        except Exception as exc:
            res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        stamp = datetime.now(timezone.utc).isoformat()
        ledger_write({"ts": stamp, "file": known.get(p.get("_id")) or "(backlog)",
                      "title": p.get("title"),
                      "platform": "instagram", "mode": "publish", "ok": bool(res.get("ok")),
                      "postId": p.get("_id"), "postStatus": res.get("postStatus"),
                      "platformStatus": res.get("platformStatus"), "url": res.get("url"),
                      "error": res.get("error")})

        if res.get("ok"):
            ok += 1
            streak = 0
            if res.get("pending"):
                print(f"        accepted, still processing at Instagram "
                      f"(post {p.get('_id')}) — check it later", flush=True)
            else:
                print(f"        published  {res.get('url')}", flush=True)
        else:
            fail += 1
            streak += 1
            print(f"        FAILED  HTTP {res.get('http')}  {res.get('error')}", flush=True)
            if res.get("category") == "user_abuse":
                print("        user_abuse: Instagram is throttling this account. "
                      "Stopping so the cooldown does not double.", flush=True)
                break
            if streak >= STOP_CONSECUTIVE or fail >= STOP_TOTAL:
                print(f"        stopping early ({streak} in a row, {fail} total) — "
                      f"fix the cause before publishing the rest.", flush=True)
                break

        if i < len(chosen):
            time.sleep(a.spacing)

    print(f"\npublished {ok} | failed {fail} | remaining {len(chosen) - ok - fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
