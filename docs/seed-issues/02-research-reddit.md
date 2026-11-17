---
title: "Research source: Reddit (hot/top from the subreddits you name)"
labels: good first issue, provider
---

Add a `creatoros.research` provider that reads the subreddits a user names and returns their
hot or top posts, with the post score as `Item.score`. Reddit's public `.json` endpoints need
no key, so this stays on the path that works with an empty `.env`. One new file, one line in
`pyproject.toml`, no change to `core/`.

## Why this matters

A niche channel's audience is usually sitting in three or four subreddits, and a post's score
plus its comment count is a popularity signal the existing sources cannot give you:
`providers/research/arxiv.py` sets `score=0.0` on every item by design, and
`providers/research/rss.py` never sets `score` at all.

Subreddits also make this the generic version of a source. `examples/ember-fitness/` and
`examples/claro-es/` are presets for channels that have nothing to do with AI — if the
subreddits are configuration, all three use the same code path. A provider pinned to
`r/MachineLearning` would be a source only this repo's own channel can use.

## Where to start

- `creatoros_autopilot/interfaces/research.py` — `ResearchSource` needs `name`,
  `available()` and `fetch(*, since, limit, query) -> list[Item]`. `Item` carries `title`,
  `url`, `summary`, `published`, `source`, `score`, `extra`, and `Item.key()` is what
  deduplicates across sources.
- `creatoros_autopilot/interfaces/availability.py` — `available()` returns an `Availability`
  (`Availability.yes` / `Availability.no(detail, fix)`), never a bool, and the `fix` is
  meant to be pasteable.
- **`creatoros_autopilot/providers/research/rss.py` for two specific things.** First, its
  `USER_AGENT` constant and the comment directly above it, which already records that Reddit
  answers urllib's default User-Agent with 403 or 429 — reuse that string's shape (project
  name, version, project URL, and nothing that identifies the person running it). Second,
  its `fetch` docstring, which states the rules about `[]` and about `limit` that cost this
  project real bugs.
- **`creatoros_autopilot/providers/research/huggingface_papers.py` for the body of the
  work.** It is the closest existing provider: a JSON API over `urllib`, a `_get()` that
  turns every HTTP and transport failure into one exception type, a `_item()` that returns
  `None` for an unusable entry, `HuggingFaceError` vs `HuggingFaceShape` for "retry" vs "this
  needs a code change", and `_clean` / `_aware` for text and timestamps.
- `creatoros_autopilot/providers/research/arxiv.py` for the constructor. Its `categories`
  argument plus `CREATOROS_ARXIV_CATEGORIES` fallback is the pattern you want for
  `subreddits`, and its module docstring explains why the fallback is not optional — see the
  gotcha below.
- New file: `creatoros_autopilot/providers/research/reddit.py`.
- Register it in `pyproject.toml`, in the existing block:

  ```toml
  [project.entry-points."creatoros.research"]
  reddit = "creatoros_autopilot.providers.research.reddit:Reddit"
  ```
- Tests: `tests/test_provider_rss.py` has a `_Feeds` helper that starts a real
  `http.server` on `127.0.0.1` and answers only the paths a test registers; copy it.
  `tests/test_provider_arxiv.py` retargets the provider with
  `monkeypatch.setattr(arxiv, "API", server.url)`, so keep your base URL in a module-level
  constant or the provider cannot be tested.
- There is no `creatoros research` subcommand yet and `cli/doctor.py` has no research
  section, so your test plus
  `python -c "from creatoros_autopilot import plugins;
  print(plugins.load('research', 'reddit').available())"` is how you run it by hand.

### What the endpoint looks like

`https://www.reddit.com/r/<sub>/hot.json?limit=25&raw_json=1`, and `/top.json?t=day` for the
day's best. Several subreddits in one request as `r/a+b+c`. The body is
`{"kind": "Listing", "data": {"children": [{"kind": "t3", "data": {…}}]}}`, and the post
fields you want are `title`, `url`, `permalink`, `created_utc`, `score`, `num_comments`,
`selftext`, `subreddit`, `stickied`, `over_18` and `is_self`.

Two things to confirm against Reddit's live docs rather than against this issue:

- **The User-Agent requirement.** Reddit's API rules ask for a unique, descriptive
  User-Agent and throttle generic ones; this repo has already been bitten by it, which is
  what the comment above `rss.USER_AGENT` is recording. Check Reddit's current wording and
  suggested format before you write the string.
- **Whether unauthenticated `.json` still answers from your IP.** Reddit has tightened
  anonymous access, and a datacenter or VPN address can get a 403 even with a good
  User-Agent. If that is what you hit, say so in `available()`'s `fix` and in the PR rather
  than quietly returning `[]`. Moving to an OAuth app would take this off the keyless path
  and is a separate issue.

## What done looks like

- [ ] `creatoros_autopilot/providers/research/reddit.py` with a class whose `name` is
      `"reddit"`, and nothing under `creatoros_autopilot/core/` touched.
- [ ] `subreddits` is a constructor argument (`str | list[str] | None`) **and** has an
      environment fallback, e.g. `CREATOROS_REDDIT_SUBREDDITS`.
- [ ] With no subreddits configured, `available()` returns `Availability.no` with a `fix`
      naming the variable and showing the exact line to set. `rss.available()` is the model.
- [ ] The post score lands in `Item.score`; `num_comments` goes in `extra`.
- [ ] `Item.url` is the submitted link for a link post, and
      `https://www.reddit.com` + `permalink` for a self post (`permalink` is a path, not a
      URL).
