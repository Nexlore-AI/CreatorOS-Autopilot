#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Promote Zernio drafts to live or scheduled. This is the step that makes posts public.

    python3 promote_drafts.py --dir out/2026-11-07-fix              # dry run: show what would go live
    python3 promote_drafts.py --dir out/2026-11-07-fix --yes        # PUBLISH NOW, publicly
    python3 promote_drafts.py --post 6aeef664... --yes              # one specific draft
    python3 promote_drafts.py --dir out/DATE --at 2026-11-08T09:00:00 --yes   # schedule instead
    python3 promote_drafts.py --list                                # every draft the ledger knows

Dry run is the default. Nothing goes public without --yes.

Zernio semantics (PUT /v1/posts/{id}):
  isDraft:false + publishNow:true   -> publishes immediately
  isDraft:false + scheduledFor:...  -> becomes scheduled
  scheduledFor alone                -> returns 200 but the post STAYS a draft (a real trap)
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
from . import zernio as pz                       # spec + metadata helpers
from ._api import LIMITS, ZernioAPI, ZernioError

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


def ledger_rows():
    if not os.path.exists(LEDGER):
        return []
    out = []
    for line in open(LEDGER):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def get_post(key, pid):
    st, d = _req("GET", f"{_api.base}/posts/{pid}", key=key)
    if st != 200:
        return None
    return (d or {}).get("post", d)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=None, help="promote every draft the ledger has for this folder")
    ap.add_argument("--post", action="append", default=[], help="a specific post id (repeatable)")
    ap.add_argument("--at", default=None, help="schedule for this ISO time instead of publishing now")
    ap.add_argument("--tz", default="Asia/Kolkata")
    ap.add_argument("--list", action="store_true", help="list known drafts and exit")
    ap.add_argument("--yes", action="store_true", help="actually do it (without this, dry run)")
    a = ap.parse_args()

    key = os.environ.get("ZERNIO_API_KEY", "").strip()
    if not key:
        sys.exit("ZERNIO_API_KEY not set. Run: set -a; source ./config.env; source ./.env; set +a")

    rows = ledger_rows()

    if a.list:
        seen = {}
        for r in rows:
            if r.get("postId"):
                seen.setdefault(r["postId"], set()).add(r["file"])
        print(f"{'post id':<26} {'status':<10} file(s)")
        print("-" * 92)
        for pid, files in seen.items():
            p = get_post(key, pid) or {}
            print(f"{pid:<26} {str(p.get('status')):<10} {', '.join(sorted(files))[:54]}")
        return 0

    targets = list(dict.fromkeys(a.post))
    if a.dir:
        want = os.path.relpath(os.path.abspath(a.dir), HERE)
        for r in rows:
            if r.get("postId") and str(r.get("file", "")).startswith(want + os.sep):
                if r["postId"] not in targets:
                    targets.append(r["postId"])
    if not targets:
        sys.exit("nothing selected: pass --dir and/or --post (or --list to see what exists)")

    action = f"SCHEDULE for {a.at} ({a.tz})" if a.at else "PUBLISH NOW, publicly"
    print(f"{len(targets)} post(s) selected -> {action}\n")

    live = []
    for pid in targets:
        p = get_post(key, pid)
        if not p:
            print(f"  {pid}  !! could not read; skipping")
            continue
        plats = ", ".join(x.get("platform", "?") for x in (p.get("platforms") or []))
        st = p.get("status")
        if st != "draft":
            print(f"  {pid}  status={st} (not a draft) - skipping")
            continue
        print(f"  {pid}  {str(p.get('title'))[:44]:<44} -> {plats}")
        live.append(pid)

    if not live:
        print("\nnothing to promote.")
        return 0

    if not a.yes:
        print(f"\nDRY RUN. Nothing changed. {len(live)} post(s) would go to: {action}")
        print("Re-run with --yes to do it.")
        return 0

    body = {"isDraft": False}
    if a.at:
        body["scheduledFor"] = a.at
        body["timezone"] = a.tz
    else:
        body["publishNow"] = True

    print()
    failed = 0
    for pid in live:
        st, res = _req("PUT", f"{_api.base}/posts/{pid}", key=key, body=body)
        pd = (res or {}).get("post", res) if isinstance(res, dict) else {}
        status = pd.get("status") if isinstance(pd, dict) else None
        if st not in (200, 201, 207):
            print(f"  {pid}  FAILED HTTP {st}: {json.dumps(res)[:200] if isinstance(res, dict) else str(res)[:200]}")
            failed += 1
            continue
        urls = [x.get("platformPostUrl") for x in (pd.get("platforms") or []) if x.get("platformPostUrl")]
        flag = "  (HTTP 207 - partial, check per-platform status)" if st == 207 else ""
        print(f"  {pid}  status={status}{flag}")
        for u in urls:
            print(f"      live: {u}")
        with open(LEDGER, "a") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(),
                                "file": "(promote)", "platform": "*", "mode": "scheduled" if a.at else "publish",
                                "ok": st in (200, 201), "postId": pid, "postStatus": status,
                                "urls": urls}, ensure_ascii=False) + "\n")

    print(f"\npromoted {len(live) - failed} | failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
