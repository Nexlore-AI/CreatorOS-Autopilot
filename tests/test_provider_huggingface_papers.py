# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Hugging Face daily papers source, read over a real socket.

Three of the bugs these cover could only be seen from the far side of a request: a 400
that was waved through, a day's list carrying papers from the week before, and a URL that
agreed with nothing. None of them is reachable by calling the helpers directly — the
first needs a server that refuses, the second needs a list whose entries disagree with
the day they sit on — so these tests run a server instead of monkeypatching `urlopen`.

The payloads are built relative to today's UTC date, because `fetch` asks for dates it
works out itself and a fixture pinned to a written-down day would stop being asked for
tomorrow.
"""
from __future__ import annotations

import datetime as dt
import http.server
import json
import threading
import urllib.parse
import xml.etree.ElementTree as ET

import pytest

from creatoros_autopilot.providers.research import arxiv
from creatoros_autopilot.providers.research import huggingface_papers as hf

TODAY = dt.datetime.now(dt.timezone.utc).date()
NOON = dt.time(12, 0, tzinfo=dt.timezone.utc)


def at(day: dt.date) -> str:
    """A paper's `publishedAt`, as Hugging Face spells it."""
    return f"{day.isoformat()}T00:00:00.000Z"


def paper(ident: str, *, published: dt.date | None, upvotes: int = 0,
          title: str = "", summary: str = "A paper.", daily: dt.date | None = None) -> dict:
    """One list entry in the shape the live endpoint returns."""
    entry: dict = {
        "paper": {
            "id": ident,
            "title": title or f"Paper {ident}",
            "summary": summary,
            "upvotes": upvotes,
            "authors": [{"name": "A Author"}],
            "ai_keywords": ["agents"],
            "ai_summary": "A gloss.",
        },
        "numComments": 0,
        "submittedBy": {"fullname": "Someone Else"},
    }
    if published is not None:
        entry["paper"]["publishedAt"] = at(published)
    if daily is not None:
        entry["paper"]["submittedOnDailyAt"] = at(daily)
    return entry


REFUSED = {"error": '✖ "date" must be less than or equal to "%s"' % at(TODAY)}


class _Daily:
    """A local stand-in for `/api/daily_papers`, answering per requested date.

    Routes are keyed by the `date` parameter, with None for the undated request — the
    one `fetch` falls back on to tell "that day has not happened yet" from "this request
    is wrong", which is the distinction the 400 bug erased.
    """

    def __init__(self) -> None:
        self.routes: dict[dt.date | None, tuple[int, object]] = {}
        self.asked: list[dt.date | None] = []
        routes, asked = self.routes, self.asked

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_GET(self) -> None:
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                raw = (query.get("date") or [""])[0]
                day = dt.date.fromisoformat(raw) if raw else None
                asked.append(day)
                status, payload = routes.get(day, (400, REFUSED))
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address[:2]
        self.api = f"http://{host}:{port}/api/daily_papers"

    def serve(self, day: dt.date | None, entries: list) -> None:
        self.routes[day] = (200, entries)

    def fail(self, day: dt.date | None, status: int, payload: object = None) -> None:
        self.routes[day] = (status, payload if payload is not None else REFUSED)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def daily(monkeypatch):
    served = _Daily()
    monkeypatch.setattr(hf, "API", served.api)
    try:
        yield served
    finally:
        served.close()


def source(**kwargs) -> hf.HuggingFacePapers:
    return hf.HuggingFacePapers(timeout=5, **kwargs)


# --- `since` is a cut-off, not just a choice of which lists to ask for -----------------

def test_since_drops_papers_published_before_it(daily):
    """A daily list is what people read today, not what came out today.

    Live, `fetch(since=now-24h, limit=5)` returned five papers published two to six days
    before the cut-off, because the list it came off carries the week's papers. `since`
    chose which lists to ask for and nothing else.
    """
    daily.serve(TODAY, [
        paper("2611.00001", published=TODAY, upvotes=3),
        paper("2611.00002", published=TODAY - dt.timedelta(days=6), upvotes=99),
    ])
    daily.serve(TODAY - dt.timedelta(days=1), [
        paper("2611.00003", published=TODAY - dt.timedelta(days=1), upvotes=5),
    ])

    since = dt.datetime.combine(TODAY, NOON) - dt.timedelta(hours=18)
    items = source().fetch(since=since, limit=10)

    assert [i.extra["arxiv_id"] for i in items] == ["2611.00001"]
    assert all(i.published >= since for i in items)
    # The six-day-old paper had the most upvotes, so an unfiltered fetch ranked it first
    # rather than dropping it — and still does with no cut-off, which is what shows that
    # `since` is doing the work here and not the fixture.
    assert "2611.00002" not in {i.extra["arxiv_id"] for i in items}
    assert source().fetch(limit=10)[0].extra["arxiv_id"] == "2611.00002"


