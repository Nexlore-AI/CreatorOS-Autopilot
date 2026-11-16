#!/usr/bin/env python3
"""Nexlore AI -> Zernio: draft, schedule or publish finished reels to social platforms.

Usage:
    python3 engine/zernio.py accounts                      # what is connected (read-only)
    python3 engine/zernio.py post VIDEO.mp4                # create a draft (default, nothing goes out)
    python3 engine/zernio.py post VIDEO.mp4 --mode schedule
    python3 engine/zernio.py post VIDEO.mp4 --mode publish  # posts immediately
    python3 engine/zernio.py post VIDEO.mp4 --dry-run      # print the payload, call nothing

Prefer the ./zernio wrapper, which sources config.env + .env first.

Modes (default: draft)
    draft     saved in Zernio as a draft; review in the dashboard before anything goes out
    schedule  scheduled at the config.env POST_TIMES slot matching the reel's 0N_ filename prefix
    publish   published immediately

Caption comes from the video's sibling *.caption.txt unless --caption is given.

Auth:   $ZERNIO_API_KEY, read from the environment (.env). Never hardcode it.
Config: $ZERNIO_PLATFORMS (comma list to limit platforms), $ZERNIO_PROFILE_ID,
        $POST_TIMES, $POST_TZ (default Asia/Kolkata), $ZERNIO_API_BASE.

No third-party packages: this uses stdlib urllib, like reel_engine.py, so a .venv
rebuild (which installs only pillow + numpy) can never break it.

Docs: https://zernio.com/agent-quickstart.md and https://zernio.com/llms.txt
"""
import argparse, datetime, json, mimetypes, os, re, sys, urllib.error, urllib.request

API_BASE = os.environ.get("ZERNIO_API_BASE", "https://zernio.com/api/v1").rstrip("/")
TZ_NAME = os.environ.get("POST_TZ", "Asia/Kolkata")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def die(msg, code=1):
    print("zernio: " + msg, file=sys.stderr)
    sys.exit(code)


def api_key():
    k = os.environ.get("ZERNIO_API_KEY", "").strip()
    if not k:
        die("ZERNIO_API_KEY is not set. It belongs in .env (never in config.env or in code).")
    return k


def request(method, url, body=None, headers=None, raw=None, content_type=None, timeout=120):
    """One HTTP call. Returns (status, body_bytes); HTTP errors come back, not raised."""
    hdrs = dict(headers or {})
    data = None
    if raw is not None:
        data = raw
        hdrs["Content-Type"] = content_type or "application/octet-stream"
    elif body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return getattr(r, "status", r.getcode()), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        die("cannot reach %s (%s)" % (url, e.reason))


def api(method, path, body=None, timeout=120):
    """Authenticated JSON call against the Zernio API."""
    status, payload = request(method, API_BASE + path, body=body, timeout=timeout,
                              headers={"Authorization": "Bearer " + api_key(),
                                       "Accept": "application/json"})
    text = payload.decode("utf-8", "replace")
    if status >= 400:
        die("%s %s -> HTTP %d: %s" % (method, path, status, text[:400]))
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except ValueError:
        die("%s %s -> HTTP %d but the body is not JSON: %s" % (method, path, status, text[:200]))


# ---- accounts ----

def list_accounts():
    return api("GET", "/accounts").get("accounts") or []


def pick_accounts(wanted=None):
    """Active accounts, optionally limited to a comma list of platform names."""
    accts = [a for a in list_accounts() if a.get("isActive", True)]
    if wanted:
        want = set(w.strip().lower() for w in wanted.split(",") if w.strip())
        accts = [a for a in accts if str(a.get("platform", "")).lower() in want]
    return accts


def cmd_accounts(args):
    accts = list_accounts()
    if not accts:
        print("No connected accounts. Connect them at https://zernio.com, then re-run.")
        return 0
    for a in accts:
        print("%-12s %-24s %s%s" % (a.get("platform", "?"), a.get("username") or a.get("displayName") or "",
                                    a.get("_id", "?"), "" if a.get("isActive", True) else "  (inactive)"))
    return 0


# ---- media ----

def upload_media(path):
    """presign -> PUT the bytes -> return the mediaItems entry for this file."""
    name = os.path.basename(path)
    mime = mimetypes.guess_type(name)[0] or "video/mp4"
    pres = api("POST", "/media/presign", {"filename": name, "contentType": mime})
    up, public = pres.get("uploadUrl"), pres.get("publicUrl")
    if not up or not public:
        die("/media/presign returned no uploadUrl+publicUrl: " + json.dumps(pres)[:300])
    with open(path, "rb") as f:
        blob = f.read()
    status, resp = request("PUT", up, raw=blob, content_type=mime, timeout=900)
    if status >= 400:
        die("media upload PUT -> HTTP %d: %s" % (status, resp.decode("utf-8", "replace")[:300]))
    return {"type": "video", "url": public, "filename": name, "size": len(blob), "mimeType": mime}


# ---- captions, slots, scheduling ----

def caption_for(video, explicit=None):
    p = explicit or re.sub(r"\.mp4$", ".caption.txt", video)
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            return f.read().strip()
    if explicit:
        die("caption file not found: " + p)
    return ""


def slot_index(video):
    m = re.match(r"(\d+)_", os.path.basename(video))
    return int(m.group(1)) if m else None


def day_of(video):
    """The out/YYYY-MM-DD folder this reel lives in, else today."""
    day = os.path.basename(os.path.dirname(os.path.abspath(video)))
    try:
        return datetime.date.fromisoformat(day)
    except ValueError:
        return datetime.date.today()


