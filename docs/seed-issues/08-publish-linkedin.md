---
title: Publish native video to LinkedIn, to a personal profile or an organisation page
labels: help wanted, provider
---

Write a `Publisher` plugin that uploads the rendered MP4 to LinkedIn as native video and
creates the post, for either a member's own feed or a company page. Part of the job is
verification rather than code: `platforms/linkedin.json` in this repo carries limits marked
`"verified": false` and a `"todo": "confirm limits and Zernio support before enabling"`, so
nobody has actually checked them.

## Why this matters

A short technical explainer is one of the few things that still travels on LinkedIn, and it
is the one platform in this project's list where the audience is professional rather than
algorithmic. Right now there is no way to reach it at all: no backend claims `linkedin`, and
`platforms/linkedin.json` has `"enabled": false`. A direct backend also means the numbers in
that file stop being a guess — which matters, because a caption trimmed against a wrong
limit is a caption cut in the wrong place.

**Size: this is not an afternoon.** Three APIs (initialize the upload, send the parts,
finalise it; then create the post), a three-legged OAuth flow, page-role permissions that
differ from member permissions, and a version header that expires. Budget a weekend, and
expect the permissions to be the slow part.

## Where to start

- `creatoros_autopilot/interfaces/publish.py` — the `Publisher` protocol, `PublishRequest`,
  `PublishResult`, `PlatformOutcome`, and `DRAFT` / `SCHEDULE` / `NOW`.
- `creatoros_autopilot/interfaces/availability.py` — `Availability.no(detail, fix)`.
- **Copy `creatoros_autopilot/providers/publish/youtube.py` for the upload, and
  `creatoros_autopilot/providers/publish/instagram.py` for the refusals.** YouTube is the
  existing example of a multi-step upload with real resumption discipline and of OAuth
  credentials read from the environment on every call (`YOUTUBE_CLIENT_ID`,
  `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`); Instagram is the example of refusing a
  mode the API cannot honour with an error that names what does work.
- `creatoros_autopilot/providers/publish/zernio_client.py` — its `LIMITS` dict, and
  `youtube.py`'s, are where a per-platform limits table lives in this codebase today.
  Nothing in the package reads `platforms/*.json`; it is referenced only by `STRUCTURE.md`
  and `docs/ROADMAP.md`. So decide where your limits live and say so in the PR.
- `docs/plugins/first-plugin.md`, section "Writing a publisher: the traps".
- `tests/test_provider_youtube.py` and `tests/test_provider_instagram.py` — both test
  against a fake API on localhost, which is how you build this without a LinkedIn app.
- New file: `creatoros_autopilot/providers/publish/linkedin.py`, plus one line in
  `pyproject.toml` under `[project.entry-points."creatoros.publish"]`. **No change to
  `core/`** — `tests/test_plugins.py::test_core_names_no_provider` enforces it.

Which API is current, verified against Microsoft's own docs today:

- **The Posts API replaces ugcPosts.** `POST https://api.linkedin.com/rest/posts`, and the
  docs state it plainly: "The Posts API replaces the ugcPosts API"
  ([Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api)).
  Required headers: `Linkedin-Version: {YYYYMM}` and `X-Restli-Protocol-Version: 2.0.0`.
- **The Videos API replaces the Assets API** for the upload:
  `initializeUpload` → upload each part → `finalizeUpload`, which yields a
  `urn:li:video:...`
  ([migration guide](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/contentapi-migration-guide)).
  `finalizeUpload` takes the per-part ETags and the upload token.
- The post then references it as `content.media.id` (with an optional `content.media.title`),
  alongside `author`, `commentary`, `visibility`, `distribution.feedDistribution` and
  `lifecycleState: PUBLISHED`.
- Permissions: `w_member_social` to post as the authenticated member,
  `w_organization_social` to post as an organisation — and for the organisation the
  authenticated member needs an `ADMINISTRATOR`, `CONTENT_ADMIN` or
  `DIRECT_SPONSORED_CONTENT_POSTER` page role.

## What done looks like

- [ ] `creatoros_autopilot/providers/publish/linkedin.py` plus one `creatoros.publish` entry
      point, and no diff in `creatoros_autopilot/core/`.
- [ ] `name`, `platforms = ("linkedin",)`, `third_party = False`, and `records_publication` — note that `tests/test_plugins.py::TestPracticeRuns::test_every_real_publisher_does` requires `True` for every publisher except `local_folder`, so if a success here would not mean the video is on the platform, that is a conversation for the PR rather than a quiet `False`.
- [ ] `available()` returns an `Availability` that distinguishes "no token" from "token
      present but it cannot post as that author", with a pasteable `fix` for each.
- [ ] Import-time purity: no network, no subprocess, no `os.environ[...]` at import
      (`tests/test_imports.py`), and constructing the class touches nothing.
