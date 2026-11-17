---
title: "Research source: YouTube trending and competitor uploads (Data API v3)"
labels: help wanted, provider
---

Add a `creatoros.research` provider that reads YouTube's own trending chart
(`videos.list` with `chart=mostPopular`) and a named channel's recent uploads, mapping view
counts onto `Item.score`. This is `help wanted` rather than `good first issue`: it needs a
Google Cloud project before you can run it once, and the quota budget is a design
constraint rather than a detail.

**It is not part of the keyless path.** The Data API costs no money, but it needs a Google
Cloud project and an API key, so unlike `rss`, `arxiv` and `huggingface_papers` this source
cannot work out of the box. Say that in the module docstring and in `available()`'s `fix`.
`CONTRIBUTING.md` promises that a clone needs "no API key", and CI proves it by rendering an
example with an empty environment — this provider must be opt-in and must fail with a clear
explanation when the key is absent, never half-work.

## Why this matters

Every source that ships answers either "what was published" (`arxiv`, `rss`) or "what did a
forum upvote" (`huggingface_papers`). None of them answers "what is doing numbers on the
platform these videos are going to". View count is the strongest popularity signal available
for short-form, and `Item.score` exists for exactly a source that has one — see the comment
in `providers/research/arxiv.py` explaining why its score is always 0.0, and the
`huggingface_papers.py` docstring on why a source with a real signal is worth having.

The competitor half matters for the same reason: a channel that can see which of five
comparable channels posted what, and how it did, can choose a topic on evidence instead of
on a hunch.

## Where to start

- `creatoros_autopilot/interfaces/research.py` — `ResearchSource` needs `name`,
  `available()` and `fetch(*, since, limit, query) -> list[Item]`. `Item` carries `title`,
  `url`, `summary`, `published`, `source`, `score`, `extra`, and `Item.key()` deduplicates
  across sources.
- `creatoros_autopilot/interfaces/availability.py` — `available()` returns an `Availability`,
  never a bool, and a "no" carries a pasteable `fix`.
- **`creatoros_autopilot/providers/publish/youtube.py` for how this repo talks to Google**,
  but only the half that applies. Worth reading: `_req` and `_call` (one HTTP call, and
  backoff on a transport failure or a transient 5xx), `_api_error` (a Google error body as
  one line), the `HINTS` dict (a Google `reason` string mapped to what it actually means in
  practice), and `available()`, which is deliberately offline and checks environment
  variables only. **Do not copy the OAuth half.** That publisher needs
  `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` / `YOUTUBE_REFRESH_TOKEN`, a token exchange
  and a resumable upload because it writes to a channel. A research read is a public read:
  one API key on the query string, and none of that machinery.
- **`creatoros_autopilot/providers/research/huggingface_papers.py` for the body of the
  work** — the closest existing research provider in shape: JSON over `urllib`, a `_get()`
  that funnels every HTTP and transport failure into one exception type, `_item()` returning
  `None` for an unusable entry, `HuggingFaceError` vs `HuggingFaceShape` for "retry" vs "this
  needs a code change", and `_clean` / `_aware` for text and timestamps.
- `creatoros_autopilot/providers/research/arxiv.py` for the constructor pattern: a
  `categories` argument with a `CREATOROS_ARXIV_CATEGORIES` fallback, and an explicit refusal
  to fall back to "everything" when the configuration names nothing.
- New file: `creatoros_autopilot/providers/research/youtube_trending.py`.
- Register it in `pyproject.toml`, in the existing block:

  ```toml
  [project.entry-points."creatoros.research"]
  youtube_trending = "creatoros_autopilot.providers.research.youtube_trending:YouTubeTrending"
  ```
- Tests: `tests/test_provider_huggingface_papers.py` and `tests/test_provider_rss.py` start a
  real `http.server` on `127.0.0.1` rather than monkeypatching `urlopen`.
  `tests/test_provider_arxiv.py` retargets the provider with
  `monkeypatch.setattr(arxiv, "API", server.url)` — so keep every endpoint in a module-level
  constant.
- There is no `creatoros research` subcommand yet and `cli/doctor.py` has no research
  section, so your test plus
  `python -c "from creatoros_autopilot import plugins;
  print(plugins.load('research', 'youtube_trending').available())"` is how you run it by
  hand.

### The two calls, and what they cost

- `videos.list?part=snippet,statistics&chart=mostPopular&regionCode=…&videoCategoryId=…&maxResults=50`
  — the trending chart. 1 quota unit.
- `search.list?part=snippet&channelId=…&order=date&type=video&publishedAfter=<RFC 3339>` —
  a channel's recent uploads. **100 quota units**, against a default daily allowance of
  **10,000**. Six competitor channels, four runs a day, is 2,400 units before you have
  fetched a single view count.
- The usual cheaper route to the same list is `channels.list` (`part=contentDetails`) to get
  `contentDetails.relatedPlaylists.uploads`, then `playlistItems.list` on that playlist, then
  one `videos.list` for the statistics — each of those is in the cheap tier rather than the
  100-unit one. Check the current per-method costs in Google's own quota table before you
  commit to either route, and write what one `fetch()` costs in the module docstring.

## What done looks like

- [ ] `creatoros_autopilot/providers/research/youtube_trending.py` with a class whose `name`
      is `"youtube_trending"`, and nothing under `creatoros_autopilot/core/` touched.
- [ ] The key is read from the environment (e.g. `YOUTUBE_API_KEY`) inside `available()` and
      `fetch()`, never at import, and never logged or echoed. A line for it in `.env.example`,
      with no value.
- [ ] `available()` with no key returns `Availability.no` whose `fix` says where to create
      the key and what line to put in `.env`. With a key present it returns yes **without**
      making a request.