def test_an_undated_paper_survives_the_cutoff(daily):
    """`since` cannot rule out a paper whose age is unknown.

    `publishedAt` is the endpoint's to stop sending, and dropping entries without it
    would turn that into this source silently finding nothing — the failure it exists to
    avoid. The list the entry came off is inside the window, which is more than nothing.
    """
    daily.serve(TODAY, [paper("2611.00009", published=None, upvotes=1)])
    items = source().fetch(since=dt.datetime.combine(TODAY, NOON), limit=5)
    assert [i.extra["arxiv_id"] for i in items] == ["2611.00009"]
    assert items[0].published is None


def test_a_window_emptied_by_the_cutoff_says_so(daily, capsys):
    """An empty answer that the lists caused must not read like a day with no list."""
    daily.serve(TODAY, [paper("2611.00002", published=TODAY - dt.timedelta(days=9))])
    assert source().fetch(since=dt.datetime.combine(TODAY, NOON)) == []
    said = capsys.readouterr().out
    assert "published before" in said and "lookback_days" in said


def test_the_query_filter_still_applies(daily):
    daily.serve(TODAY, [
        paper("2611.00010", published=TODAY, title="Sparse attention at scale"),
        paper("2611.00011", published=TODAY, title="Protein folding"),
    ])
    items = source().fetch(since=dt.datetime.combine(TODAY, NOON) - dt.timedelta(hours=18),
                           query="sparse attention")
    assert [i.extra["arxiv_id"] for i in items] == ["2611.00010"]


# --- a 400 is only ever excusable on the newest day asked for --------------------------

def test_a_400_on_every_dated_request_raises(daily):
    """The bug: a refused window came back as `[]`, printing and raising nothing.

    Reproduced against a server that 400s every dated request, which is what a renamed
    parameter or a narrowed date range looks like. A source that has stopped working
    must not be indistinguishable from a quiet weekend.
    """
    with pytest.raises(hf.HuggingFaceError) as refused:
        source().fetch(since=dt.datetime.combine(TODAY, NOON) - dt.timedelta(days=3))
    assert "400" in str(refused.value)


def test_a_400_only_on_the_newest_day_is_tolerated(daily, capsys):
    """Hugging Face publishes a day's list during that day.

    For the first hours of a UTC day the newest date asked for genuinely does not exist
    yet, which is a 400 and not a failure — the rest of the window must still be used.
    """
    yesterday = TODAY - dt.timedelta(days=1)
    daily.fail(TODAY, 400)
    daily.serve(yesterday, [paper("2611.00004", published=yesterday, upvotes=7)])
    items = source().fetch(
        since=dt.datetime.combine(yesterday, dt.time(0, 0, tzinfo=dt.timezone.utc)))
    assert [i.extra["arxiv_id"] for i in items] == ["2611.00004"]
    assert capsys.readouterr().out == ""


def test_an_empty_window_asks_the_undated_endpoint_and_explains_itself(daily, capsys):
    """Today's list is not up yet: a note and an empty list, not an exception."""
    daily.fail(TODAY, 400)
    daily.serve(None, [paper("2611.00005", published=TODAY - dt.timedelta(days=2),
                             daily=TODAY - dt.timedelta(days=1))])
    assert source().fetch(since=dt.datetime.combine(TODAY, NOON)) == []
    said = capsys.readouterr().out
    assert f"no list for {TODAY.isoformat()} yet" in said
    assert daily.asked[-1] is None


def test_an_empty_window_that_is_not_a_missing_day_raises(daily):
    """The same 400, with the endpoint serving a list for the day it just refused.

    That is not a day Hugging Face has yet to reach — it is this module's request being
    rejected — and it has to stop the run rather than read as a quiet day.
    """
    daily.fail(TODAY, 400)
    daily.serve(None, [paper("2611.00006", published=TODAY, daily=TODAY)])
    with pytest.raises(hf.HuggingFaceShape) as broken:
        source().fetch(since=dt.datetime.combine(TODAY, NOON))
    assert "date parameter" in str(broken.value)


def test_an_empty_window_with_an_unreadable_undated_list_raises(daily):
    """Nothing refused the request and nothing can be read from the fallback either."""
    daily.fail(TODAY, 400)
    daily.serve(None, [])
    with pytest.raises(hf.HuggingFaceShape) as broken:
        source().fetch(since=dt.datetime.combine(TODAY, NOON))
    assert "no day this could read" in str(broken.value)


def test_a_server_error_mid_window_warns_but_keeps_the_rest(daily, capsys):
    daily.serve(TODAY, [paper("2611.00007", published=TODAY, upvotes=2)])
    daily.fail(TODAY - dt.timedelta(days=1), 503, {"error": "upstream"})
    items = source().fetch(since=dt.datetime.combine(TODAY, NOON) - dt.timedelta(days=1))
    assert [i.extra["arxiv_id"] for i in items] == ["2611.00007"]
    assert "no list for" in capsys.readouterr().out


