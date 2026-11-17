#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Publishing through Zernio, which fans one post out to connected accounts.

Zernio is one publisher among several. The direct YouTube Data API and Instagram Graph API
backends are the documented default for new users — they need no third-party subscription
— and Zernio stays fully supported because it is what runs the Nexlore channels and because
it reaches both platforms with one upload and one OAuth relationship instead of two.

All HTTP lives in `_api.ZernioAPI`. This module is the business logic the protocol asks for:
what a title is, what a caption is, which limits apply, and what counts as success.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from ...core import spec as spec_mod
from ...interfaces import Availability, PlatformOutcome, PublishRequest, PublishResult
from ._api import LIMITS, ZernioAPI, ZernioError

YT_CATEGORY_SCI_TECH = "28"
FAILED_STATUSES = {"failed", "error", "rejected", "cancelled"}


def _caption_file(video: str) -> str:
    return os.path.splitext(video)[0] + ".caption.txt"


def load_spec(video: str) -> dict:
    """The video's spec, which sits beside it."""
    candidate = os.path.splitext(video)[0] + ".json"
    if os.path.exists(candidate):
        try:
            return spec_mod.load(candidate)
        except Exception:
            return {}
    return {}


def spec_hook(spec: dict) -> str:
    """The designed hook line — a good Instagram opener, a weak YouTube title."""
    for scene in spec.get("scenes") or []:
        if scene.get("type") == "hook" and scene.get("lines"):
            text = " ".join(line["text"] if isinstance(line, dict) else str(line)
                            for line in scene["lines"]).strip()
            if text:
                return text
    return ""


def derive_title(caption: str, fallback: str, spec: dict | None = None) -> str:
    """Prefer the spec's hook, else the caption's first line. <=100 chars, on a word."""
    first = (spec_hook(spec or {}) or
             next((line.strip() for line in caption.splitlines() if line.strip()), "") or
             fallback)
    first = re.sub(r"#\S+", "", first).strip(" .,-—")
    cap = LIMITS["youtube"]["title"]
    if len(first) <= cap:
        return first
    cut = first[:cap]
    return cut[:cut.rfind(" ")] if " " in cut else cut


def derive_tags(caption: str, cap_total: int = 500, cap_each: int = 100) -> list[str]:
    tags, total = [], 0
    for tag in dict.fromkeys(re.findall(r"#(\w+)", caption)):
        tag = tag[:cap_each]
        if total + len(tag) > cap_total:
            break
        tags.append(tag)
        total += len(tag)
    return tags


