---
title: Publish to Mastodon and/or Bluesky — open APIs, no app review, no cost
labels: good first issue, provider
---

Write a `Publisher` plugin for Mastodon, for Bluesky, or for both. Both have open APIs you
can call today with a token you create yourself: no developer app to register, no review
queue, no subscription. That makes this the most approachable real publisher on the list —
you can have it posting to your own account within an hour of cloning the repo.

**Scope honestly:** one platform is an afternoon, which is what `good first issue` is meant
to mean here. Both is two afternoons. Do Mastodon first if you want the shorter path, and
open the PR for one platform rather than waiting until both work — they are separate classes
and separate entry points anyway, because the credentials and the `available()` check have
nothing in common.

## Why this matters

Every other publisher in this repo costs something: Zernio is a paid relay, Instagram needs
a Business account linked to a Facebook Page, YouTube needs a Google Cloud OAuth client you
have to create, TikTok needs an approved app and an audit. The free path through this project
currently ends at `local_folder`, which writes the video to a folder and posts nothing. This
issue is what makes "install it, render something, watch it actually post" true with no
account that costs money or needs permission — which is the feature that decides whether
anyone tries this project at all.

## Where to start

- `creatoros_autopilot/interfaces/publish.py` — `Publisher`, `PublishRequest`,
  `PublishResult`, `PlatformOutcome`, and the `DRAFT` / `SCHEDULE` / `NOW` constants. The
  `PlatformOutcome` docstring says why outcomes are per platform.
- `creatoros_autopilot/interfaces/availability.py` — `Availability.yes(detail)` and
  `.no(detail, fix)`. Never a bool;
  `tests/test_plugins.py::test_available_returns_an_availability_with_a_fix` fails a
  provider that says no without a `fix`.
- **Copy `creatoros_autopilot/providers/publish/instagram.py`.** Mastodon's media upload is
  asynchronous in exactly the way Instagram's container is, so `Instagram._wait()`,
  `_retrying()`, `_caption()` and the refusal block at the top of `publish()` all transfer
  nearly unchanged.
- `creatoros_autopilot/providers/publish/local_folder.py` for the simpler end: how a
  publisher reads the spec's per-platform entry, falls back to the video's
  `.caption.txt` sibling, and returns outcomes without raising.
- `docs/plugins/first-plugin.md`, section "Writing a publisher: the traps".
- `tests/test_provider_instagram.py` — a fake API on `127.0.0.1` via
  `ThreadingHTTPServer`, with `module.HOST` monkeypatched at it. Copy this wholesale; it is
  how you prove which calls were made.
- New files: `creatoros_autopilot/providers/publish/mastodon.py` and/or
  `bluesky.py`, plus one line each in `pyproject.toml` under
  `[project.entry-points."creatoros.publish"]`. **No change to `core/`** —
  `tests/test_plugins.py::test_core_names_no_provider` reads the AST of `core/` and fails if
  it names a provider. `core.media.require("ffprobe")` and `core.media.duration()` are there
  if you need to probe the file.