- [ ] Region, category and the competitor channel ids are constructor arguments with
      environment fallbacks, in `arxiv.py`'s argument-then-environment-then-default order.
- [ ] `statistics.viewCount` lands in `Item.score` as a float; `url` is
      `https://www.youtube.com/watch?v=<id>`; `extra` carries the video id, channel id and
      title, and the like and comment counts when they are there.
- [ ] Every `published` is timezone-aware, or `None`.
- [ ] The module docstring states what one `fetch()` costs in quota units, and `fetch()` does
      not paginate without a bound.
- [ ] A 403 `quotaExceeded` is reported as itself, with a `fix`, and is not confused with a
      bad key.
- [ ] An entry point in `pyproject.toml`.
- [ ] `tests/test_provider_youtube_trending.py` against a local server, covering: no key, a
      403 quota body, a 200 with `items` missing, a video whose statistics are hidden, and
      `publishedAt` parsed to an aware datetime.
- [ ] No new third-party dependency. `pytest -q`, `ruff check .` and `mypy` pass.

## Gotchas

**An exhausted quota is a 403, not a 429, and swallowing it is the bug this project keeps
making.** The Data API answers a spent quota with HTTP 403 and a `reason` of `quotaExceeded`
or `dailyLimitExceeded`, which reads like an authentication failure and is not one. Catch it,
return `[]`, and your source reports "nothing is trending today" every day after lunch —
indistinguishable from a working source on a quiet day, which is the exact shape of every
serious bug this repo has had. `publish/youtube.py` already carries a `quotaExceeded` entry in
its `HINTS` dict, explaining what the number means in practice rather than repeating Google's
message; do the same here and raise rather than returning empty. Quota is understood to reset
daily on US Pacific time — check the current docs rather than quoting this issue, and put
whatever you find in the `fix` text, because "wait until tomorrow" is useless without a
"tomorrow".

**Statistics are strings, and some are absent.** `viewCount`, `likeCount` and `commentCount`
arrive as JSON strings (`"1234"`), so they need converting, and a channel that hides its
counts simply does not send the field — `float("")` and a missing key are both live cases. Be
careful what `score=0.0` then means: `arxiv.py`'s comment on this is worth reading, because
0.0 has to mean "this source has no signal for this item" and not "nobody watched it". If you
keep an item whose count is hidden, record that in `extra` and say in the docstring how it
ranks.

**`publishedAt` is RFC 3339 with a trailing `Z`, which Python 3.10 cannot parse.** This
project supports 3.10 and CI runs it, and `dt.datetime.fromisoformat` only learned to read
that `Z` in 3.11. `_when` in `huggingface_papers.py` and in `arxiv.py` both have the
two-line fix. The datetime must then be timezone-aware: `core/schedule.py` raises
`ValueError` on a naive one, and that strictness is there because the shell pipeline this
replaced swallowed a date failure and published four videos at once instead of scheduling
them. `arxiv.py`'s `_aware` docstring records the other half: one naive stamp made a
comparison raise `TypeError` and lost the entire feed, every sound entry included.

**`chart=mostPopular` is not a search, so decide what `query` means and say so.** The chart
takes a region and optionally a category; check whether it accepts a text query at all — the
chart's documented parameters do not appear to include one. If it does not, either filter
client-side after the fetch or route a non-empty `query` to `search.list` and charge the 100
units. What you must not do is accept a `query`, ignore it, and hand back the unfiltered
chart: `_matches` in `huggingface_papers.py` exists because that endpoint accepts a `q`
parameter and silently ignores it, so a search for a word no paper contained came back as the
whole day's list, looking like results. Silently unfiltered results are worse than a rejected
query.

**A "trending" list with no `regionCode` is the API's default region, not the world.** Pin it
in configuration rather than inheriting a default nobody chose. `arxiv.py` refuses an empty
category list rather than falling through to `search_query=all` for precisely this reason —
its `_no_categories` docstring describes a source configured for biology answering with
machine-learning papers while `available()` reported OK. A channel in India ranking US
trending videos is the same failure with a different accent.

**An API key restricted to HTTP referrers is rejected from a server-side call.** The error
reads like an invalid key, so `available()`'s `fix` should mention it: restrict the key by IP
or by API, not by referrer. Check the exact restriction names and the `reason` string in the
Cloud console as they stand now, and if you can reproduce the error, map it in a `HINTS`-style
dict the way `publish/youtube.py` does.

**Truncation must be audible.** If you cap pagination to protect quota — and you should —
print that you did. `huggingface_papers._window` prints a `!!` line when it shortens the
window it was asked for, because truncating in silence makes a cut-short answer look like the
whole answer, and whoever is reading the candidate list cannot tell.

**`available()` must not raise and must not make a request.**
`tests/test_plugins.py::test_available_returns_an_availability_with_a_fix` is parametrised
over every installed `research` provider, calls `available()` with no `try`, and asserts both
`detail` and `fix` when it says no. So an exception there is a red suite — and a live call
there would spend quota on every `pytest` run. `publish/youtube.py`'s `available()` docstring
makes the argument for staying offline: `doctor` is what people run when nothing works yet,
often with no network to hand.

**Importing the module must work offline.** No request, no client construction, no
`os.environ[...]` at import time. `tests/test_imports.py` imports every module in the package
with nothing set and nothing running.

**Don't add `google-api-python-client`.** `CONTRIBUTING.md` lists "a new third-party
dependency without a reason in the PR description" among the things that get sent back —
there are four dependencies and that is a feature. `publish/youtube.py` talks to the same
API with `urllib` and the standard library, and so can this.