class Zernio:
    """The Zernio publisher plugin."""

    name = "zernio"
    platforms = ("instagram", "youtube")
    third_party = True
    records_publication = True

    def __init__(self, api_key: str | None = None, base: str | None = None,
                 key_env: str = "ZERNIO_API_KEY"):
        self.api = ZernioAPI(api_key, base, key_env=key_env)

    # ------------------------------------------------------------ protocol
    def available(self) -> Availability:
        if not self.api.has_key():
            return Availability.no(
                f"{self.api.key_env} is not set",
                f"add {self.api.key_env} to .env beside creatoros.yaml",
            )
        try:
            found = self.api.account_ids()
        except ZernioError as exc:
            return Availability.no(f"Zernio is unreachable ({exc})",
                                   "check the key and https://zernio.com status")
        if not found:
            return Availability.no(
                "the key works but no social accounts are connected",
                "connect an account once in the Zernio dashboard",
            )
        return Availability.yes("connected: " + ", ".join(sorted(found)))

    def requires(self, platform: str) -> tuple[str, ...]:
        """What a spec must carry for this platform.

        A Reel with no caption gets no reach and cannot be found; a Short with no title is
        invisible to search. Both have happened here, which is why these are requirements
        rather than suggestions.
        """
        return {"youtube": ("title", "description", "tags"),
                "instagram": ("caption",)}.get(platform, ())

    def publish(self, request: PublishRequest) -> PublishResult:
        video = os.path.abspath(request.video)
        if not os.path.exists(video):
            raise ZernioError(f"no such file: {video}")
        wanted = [p for p in request.platforms if p]
        spec = request.spec or load_spec(video)
        meta = spec.get("platforms") or {}
        ig_meta, yt_meta = meta.get("instagram") or {}, meta.get("youtube") or {}

        caption = ""
        if os.path.exists(_caption_file(video)):
            caption = Path(_caption_file(video)).read_text(encoding="utf-8").strip()
        ig_caption = ig_meta.get("caption") or caption
        yt_description = yt_meta.get("description") or caption
        title = yt_meta.get("title") or derive_title(caption, os.path.basename(video), spec)
        tags = yt_meta.get("tags") or derive_tags(caption)
        if "youtube" in wanted and not yt_meta.get("title"):
            print("!! no platforms.youtube.title in the spec — deriving one from the hook, "
                  "which makes a weak search title")

        # A Reel with no caption gets no reach and cannot be found, so refuse rather than
        # post a blank one. This has happened: the shorts specs carried only
        # platforms.youtube until 2026-11-16.
        if "instagram" in wanted and not ig_caption.strip():
            raise ZernioError(
                "refusing to post to Instagram with an empty caption: the spec has no "
                "platforms.instagram.caption and there is no sibling .caption.txt"
            )

        size = os.path.getsize(video)
        if request.dry_run:
            found = {p: f"dry-run-{p}" for p in wanted}
        else:
            found = self.api.account_ids()
        for platform in wanted:
            if platform not in found:
                raise ZernioError(
                    f"{platform!r} is not connected in Zernio (have: {sorted(found)})")
            limit = LIMITS.get(platform, {})
            if limit.get("bytes") and size > limit["bytes"]:
                raise ZernioError(f"{size / 1e6:.0f} MB exceeds {platform}'s limit")
            body = ig_caption if platform == "instagram" else yt_description
            if limit.get("caption") and len(body) > limit["caption"]:
                print(f"!! text {len(body)} > {platform} max {limit['caption']}; "
                      f"it will be truncated for {platform}")

        entries = []
        for platform in wanted:
            entry = {"platform": platform, "accountId": found[platform]}
            if platform == "youtube":
                entry["platformSpecificData"] = {
                    "title": title[:LIMITS["youtube"]["title"]],
                    "visibility": request.spec.get("_visibility", "public")
                    if request.spec else "public",
                    "categoryId": YT_CATEGORY_SCI_TECH,
                    "madeForKids": False,
                    # The narration is an AI voice. This is a disclosure obligation, not a
                    # preference, so it is set here rather than left to a caller.
                    "containsSyntheticMedia": True,
                }
                entry["customContent"] = yt_description[:LIMITS["youtube"]["caption"]]
            if platform == "instagram":
                entry["customContent"] = ig_caption[:LIMITS["instagram"]["caption"]]
            entries.append(entry)

        if request.dry_run:
            print(f"targets: {', '.join(wanted)}\ntitle:   {title!r} ({len(title)} chars)\n"
                  f"tags:    {tags}\nmode:    {request.mode} at={request.at} tz={request.tz}\n"
                  f"bytes:   {size}")
            return PublishResult(
                ok=True, mode=request.mode, dry_run=True,
                outcomes=[PlatformOutcome(p, True) for p in wanted])

        pre = self.api.upload(video)
        print(f"uploaded {size / 1e6:.1f} MB")

        payload = {
            "content": ig_caption or yt_description,
            "title": os.path.basename(video),
            "mediaItems": [{"url": pre["publicUrl"], "type": "video"}],
            "platforms": entries,
        }
        if tags:
            payload["tags"] = tags
        if request.mode == "draft":
            payload["isDraft"] = True
        elif request.mode == "now":
            payload["publishNow"] = True
        else:
            payload.update(scheduledFor=request.at, timezone=request.tz)

        status, res = self.api.create_post(payload)
        post = res.get("post", res)
        outcomes = []
        reported = {p.get("platform"): p for p in (post.get("platforms") or [])}
        for platform in wanted:
            detail = reported.get(platform) or {}
            error = str(detail.get("error") or detail.get("errorCategory") or "")
            state = str(detail.get("status", "")).lower()
            outcomes.append(PlatformOutcome(
                platform=platform,
                ok=not error and state not in FAILED_STATUSES,
                url=detail.get("platformPostUrl", ""),
                post_id=str(post.get("_id") or ""),
                error=error,
            ))
        if status == 207:
            print("!! HTTP 207 = created, but an inline publish did not complete")
        return PublishResult(ok=all(o.ok for o in outcomes), mode=request.mode,
                             post_id=str(post.get("_id") or ""), outcomes=outcomes)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m creatoros_autopilot.providers.publish.zernio")
    parser.add_argument("video")
    parser.add_argument("--platforms", default="instagram,youtube")
    parser.add_argument("--mode", default="draft", choices=["draft", "now", "schedule"])
    parser.add_argument("--at")
    parser.add_argument("--tz", default="UTC")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    request = PublishRequest(
        video=args.video, job_id="",
        platforms=tuple(p.strip() for p in args.platforms.split(",") if p.strip()),
        mode=args.mode, at=args.at, tz=args.tz, dry_run=args.dry_run)
    try:
        result = Zernio().publish(request)
    except ZernioError as exc:
        print(f"publish failed: {exc}", file=sys.stderr)
        return 1
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
