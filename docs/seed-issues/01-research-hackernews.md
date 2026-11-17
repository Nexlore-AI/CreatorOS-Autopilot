---
title: "Research source: Hacker News top stories"
labels: good first issue, provider
---

Add a `creatoros.research` provider that returns the Hacker News stories from the last N
hours, with HN's points as `Item.score`. One new file, one line in `pyproject.toml`, no
change to `core/` — `tests/test_plugins.py::test_core_names_no_provider` reads the AST of
every file in `core/` and fails if it so much as names a provider, so that part is a
guarantee rather than a convention.

## Why this matters

Three research sources ship today and none of them can rank a general-interest topic.
`providers/research/arxiv.py` sets `score=0.0` on every item and says why in a comment:
arXiv publishes no popularity signal at all. `providers/research/rss.py` never sets `score`
either, so an RSS item is also 0.0. `providers/research/huggingface_papers.py` does have a
real signal — upvotes — but only for machine-learning papers.

So a channel about developer tools, security, hardware or startups has no source that
answers "what did anyone actually care about today". Hacker News does, it needs no key, and
that keeps it on the path that works with an empty `.env`.

## Where to start

- `creatoros_autopilot/interfaces/research.py` — the whole contract. `ResearchSource` wants
  `name`, `available()` and `fetch(*, since, limit, query) -> list[Item]`. `Item` is a
  frozen dataclass: `title`, `url`, `summary`, `published`, `source`, `score`, `extra`, plus
  `Item.key()`, which is what deduplicates the same story arriving from two sources.
- `creatoros_autopilot/interfaces/availability.py` — `available()` returns an `Availability`
  (`Availability.yes(detail)` / `Availability.no(detail, fix)`), never a bool, and a "no"
  must carry a `fix` the user can paste.
- **Copy the shape of `creatoros_autopilot/providers/research/huggingface_papers.py`.** It
  is the closest existing provider: a JSON API read with `urllib`, a `_get()` that turns
  HTTP and transport failures into one exception type, a `_item()` that returns `None` for
  an entry too broken to use, `_clean()` for whitespace and `_aware()` for timestamps. It is
  also where `HuggingFaceShape` and `SHAPE_FIX` live, which is the pattern for "the endpoint
  answered, but not with what this module was written against".
- Read the `fetch` docstring in `providers/research/rss.py` even though you are not parsing
  XML. It states the two rules every source here follows: raise when nothing could be read,
  and treat `limit <= 0` as a ceiling of zero.
- New file: `creatoros_autopilot/providers/research/hackernews.py`.
- Register it in `pyproject.toml`, in the existing block:

  ```toml
  [project.entry-points."creatoros.research"]
  hackernews = "creatoros_autopilot.providers.research.hackernews:HackerNews"
  ```

  Adding your provider to this repo's own `pyproject.toml` is correct — every shipped
  provider registers through the same public entry points a third-party plugin would use.
- Tests: `tests/test_provider_rss.py` and `tests/test_provider_huggingface_papers.py` both
  start a real `http.server` on `127.0.0.1` rather than monkeypatching `urlopen`, because
  the bugs worth catching are the ones that need a server answering badly.
  `tests/test_provider_arxiv.py` points the provider at that server with
  `monkeypatch.setattr(arxiv, "API", server.url)` — so keep your base URL in a module-level
  constant, or it cannot be tested.
- There is no `creatoros research` subcommand yet, and `creatoros doctor` has no research
  section (see `cli/doctor.py` — it checks tts, publish, llm, style and gates). Your own
  test and `python -c "from creatoros_autopilot import plugins;
  print(plugins.load('research', 'hackernews').available())"` are how you exercise it.

### Which API

Both are public and keyless; pick one and write the reason in the module docstring.

- **Firebase** (`https://hacker-news.firebaseio.com/v0/`): `topstories.json` returns an
  array of up to 500 item ids in HN's own rank order, then one GET per
  `item/<id>.json`. You get HN's ranking, at the cost of N+1 requests.
- **Algolia** (`https://hn.algolia.com/api/v1/search_by_date?tags=story&numericFilters=created_at_i>…`):
  one request returns whole stories with `points`, `num_comments`, `created_at` and
  `created_at_i`. Ordered by date, not by HN's rank.

"Top stories in the last N hours" is one request on Algolia and ~500 on Firebase, so Algolia
is probably the easier answer — but check the field names and the rate limits on the live
endpoints before you commit to either, rather than trusting this paragraph.

## What done looks like

- [ ] `creatoros_autopilot/providers/research/hackernews.py` with a class whose `name` is
      `"hackernews"`, and nothing under `creatoros_autopilot/core/` touched.
- [ ] `available()` returns an `Availability`, never raises, and when it says no it carries a
      `fix` that can be pasted.
- [ ] `fetch()` honours `since` (rejecting a naive datetime), honours `limit` (`<= 0` returns
      `[]`), and does something defined and documented with `query`.
