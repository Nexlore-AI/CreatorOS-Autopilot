---
title: Publish to TikTok with the Content Posting API
labels: help wanted, provider
---

Write a `Publisher` plugin that posts a finished 1080×1920 MP4 to TikTok, so the render that
already goes to Instagram and YouTube reaches the third place vertical video belongs. Today
nothing in this project reaches TikTok: `plugins.publisher_for("tiktok")` raises
`PluginError` unless you name `local_folder` in `publish.backends`, and `local_folder`
writes the file and posts nothing.

## Why this matters

The pipeline renders one vertical video and the only way to get it onto TikTok right now is
to upload it by hand, or to pay a relay that does it for you. A direct backend means the
same MP4, the same caption and the same slot reach a third platform with no subscription in
the middle — which is the whole argument for the plugin layer existing.

Be warned before you start: **this is the slowest of the three publisher issues, and the
delay is not code.** A TikTok developer app must be registered and approved, and then a
separate audit is needed before anything it posts can be seen by anyone. The code is a
day; the approval is not. If you want a publisher you can finish in an afternoon, take
`09-publish-mastodon-bluesky.md` instead.

## Where to start

- `creatoros_autopilot/interfaces/publish.py` — the `Publisher` protocol, plus
  `PublishRequest`, `PublishResult`, `PlatformOutcome` and the `DRAFT` / `SCHEDULE` / `NOW`
  constants. Read the `PlatformOutcome` docstring; it explains why outcomes are per
  platform.
- `creatoros_autopilot/interfaces/availability.py` — `Availability.yes()` / `.no(detail, fix)`.
- **Copy `creatoros_autopilot/providers/publish/instagram.py`.** It is the closest existing
  example by a distance: same shape of problem (asynchronous server-side processing, a
  separate "now actually post it" call, no draft, no schedule). Specifically reuse the
  discipline in `Instagram._retrying()`, `Instagram._wait()` (poll with backoff, return
  `finished` / `error` / `timeout` as three different things), `Instagram._video_url()` and
  the refusal structure at the top of `Instagram.publish()`.
- `docs/plugins/first-plugin.md`, section "Writing a publisher: the traps".
- `tests/test_provider_instagram.py` — the test pattern to copy. It starts a
  `ThreadingHTTPServer` on localhost that pretends to be the Graph API and monkeypatches
  `module.HOST` at it, so every assertion is about *which calls were actually made*. You can
  build and test this whole backend with no TikTok account at all.
- New file: `creatoros_autopilot/providers/publish/tiktok.py`. One entry point in
  `pyproject.toml` under `[project.entry-points."creatoros.publish"]`, beside the existing
  `instagram = ...` line. **No change to `core/`** —
  `tests/test_plugins.py::test_core_names_no_provider` reads the AST of every file in
  `core/` and fails if it so much as names a provider.

The API, as the docs read today (verify each against the current reference before you
rely on it — this is the API most likely to have moved under you):

1. `POST /v2/post/publish/creator_info/query/` — required first for a direct post, and it
   returns `privacy_level_options` for *that* creator.
2. `POST /v2/post/publish/video/init/` — `source_info.source` is either `FILE_UPLOAD`
   (chunked `PUT` with `Content-Range`) or `PULL_FROM_URL` (TikTok's servers fetch a public
   URL, exactly like the Instagram Graph API does — `Instagram._video_url()` already maps a
   local render to a public URL through `CREATOROS_PUBLIC_BASE_URL` /
   `CREATOROS_PUBLIC_ROOT`, and that logic is worth lifting rather than rewriting).
3. `POST /v2/post/publish/status/fetch/` with the `publish_id` — poll until terminal.

Scopes: `video.publish` for the direct post, `video.upload` for the variant that drops a
draft into the creator's inbox. Check whether app review for the scope and the Content
Posting audit are two separate approvals — third-party write-ups say they are, and TikTok's
own pages I could find do not say so plainly.

## What done looks like

- [ ] `creatoros_autopilot/providers/publish/tiktok.py` plus one `creatoros.publish` entry
      point, and no diff anywhere in `creatoros_autopilot/core/`.
- [ ] `name`, `platforms = ("tiktok",)`, `third_party = False`, and `records_publication` — note that `tests/test_plugins.py::TestPracticeRuns::test_every_real_publisher_does` requires `True` for every publisher except `local_folder`, so if a success here would not mean the video is on the platform, that is a conversation for the PR rather than a quiet `False`.
- [ ] `available()` returns an `Availability`; when it says no, `fix` names the exact
      environment variable and what to paste.