def local_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(TZ_NAME)
    except Exception:
        # Asia/Kolkata has no DST, so a fixed +05:30 is a safe fallback.
        return datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def scheduled_for(video, when=None):
    """ISO-8601 UTC instant for this reel's POST_TIMES slot, or --at verbatim."""
    if when:
        return when
    times = [t.strip() for t in os.environ.get("POST_TIMES", "").split(",") if t.strip()]
    idx = slot_index(video)
    if not times:
        die("POST_TIMES is not set (config.env), so --mode schedule has no time. Pass --at.")
    if not idx or idx > len(times):
        die("filename gives slot %s but POST_TIMES has %d entries. Pass --at." % (idx, len(times)))
    hh, mm = (int(x) for x in times[idx - 1].split(":")[:2])
    d = day_of(video)
    local = datetime.datetime(d.year, d.month, d.day, hh, mm, tzinfo=local_tz())
    return local.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def title_of(video):
    """Reel title from the sibling spec JSON, else from the filename."""
    spec = re.sub(r"\.mp4$", ".json", video)
    if os.path.isfile(spec):
        try:
            with open(spec, encoding="utf-8") as f:
                d = json.load(f)
            for k in ("title", "name", "hook"):
                if isinstance(d.get(k), str) and d[k].strip():
                    return d[k].strip()
            # Reel specs carry the title on a scene (e.g. the card scene), not at the top level.
            for sc in d.get("scenes") or []:
                for k in ("title", "heading"):
                    if isinstance(sc.get(k), str) and sc[k].strip():
                        return sc[k].strip()
        except (ValueError, OSError):
            pass
    base = re.sub(r"\.mp4$", "", os.path.basename(video))
    return re.sub(r"^\d+_[a-z]+_", "", base).replace("_", " ") or base


def record(video, mode, id_or_url, ok):
    """Append one line to history.jsonl, matching the existing shape."""
    base = os.path.basename(video)
    m = re.match(r"(\d+_[a-z]+)", base)
    entry = {"date": day_of(video).isoformat(),
             "slot": m.group(1) if m else base,
             "title": title_of(video),
             "file": os.path.relpath(os.path.abspath(video), ROOT),
             "upload": {"mode": "zernio:" + mode, "id_or_url": id_or_url, "ok": ok}}
    try:
        with open(os.path.join(ROOT, "history.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        print("zernio: warning, could not append to history.jsonl (%s)" % e, file=sys.stderr)


# ---- post ----

def cmd_post(args):
    video = args.video
    if not os.path.isfile(video):
        die("no such video: " + video)
    mode = args.mode

    accts = pick_accounts(args.platforms or os.environ.get("ZERNIO_PLATFORMS"))
    if not accts and not args.dry_run:
        die("no connected Zernio accounts to post to. Connect them at https://zernio.com "
            "(then `./zernio accounts` will list them). Nothing was uploaded.")

    body = {"content": caption_for(video, args.caption),
            "title": title_of(video),
            "timezone": TZ_NAME,
            "platforms": [{"platform": a.get("platform"), "accountId": a.get("_id")} for a in accts]}
    if os.environ.get("ZERNIO_PROFILE_ID"):
        body["profileId"] = os.environ["ZERNIO_PROFILE_ID"]

    if mode == "draft":
        body["isDraft"] = True
    elif mode == "schedule":
        body["isDraft"] = False
        body["publishNow"] = False
        body["scheduledFor"] = scheduled_for(video, args.at)
    elif mode == "publish":
        body["isDraft"] = False
        body["publishNow"] = True

    if args.dry_run:
        body["mediaItems"] = [{"type": "video", "url": "<uploaded on a real run>",
                               "filename": os.path.basename(video),
                               "size": os.path.getsize(video), "mimeType": "video/mp4"}]
        print(json.dumps({"POST": API_BASE + "/posts", "body": body}, indent=2))
        if not accts:
            print("\nNote: no connected accounts, so `platforms` is empty. "
                  "A real run would refuse to post.", file=sys.stderr)
        return 0

    media = upload_media(video)
    body["mediaItems"] = [media]
    res = api("POST", "/posts", body)
    post = res.get("post") or res
    pid = post.get("_id") or post.get("id") or ""
    where = ", ".join(sorted(set(str(a.get("platform")) for a in accts)))
    when = body.get("scheduledFor", "now" if mode == "publish" else "draft")
    print("%s -> zernio:%s  id=%s  platforms=%s  when=%s" % (os.path.basename(video), mode, pid, where, when))
    record(video, mode, pid, True)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="zernio", description="Send Nexlore AI reels to Zernio.")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("accounts", help="list connected accounts (read-only)")
    q = sub.add_parser("post", help="draft, schedule or publish one reel")
    q.add_argument("video")
    q.add_argument("--mode", choices=["draft", "schedule", "publish"], default="draft")
    q.add_argument("--caption", help="caption file (default: the video's *.caption.txt)")
    q.add_argument("--at", help="ISO-8601 time for --mode schedule, overriding POST_TIMES")
    q.add_argument("--platforms", help="comma list of platforms to post to (default: all active)")
    q.add_argument("--dry-run", action="store_true", help="print the payload and call nothing")
    a = p.parse_args(argv)
    if a.cmd == "accounts":
        return cmd_accounts(a)
    if a.cmd == "post":
        return cmd_post(a)
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