- [ ] HN points land in `Item.score` as a float.
- [ ] `Item.url` is the story's own link. An Ask HN post, a job post or a text post has no
      link, so it gets `https://news.ycombinator.com/item?id=<id>` instead of an empty
      string.
- [ ] `extra` carries at least the HN id, the discussion URL, `num_comments` and the author.
- [ ] Every `published` is timezone-aware, or `None`.
- [ ] An entry point in `pyproject.toml`.
- [ ] `tests/test_provider_hackernews.py` against a local server, covering: a 200 that is not
      the expected shape, a story with no `url`, `limit=0`, a naive `since`, and all requests
      failing.
- [ ] `pytest -q`, `ruff check .` and `mypy` pass.

## Gotchas

**A 200 that carries nothing usable must not look like a quiet day.** This is the bug this
project keeps having, and `rss.py` had the exact version of it: a moved feed URL answered 200
with a sitemap or a styled error page — valid XML, no `<item>` anywhere — and the reader
returned `[]`. A broken source and a slow news day were indistinguishable for as long as
nobody checked. The fix is the `FEED_ROOTS` check in `rss.py`: assert the payload is the kind
of thing you asked for, and raise when it is not.

The HN versions of that same trap:

- `item/<id>.json` answers the literal JSON `null` for an id that has gone away. `json.loads`
  gives you `None`, and `None.get(...)` inside a `try: … except Exception: continue` is a
  silently skipped item.
- `topstories.json` answering an object instead of an array of ints — one `isinstance` check
  away from an empty list and no error. `huggingface_papers._get` raises `HuggingFaceShape`
  for precisely this; copy it, including a `fix` that says this needs a code change rather
  than a setting.
- On the Algolia route, a misspelled `numericFilters` or `tags` gives you 200 and `{"hits":
  []}`. Nothing in that response says you asked wrongly.

So: if every request failed, raise — `huggingface_papers.fetch` does `if failures and not
served: raise failures[0]`, and `rss.fetch` counts feeds *reached* rather than items found
(there is a comment there about the day it tested the wrong one). If the endpoint answered
with a shape you were not written against, raise. Only a request that genuinely succeeded and
held nothing may return `[]`. Dropping dead and deleted items is right; dropping all of them
and saying nothing is not — `huggingface_papers.fetch` prints a `!!` line when everything it
fetched got filtered out, which is the honest middle ground.

**Return timezone-aware datetimes.** `core/schedule.py` raises `ValueError` on a naive
`slot_dt` or `now`, and `rss.fetch` raises on a naive `since`. That strictness is there
because the shell pipeline this replaced swallowed a date failure and published four videos
at once instead of scheduling them — the module docstring quotes the exact line. Concretely:

- Firebase's `time` is Unix epoch seconds. Use
  `dt.datetime.fromtimestamp(value, dt.timezone.utc)`. The bare `fromtimestamp(value)` is
  local time and will move your cut-off by hours depending on the machine.
- Algolia's `created_at` is ISO 8601 ending in `Z`, and on Python 3.10 —
  which this project supports and CI runs — `dt.datetime.fromisoformat` cannot read that
  `Z`. See `_when` in `huggingface_papers.py` and in `arxiv.py` for the two-line fix.
- Comparing a naive datetime with an aware one raises `TypeError`. The `_aware` docstring in
  `arxiv.py` records what that cost: one sloppy stamp in one entry took out the whole feed,
  every sound entry in it included.

**`available()` must not raise, and should not need the network.**
`tests/test_plugins.py::test_available_returns_an_availability_with_a_fix` is parametrised
over every installed `research` provider, calls `available()` with no `try`, and asserts both
`detail` and `fix` when it says no. An exception there is a red suite. A live HN call there
makes the test suite depend on HN being up — `rss.available()` is deliberately offline and
its docstring explains why.

**`limit <= 0` means none, not everything.** `rss.py` had this bug too: callers work their
budget out as `cap - len(already_have)`, so a caller with nothing left asked for nothing and
was handed the entire feed to render.

**Importing the module must work offline.** No request, no subprocess, no `os.environ[...]`
at import time. `tests/test_imports.py` imports every module in the package with nothing
installed and nothing running.

**Don't reshape the URL.** `Item.key()` is `(url or title)` lowercased with a trailing slash
stripped, and that is the only thing stopping one story becoming two videos. An HN post
linking an arXiv abstract should dedupe against the arxiv source, which only works if you
pass the submitted URL through untouched — no tracking parameters added, and the HN
discussion link in `extra`, not in `url`. `huggingface_papers.py` has a long comment about
the day it got this wrong in the other direction.

**If you take the Firebase route, bound the request count.** Walking 500 ids to fill
`limit=20` is 501 requests. HN's Firebase API documents no rate limit at all — check for one
before writing a tight loop, and if pacing turns out to be needed, `arxiv.py`'s `_wait_turn`
is the pattern (module-level clock, because the limit belongs to the service's view of the
machine and not to one object). And if you stop early, say so: truncating in silence makes a
cut-short answer look like the whole answer.