- [ ] Importing the module works with no network, no subprocess and no `os.environ[...]` at
      import time (`tests/test_imports.py`), and constructing the class touches nothing —
      see `test_constructing_it_touches_nothing` in `tests/test_provider_instagram.py`.
- [ ] `request.dry_run` makes **zero** state-changing calls — not even `video/init/`.
- [ ] `requires("tiktok")` returns the spec keys you actually need under
      `spec["platforms"]["tiktok"]` (the caption/title field and `privacy_level` at minimum;
      check the Direct Post reference for the exact field names).
- [ ] One `PlatformOutcome` for tiktok on every path, success or failure.
- [ ] `mode: draft` and `mode: schedule` are either implemented or **refused with an error
      that names what does work**, never approximated.
- [ ] Tests against a fake TikTok on localhost, including: a failed upload does not report
      ok, and a post still processing does not report ok.
- [ ] A short paragraph at the top of the module saying what this backend cannot do, in the
      style of the `instagram.py` and `youtube.py` docstrings.

## Gotchas

**`publish_id` is not a post.** `video/init/` returns immediately and the video is still
being processed, uploaded or moderated. Returning `ok=True` at that point is the exact bug
that has already cost this project a day of Reels: the Instagram backend created a container,
reported success, the ledger marked instagram done, and the real publish skipped that job
forever while nothing had been posted (see the `mode: draft` paragraph in
`instagram.py`'s docstring, and `core/pipeline.py` around the `practice` set). Poll
`status/fetch/` until the status is terminal. A status that is still `PROCESSING_*` when your
deadline expires is **not** a failure and **not** a success — report it as its own thing,
carrying the `publish_id`, the way `Instagram._wait()` returns `("timeout", ...)` with the
container id so a human can finish the job instead of re-rendering.

**An unaudited app can only post to private accounts.** TikTok's docs say all content posted
by unaudited clients is restricted to private viewing mode, that every account posting
through such a client must be set to private at the time of posting, and that unaudited
clients can serve at most 5 users in a 24-hour window; the publish attempt is blocked at
`video/init/` with `unaudited_client_can_only_post_to_private_accounts`
([content sharing guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines),
[direct post reference](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post)).
Check the current wording before you write the error message — this is a policy, not a
constant, and it changes. What must not happen is a run that reports four videos published
to TikTok when all four are visible only to their owner. Say it in `available().detail`, and
if the API tells you the post landed `SELF_ONLY`, say that in the outcome rather than
printing a URL nobody can open.

**Do not hardcode `privacy_level`.** It must be one of the `privacy_level_options` that
`creator_info/query/` returned for that creator, which differ between a public and a private
account; a mismatch is rejected (`privacy_level_option_mismatch`). Read the options, and
refuse a spec that asks for one the creator cannot use — with the list in the error.

**Do not wrap the whole flow in `_retrying`.** Check whether `video/init/` is idempotent.
If it is not, a retry after a dropped socket can start a second publish of a video that is
already on its way, and TikTok has no delete-and-repost that is free. `instagram.py` draws
this line explicitly: `_retrying()` for safe calls, a bare `_call()` for `media_publish`
with a comment saying why it is never repeated. Draw the same line here and write down
which side each call is on.

**Chunked upload bookkeeping.** With `FILE_UPLOAD` you promise a `video_size`, a
`chunk_size` and a `total_chunk_count` up front and then have to deliver exactly that, with
correct `Content-Range` headers. The trap is the attempt that gives up: a partial upload
that stops retrying must not fall through to "post created". `youtube.py`'s docstring has
the general rule — the upload session is the only idempotency the API offers, so a dropped
connection is answered by asking the existing session what it holds, never by opening a new
one.

**`publish.tiktok:` in `creatoros.yaml` will not work.** `core/settings.py::_build` rejects
unknown keys on purpose (a silently ignored `publsh:` once meant someone believing they had
set draft mode). Adding a `tiktok:` settings block means editing core, which is what this
plugin layer exists to avoid. Per-post values belong in the spec under
`platforms.tiktok` and in `requires()`; secrets belong in `.env`.

**The narration is synthetic and that is a disclosure, not a preference.** Every video this
pipeline makes is voiced by a synthetic voice. `youtube.py` prints a loud line on every
publish because the Data API has no field for it. Check whether the Content Posting API can
set TikTok's AI-generated content label; if it can, set it, and if it cannot, print it the
way `youtube.py` does. `docs/plugins/first-plugin.md` puts it bluntly: never drop a
disclosure silently.
