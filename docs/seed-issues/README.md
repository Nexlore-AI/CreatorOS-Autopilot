# Seed issues

Work that is scoped, has a real starting point, and does not require knowing the whole
codebase. Each file is a draft ready to be pasted into GitHub as an issue.

Every file path and function name in these was checked against the tree. Where something
depends on a third-party API that could not be verified from here, the issue says
"check whether…" rather than asserting it — if you find one that is wrong, that correction
is itself a welcome pull request.

Read [CONTRIBUTING.md](../../CONTRIBUTING.md) first. The short version: a provider is one
file plus one entry point with no change to `core/`, `available()` returns an
`Availability` with a fix the user can paste, and failing loudly beats continuing with a
wrong value.

## Good first issues

An afternoon for someone who has not seen this codebase before.

| # | What | Labels |
|---|---|---|
| [01](01-research-hackernews.md) | "Research source: Hacker News top stories" | good first issue, provider |
| [02](02-research-reddit.md) | "Research source: Reddit (hot/top from the subreddits you name)" | good first issue, provider |
| [04](04-tts-openai.md) | Add an OpenAI TTS backend (creatoros.tts) | good first issue, provider |
| [09](09-publish-mastodon-bluesky.md) | Publish to Mastodon and/or Bluesky — open APIs, no app review, no cost | good first issue, provider |
| [11](11-gate-hook-score.md) | A hook-score gate that wraps the existing scorer | good first issue, provider |
| [12](12-gate-reading-level.md) | "A reading-level gate: narration too dense to speak in the time the scene has" | good first issue, provider |
| [13](13-cli-json-output.md) | Add `--json` to `creatoros doctor`, `creatoros status` and `creatoros brands` | good first issue, enhancement |
| [15](15-cli-preview-open.md) | `creatoros preview --open`, and a clickable path from `creatoros render` | good first issue, enhancement |
| [16](16-preset-new-language.md) | Add an example preset for a language that is not yet represented | good first issue, docs |
| [17](17-docs-quickstart.md) | Write QUICKSTART.md — from install to a first video in ten minutes | good first issue, docs |

## Help wanted

Larger, or needing an account, a device or an API nobody here can test against.

| # | What | Labels |
|---|---|---|
| [03](03-research-youtube-trending.md) | "Research source: YouTube trending and competitor uploads (Data API v3)" | help wanted, provider |
| [05](05-tts-kokoro.md) | Add a Kokoro TTS backend (free, local, offline) | help wanted, provider |
| [06](06-tts-voice-installer.md) | creatoros voices — list, install and verify Piper voices | help wanted |
| [07](07-publish-tiktok.md) | Publish to TikTok with the Content Posting API | help wanted, provider |
| [08](08-publish-linkedin.md) | Publish native video to LinkedIn, to a personal profile or an organisation page | help wanted, provider |
| [10](10-style-minimal.md) | "A second video style: plain typographic, no grid, no cards" | help wanted, provider, enhancement |
| [14](14-windows-polish.md) | Windows polish — run the first-run path on a real machine and fix what breaks | help wanted, windows |
| [18](18-spec-schema-versioning.md) | Version the spec format with `spec_version` and a pydantic schema | help wanted |

## The most useful thing you could do

Two of the publishers — `youtube` and `instagram` — are written, unit-tested, and have
**never run against the live APIs**, because the maintainer has no OAuth app for one and no
Business account for the other. Running either once and reporting what breaks is worth more
than any new feature on this list.