def test_a_rate_limit_stops_the_window(daily):
    for n in range(5):
        daily.fail(TODAY - dt.timedelta(days=n), 429, {"error": "slow down"})
    with pytest.raises(hf.HuggingFaceError) as limited:
        source().fetch(since=dt.datetime.combine(TODAY, NOON) - dt.timedelta(days=4))
    assert limited.value.status == 429
    # One request, not five: spending the rest of the window on calls that will also be
    # refused makes the rate limit worse and the window no larger.
    assert len(daily.asked) == 1


# --- the URL is the deduplication key, so it has to be arXiv's -------------------------

def atom(ident: str) -> ET.Element:
    """The same paper as arXiv's Atom feed gives it, version suffix and all."""
    feed = ET.fromstring(
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        f'<id>http://arxiv.org/abs/{ident}</id><title>Paper {ident}</title>'
        '<published>2026-11-10T00:00:00Z</published>'
        '</entry></feed>')
    return feed.find("{http://www.w3.org/2005/Atom}entry")


def test_the_same_paper_from_both_sources_is_one_key(daily):
    """The bug: one paper became two videos.

    `arxiv.py` deliberately rebuilds https://arxiv.org/abs/<bare id> so that the copy
    coming off Hugging Face collides with it in `Item.key()`. This source used to carry
    the huggingface.co/papers URL, so it never collided and the pipeline, which
    deduplicates by URL, kept both.
    """
    daily.serve(TODAY, [paper("2611.00008v2", published=TODAY)])
    mine = source().fetch(limit=1)[0]
    theirs = arxiv._item(atom("2611.00008v2"), "arxiv")

    assert mine.url == "https://arxiv.org/abs/2611.00008"
    assert mine.key() == theirs.key()
    assert mine.extra["arxiv_id"] == theirs.extra["arxiv_id"] == "2611.00008"
    # The votes and the discussion are on the Hugging Face page, which is why it is kept.
    assert mine.extra["hf_url"] == "https://huggingface.co/papers/2611.00008"


def test_an_old_style_arxiv_id_is_also_an_arxiv_url(daily):
    daily.serve(TODAY, [paper("math.GT/0309136", published=TODAY)])
    item = source().fetch(limit=1)[0]
    assert item.url == "https://arxiv.org/abs/math.GT/0309136"
    assert item.key() == arxiv._item(atom("math.GT/0309136"), "arxiv").key()


def test_a_paper_that_is_not_on_arxiv_keeps_the_page_it_is_on(daily):
    """An arxiv.org address built from a non-arXiv id would 404.

    Hugging Face has only ever listed arXiv papers, but an empty `arxiv_id` is the
    honest answer for one it has not, and linking the page the paper is actually on
    beats linking a guess.
    """
    daily.serve(TODAY, [paper("hf-2026-house-paper", published=TODAY)])
    item = source().fetch(limit=1)[0]
    assert item.url == "https://huggingface.co/papers/hf-2026-house-paper"
    assert item.extra["arxiv_id"] == "" and item.extra["arxiv_url"] == ""


def test_one_paper_on_two_days_lists_is_one_item(daily):
    daily.serve(TODAY, [paper("2611.00020", published=TODAY, upvotes=4)])
    daily.serve(TODAY - dt.timedelta(days=1),
                [paper("2611.00020", published=TODAY, upvotes=1)])
    items = source().fetch(since=dt.datetime.combine(TODAY, NOON) - dt.timedelta(days=1))
    assert len(items) == 1 and items[0].score == 4.0


def test_items_come_back_most_upvoted_first(daily):
    daily.serve(TODAY, [
        paper("2611.00021", published=TODAY, upvotes=1),
        paper("2611.00022", published=TODAY, upvotes=50),
    ])
    items = source().fetch(limit=5)
    assert [i.extra["arxiv_id"] for i in items] == ["2611.00022", "2611.00021"]


# --- availability -----------------------------------------------------------------------

def test_available_needs_no_key(daily):
    daily.serve(None, [paper("2611.00030", published=TODAY, daily=TODAY)])
    ok = source().available()
    assert ok and TODAY.isoformat() in ok.detail


def test_available_names_a_fix_when_the_shape_changes(daily):
    daily.routes[None] = (200, {"papers": []})
    answer = source().available()
    assert not answer and answer.fix and "issue" in answer.fix


def test_available_names_a_fix_when_nothing_answers(monkeypatch):
    # Port 1 on localhost refuses connections, which is the no-network case.
    monkeypatch.setattr(hf, "API", "http://127.0.0.1:1/api/daily_papers")
    answer = source().available()
    assert not answer and "curl" in answer.fix