- [ ] Titles and bodies arrive unescaped — `raw_json=1`, or `html.unescape`.
- [ ] Stickied posts are skipped. `over_18` is skipped or opt-in, your call, but documented.
- [ ] Every `published` is timezone-aware, or `None`.
- [ ] An entry point in `pyproject.toml`.
- [ ] `tests/test_provider_reddit.py` against a local server, covering: `limit=0`, one bad
      subreddit among several, every subreddit failing, a body that is not a Listing, a naive
      `since`, and a self post's URL.
- [ ] `pytest -q`, `ruff check .` and `mypy` pass.

## Gotchas

**`limit <= 0` means none, not everything.** This is a bug `rss.py` actually had, and its
comment names the cost: callers compute their budget as `cap - len(already_have)`, so a
caller with nothing left asked for nothing and was handed the whole feed to render. There is
a second trap stacked on it here — the protocol's `limit` is how many `Item`s you return
across all the subreddits, while Reddit's `limit` parameter is per request and caps at 100.
They are different numbers. And a `limit` of 0 must not become "send no `limit` parameter",
because Reddit's own default is then 25.

**Copy the User-Agent pattern, don't invent one.** `rss.USER_AGENT` is
`CreatorOS-Autopilot/<version> (+<repo URL>)` and the comment above it says what must not go
in it: nothing that identifies the person running it. A feed request is not a place to leak
who the user is. A 429 is then a raised error carrying a `fix`, never an empty list.

**The silent-empty trap, in its Reddit shape.** Every serious bug this project has had is an
error path that swallowed a failure and carried on, and a loop over subreddits with a bare
`except Exception: continue` is exactly that loop. A misspelled subreddit, a private one, a
banned one and a quiet one must not all produce the same answer:

- Check what `/r/<typo>/hot.json` actually returns — a 404 JSON body, a redirect, or a 200
  empty Listing. Whichever it is, it must not read as a quiet day.
- A private or banned subreddit answers with a body carrying a `reason`. Surface it; that
  sentence is the whole diagnosis.
- If the body is not a Listing at all — a login page, an error object, HTML — raise. Copy
  `HuggingFaceShape` and `SHAPE_FIX` from `huggingface_papers.py`: an endpoint that has
  changed shape is not fixed by retrying or by checking the network, and the message should
  say so. `payload["data"]["children"]` missing is not an empty day.

The accounting to copy is in `rss.fetch`: it counts subreddits *reached*, not items found,
and raises only when nothing could be read — there is a comment there about the day it tested
`not found` instead and reported "every feed failed" because one live feed was having a quiet
week. One dead subreddit among five must not take out the other four, and five dead ones must
not pass for a quiet day. `huggingface_papers.fetch` does the same with
`if failures and not served: raise failures[0]`, and prints a `!!` line for the partial case.

**A constructor argument alone is unreachable, which is why the env fallback is on the
checklist.** `plugins.load` (in `creatoros_autopilot/plugins.py`) builds every provider by
calling the factory with no arguments, and `ResearchSettings` in
`creatoros_autopilot/core/settings.py` has only `sources`, `feeds`, `lookback_days` and
`limit`, with no per-source options. `ResearchSettings.feeds` is declared and nothing reads
it; `rss.py` gets its feeds from its constructor or from `CREATOROS_RSS_FEEDS`, and nothing
else. `arxiv.py`'s docstring spells this out: while the constructor argument was the only way
in, that paragraph described something no user could actually do. So wire both, and follow
`arxiv.py`'s precedence — argument, then environment, then default — including its decision
that a variable which is *set but names nothing* is an error rather than a silent fall back to
the default.

**`created_utc` is epoch seconds.** Use `dt.datetime.fromtimestamp(value, dt.timezone.utc)`,
not the bare `fromtimestamp` (local time), and not the `created` field. `core/schedule.py`
raises `ValueError` on a naive datetime and `rss.fetch` raises on a naive `since` — do the
same. That strictness exists because the shell pipeline this replaced swallowed a date
failure and published four videos at once; `core/schedule.py`'s docstring quotes the line.
`arxiv.py`'s `_aware` docstring records the other half of it: one naive stamp made a
comparison raise `TypeError` and lost an entire feed.

**Without `raw_json=1`, Reddit HTML-escapes the text.** `&amp;` in a title goes through the
spec and onto the screen in the render. `rss.py`'s `_plain` exists for the same reason:
unescape entities, strip tags, collapse whitespace. Do that even with `raw_json=1` set —
users paste markdown and angle brackets into titles.

**Don't put the subreddit in the URL to keep posts apart.** `Item.key()` is the URL, so the
same article posted to three subreddits collapses on its own if you pass the submitted link
through untouched, which is what you want — it is one video. A self post keys on its
permalink and stays distinct anyway.

**Stickied posts are the subreddit's rules and its weekly thread.** They sit at the top of
`/hot` and are never a topic.

**`available()` must not raise and should stay offline.**
`tests/test_plugins.py::test_available_returns_an_availability_with_a_fix` is parametrised
over every installed `research` provider, calls `available()` with no `try`, and asserts both
`detail` and `fix` when it says no. An exception is a red suite; a live request there makes
`pytest` depend on Reddit being up. `rss.available()` checks configuration only, and its
docstring explains why that is the right call.

**Importing the module must work offline.** No request, no subprocess, no `os.environ[...]`
at import time — read the environment inside `__init__`, as `arxiv.py` does.
`tests/test_imports.py` imports every module in the package with nothing set and nothing
running.