- [ ] `request.dry_run` makes zero state-changing calls — `initializeUpload` is one.
- [ ] `requires("linkedin")` returns the spec keys you need under
      `spec["platforms"]["linkedin"]` — the commentary text, and the author URN if you take
      it from the spec rather than the environment.
- [ ] A `PlatformOutcome` for linkedin on every path, with the post URN in `post_id`.
- [ ] `platforms/linkedin.json` updated: `post_max`, `fold_chars` and the video `formats`
      block checked against LinkedIn's current docs, `"verified"` flipped to `true` only for
      what you actually checked, and the `todo` line replaced with what you found. Leave a
      field `false` rather than guessing it.
- [ ] Tests against a fake LinkedIn on localhost: a member post, an organisation post, a
      part that fails mid-upload, and a video that never becomes usable.

## Gotchas

**The author URN decides where the post lands, and getting it wrong posts to the wrong
place.** `"author": "urn:li:person:{id}"` is the member's own feed;
`"author": "urn:li:organization:{id}"` is the company page. These are not interchangeable
and nothing in the payload hints at which you meant. The failure mode that matters is not
the 400 (`INVALID_URN_TYPE`) or the 403 (`ACCESS_DENIED`) — those are loud and fine. It is
the one that succeeds: a token that holds `w_member_social` will cheerfully post to the
member's own profile when the user meant the company page, and the run reports a published
post with a working URL. So take the author URN explicitly from config or the spec, refuse
to infer it, and refuse an ambiguous configuration instead of defaulting to the person.
Print which author you are posting as before you post.

**Multi-part upload: every part's ETag, or the video is silently wrong.**
`initializeUpload` hands back a list of parts with byte ranges and upload URLs, and
`finalizeUpload` wants the ETag from each response. A part that failed and was skipped
produces a finalise that either errors or yields a video that never becomes usable — and the
tempting shortcut, carrying on with the ETags you did collect, is precisely the shape of bug
this project keeps having. If you did not get every ETag, fail, and say which part.

**A video URN exists before the video is usable.** The URN comes back from the upload, and
creating the post immediately can produce a post with a broken video. Check the Videos API's
status field and poll it before creating the post (third-party guides say the value to wait
for is `AVAILABLE`; confirm that against the Videos API reference, I could not verify the
exact enum). Treat "still processing when the deadline expired" as its own outcome, carrying
the video URN, the way `Instagram._wait()` returns `timeout` with the container id — nothing
has been posted, and the URN is worth keeping rather than re-uploading 20 MB.

**The post id is in a response header, not the body.** A successful create returns `201`
with the id in the `x-restli-id` response header (`urn:li:share:...` or `urn:li:ugcPost:...`).
Read the body and you get an empty `post_id`, which means a ledger row that leads nowhere —
and the ledger is how a re-run knows not to post twice.

**There is no draft and no scheduled publish here.** The Posts API docs say `PUBLISHED` is
the only `lifecycleState` accepted at creation; `DRAFT` only appears in responses. So both
`mode: draft` and `mode: schedule` have to be refused, and the refusal has to name what does
work — copy the wording and the reasoning from `instagram.py`'s `POST_FROM_HERE` constant.
Note why this matters more than it sounds: the shipped `publish.offset_days: 1` means every
slot is a day away, so `core.schedule.decide()` returns `schedule` for *every* video. A
backend that refuses `schedule` refuses everything until the user also sets
`offset_days: 0`. Say that in the error, because an error whose instructions cannot be
followed is the same as no error at all.

**Pin the version header, and let the environment override the pin.** `Linkedin-Version` is
required, and versions sunset — the docs currently warn that Marketing Version 202601 is
sunset on 15 January 2027. Do what `instagram.py` does with `DEFAULT_VERSION` and
`INSTAGRAM_GRAPH_VERSION`: a constant with a comment, overridable without a release.

**The limits in `platforms/linkedin.json` are unverified, and a wrong limit is a silently
mangled post.** `post_max: 3000` and `fold_chars: 210` are somebody's recollection. Check
them, and when you do trim a caption, print that you trimmed it and cut on a word boundary —
`Instagram._caption()` and `_trim()` show the house pattern. An empty caption should be
refused outright, not posted, for the same reason it is on Instagram.

**`publish.linkedin:` in `creatoros.yaml` will raise a config error.**
`core/settings.py::_build` rejects unknown keys deliberately. Per-post data goes in the spec
under `platforms.linkedin` and is declared by `requires()`; credentials go in `.env`. If you
think the wizard should collect them, `creatoros_autopilot/cli/init_wizard.py` has a
`SECRETS` dict — that is in `cli/`, not `core/`, so it is allowed, but keep it a separate
commit.