**Mastodon**, verified against [the media](https://docs.joinmastodon.org/methods/media/) and
[statuses](https://docs.joinmastodon.org/methods/statuses/) docs today:

1. `POST /api/v2/media` with scope `write:media`. **200** means the file was processed
   synchronously; **202** means it is still processing and `url` is `null`.
2. While it is processing, poll `GET /api/v1/media/:id`: **200** with a `url` when it is
   done, **206** while it is still going, **422** if processing failed.
3. `POST /api/v1/statuses` with scope `write:statuses` and `media_ids[]`. It accepts an
   `Idempotency-Key` header (kept about an hour), and `scheduled_at` at least five minutes
   in the future.
4. The token is an application token you make in Preferences → Development → New
   application on your own instance. The base URL is per instance, so it is configuration,
   not a constant: `MASTODON_BASE_URL` and `MASTODON_ACCESS_TOKEN` are reasonable names.
5. `GET /api/v2/instance` → `configuration.media_attachments` is where that instance's real
   limits live. Read it in `available()` and put the numbers in `Availability.detail`.

**Bluesky**, verified against the Bluesky video tutorial (the docs moved from
`docs.bsky.app` to `bsky.network/docs`) and
[the rate-limits page](https://docs.bsky.app/docs/advanced-guides/rate-limits):

1. `com.atproto.server.createSession` with a handle and an **app password** (not the account
   password) for the access JWT.
2. The simple route is `com.atproto.repo.uploadBlob` to your PDS. The tutorial notes the
   drawback: processing only starts once the post is submitted, so viewers can briefly see a
   post with a missing video.
3. The recommended route is `com.atproto.server.getServiceAuth` (audience `did:web:<your
   PDS host>`, `lxm` `com.atproto.repo.uploadBlob`, roughly 30 minutes) → `POST
   https://video.bsky.app/xrpc/app.bsky.video.uploadVideo?did=…&name=…` with the bytes →
   poll `app.bsky.video.getJobStatus` until a blob reference appears.
4. `com.atproto.repo.createRecord`, collection `app.bsky.feed.post`, with an embed of
   `$type: app.bsky.embed.video`, the blob, and an `aspectRatio`.
5. `app.bsky.video.getUploadLimits` returns whether the account can upload and how much of
   the daily quota is left — the ideal `available()` check.

## What done looks like

- [ ] One file per platform plus one `creatoros.publish` entry point each, and no diff in
      `creatoros_autopilot/core/`.
- [ ] `name`, `platforms = ("mastodon",)` / `("bluesky",)`, `third_party = False`, and `records_publication` — note that `tests/test_plugins.py::TestPracticeRuns::test_every_real_publisher_does` requires `True` for every publisher except `local_folder`, so if a success here would not mean the video is on the platform, that is a conversation for the PR rather than a quiet `False`.
- [ ] `available()` returns an `Availability` with a pasteable `fix` for each missing piece,
      and reports something real in `detail` — the instance's media limits for Mastodon, the
      remaining daily video quota for Bluesky.
- [ ] Import-time purity: no network, no subprocess, no `os.environ[...]` at import
      (`tests/test_imports.py`); credentials read inside the call, as `Instagram._token()`
      does, so a rotated token does not need a restart.
- [ ] `request.dry_run` makes zero state-changing calls — uploading media is one, even
      though nothing is posted.
- [ ] `requires("mastodon")` / `requires("bluesky")` returns `("caption",)` or whatever you
      actually read from `spec["platforms"][<name>]`, and an empty caption is refused rather
      than posted.
- [ ] A `PlatformOutcome` on every path, with the status id or record URI in `post_id` and a
      real permalink in `url`.
- [ ] `mode: draft` and `mode: schedule` are implemented or refused with an error naming
      what does work.
- [ ] Tests against a fake server on localhost: media still processing, media processing
      failed (Mastodon's 422), and a post that was never created does not report ok.

## Gotchas

**Video support differs between the two platforms and between Mastodon instances.** Mastodon
supports video, and the docs say larger formats (video, gifv, audio) keep being processed
asynchronously and return 202 — but the size and format limits are per instance, which is
what `GET /api/v2/instance` → `configuration.media_attachments` is for. Read them; do not
hardcode them, and do not assume the instance you tested on is typical. For Bluesky, check
`app.bsky.video.getUploadLimits` rather than trusting any number you read in a blog post:
the official rate-limits page gives a PDS per-upload maximum of 52,428,800 bytes (50 MB),
third-party sites repeat 50 MB / 60 seconds and also 300 MB / 3 minutes, and an atproto blog
post describes a plan to raise defaults and advertise the maximum via `describeServer`. I
could not establish which is live today — so query it at runtime and put the answer in
`Availability.detail` instead of writing a constant nobody will update. Bluesky-hosted
accounts also have to verify their email before their first video upload, which is a "no"
with a very specific `fix`.

**A 202 is not a success.** `POST /api/v2/media` returning 202 means the attachment exists
and the file is still being processed, with `url` still `null`. Attaching it to a status
straight away is the same trap that cost this project a day of Reels: `instagram.py` created
a container, reported ok, the ledger marked the platform done, and the real publish skipped
that job forever (see the `mode: draft` paragraph in its docstring). Poll
`GET /api/v1/media/:id` until 200, treat 422 as a real failure with its reason, and treat
"still 206 when my deadline expired" as a third thing — nothing was posted, the attachment
id is worth printing, and `Instagram._wait()` is the model for all three.

**On Bluesky an error can carry the answer.** `app.bsky.video.getJobStatus` can come back
with `already_exists` — an error — and still include the blob reference for a video that was
processed earlier. So check for a blob regardless of the job's outcome. This is the one place
in these two APIs where failing loudly on an error code would be the bug.

**Mastodon's `scheduled_at` does not give you back a status.** It returns a `ScheduledStatus`,
whose id is not a status id and cannot later be fetched as one, and there is no URL yet
because nothing has been posted. If you implement `mode: schedule`, be explicit about what
goes in the ledger: a scheduled id that no lookup will resolve is worse than no id. The
simplest correct first version refuses `schedule` and says why, copying the wording of
`instagram.py`'s refusal — but note the consequence there, because the shipped
`publish.offset_days: 1` makes every slot a day away, so `core.schedule.decide()` returns
`schedule` for every video and a backend that refuses it refuses everything. Say
`offset_days: 0` in the error text. Bluesky has no scheduling at all —
`com.atproto.repo.createRecord` is immediate — so refuse it there, and do not approximate it
by sleeping until the slot.

**Retry the Mastodon status, do not retry the Bluesky record.** Mastodon's
`POST /api/v1/statuses` takes an `Idempotency-Key` header, so with a key derived from the job
id it is genuinely safe to repeat — that is a rarity worth using. Bluesky's `createRecord`
has no such header that I could verify; check whether supplying your own `rkey`, or using
`putRecord`, gives you the same guarantee, and if you are not sure, do not retry it. A
duplicate post is not free. `instagram.py` marks this boundary in code:
`_retrying()` for safe calls, a bare `_call()` for the one that is not, with a comment saying
why.

**Count characters the way the platform counts them.** Mastodon's per-instance limit is in
`configuration.statuses.max_characters`; Bluesky's `app.bsky.feed.post` text limit is
commonly quoted as 300 graphemes / 3000 bytes — confirm it in the lexicon. Graphemes are not
`len(text)`, and that is not academic for this pipeline: the shipped example channel's
captions mix Devanagari and English, where codepoints, graphemes and UTF-8 bytes are three
different numbers. If you trim, print that you trimmed and cut on a word boundary, the way
`Instagram._caption()` and `_trim()` do.

**Pass the aspect ratio.** The renderer's output is 1080×1920 vertical. Bluesky's embed takes
an explicit `aspectRatio`; leave it out and the client has to guess, which is how a vertical
video ends up letterboxed in the feed. `core.media.require("ffprobe")` is available if you
would rather read it off the file than hardcode it.

**`publish.mastodon:` in `creatoros.yaml` will raise a config error.**
`core/settings.py::_build` rejects unknown keys on purpose, so there is no per-platform
settings block to add without editing core. Credentials go in `.env`; per-post values go in
the spec under `platforms.<name>` and are declared by `requires()`. And if your backend can
be configured to post nothing — don't. `records_publication = False` exists for the backend
that honestly does not publish (`local_folder`); a real publisher that returns ok without
posting is the bug this whole project is organised around.
