# SPDX-License-Identifier: AGPL-3.0-or-later
"""The arXiv research source, against a fake arXiv.

Every test here stands for something that was live-confirmed broken against the real API:
a typed quote that turned a phrase search into an OR of fragments over the whole of arXiv,
a naive feed timestamp that made the cut-off comparison raise and lost the day's papers,
and an empty category list that `available()` called OK while it searched everything.

The fake server exists because all three were invisible to a unit test that only inspected
return values — the one that matters is what arXiv is *asked*, which is why
`FakeArXiv.queries` records it.
"""
import datetime as dt
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from creatoros_autopilot import plugins
from creatoros_autopilot.interfaces.research import Item, ResearchSource
from creatoros_autopilot.providers.research import arxiv
from creatoros_autopilot.providers.research.arxiv import ArXiv

UTC = dt.timezone.utc

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2411.00001v1</id>
    <title>No zone on this one</title>
    <published>2024-11-15T18:59:59</published>
    <summary>A stamp that parses naive.</summary>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2411.00002v2</id>
    <title>A date and nothing else</title>
    <published>2024-11-15</published>
    <summary>Date only.</summary>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2411.00003v1</id>
    <title>Properly stamped</title>
    <published>2024-11-14T09:00:00Z</published>
    <summary>With a Z.</summary>
  </entry>
</feed>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self.server.queries.append({k: v[0] for k, v in query.items()})
        body = self.server.body.encode("utf-8")
        self.send_response(self.server.status)
        self.send_header("Content-Type", "application/atom+xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class FakeArXiv:
    """An arXiv that answers from a string and remembers what it was asked."""

    def __init__(self, body: str = FEED, status: int = 200) -> None:
        self.server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.server.queries: list[dict] = []
        self.server.body = body
        self.server.status = status
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def queries(self) -> list[dict]:
        return self.server.queries

    @property
    def searches(self) -> list[str]:
        return [q.get("search_query", "") for q in self.server.queries]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/api/query"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(autouse=True)
def _no_inherited_categories(monkeypatch):
    """The variable this module reads must not leak in from the developer's shell."""
    monkeypatch.delenv(arxiv.ENV_CATEGORIES, raising=False)


@pytest.fixture
def fake(monkeypatch):
    server = FakeArXiv()
    monkeypatch.setattr(arxiv, "API", server.url)
    try:
        yield server
    finally:
        server.close()


def source(categories="cs.AI", **kw) -> ArXiv:
    # min_interval=0 only because the pacing is arXiv's rate limit and the fake has none.
    return ArXiv(categories, min_interval=0.0, **kw)


class TestProtocol:
    def test_it_is_a_research_source(self):
        assert isinstance(source(), ResearchSource)


class TestQuotedQueries:
    """Defect 1: a typed quote escaped the phrase and took the category filter with it."""

    BROKEN = 'the "bitter lesson" revisited'

    def test_a_typed_quote_cannot_reach_arxiv(self):
        search = source()._search(self.BROKEN)
        # The live failure: all:"the " OR bitter OR lesson OR all:" revisited" AND (...),
        # which arXiv answered with 3.2 million papers and a 200.
        assert search.count('"') == 2, search
        assert " OR bitter" not in search
        assert search == 'all:"the bitter lesson revisited" AND (cat:cs.AI)'

    def test_quotes_change_nothing_about_what_is_searched(self):
        """The repair has to be the same query the same words without quotes give."""
        plain = source()._search("the bitter lesson revisited")
        assert source()._search(self.BROKEN) == plain

    @pytest.mark.parametrize("query", [
        'say "hi"', 'a\\b', 'trailing backslash \\', '"', 'mixed " and \\ both',
    ])
    def test_no_query_can_produce_an_unbalanced_expression(self, query):
        try:
            search = source()._search(query)
        except ValueError:
            return                      # refused outright, which is also an answer
        assert search.count('"') % 2 == 0 and "\\" not in search, search

    @pytest.mark.parametrize("query", ['"', '""', ' \\ ', '" \\ "'])
    def test_a_query_of_nothing_but_unquotables_is_refused(self, query):
        """Not answered with the category feed: that reports today's papers as hits."""
        with pytest.raises(ValueError) as exc:
            source()._search(query)
        assert "nothing searchable" in str(exc.value)

    def test_a_fielded_query_is_still_passed_through_untouched(self):
        written = 'ti:"attention is all you need" AND cat:cs.CL'
        assert source()._search(written) == written

    def test_the_category_filter_survives_into_the_request(self, fake):
        source(["cs.AI", "q-bio.NC"]).fetch(query=self.BROKEN, limit=2)
        assert fake.searches == ['all:"the bitter lesson revisited" '
                                 "AND (cat:cs.AI OR cat:q-bio.NC)"]


class TestTimestamps:
    """Defect 2: `_item` skipped `_aware`, so one entry could end the fetch."""

    @pytest.mark.parametrize("stamp,expected", [
        ("2024-11-15T18:59:59", dt.datetime(2024, 11, 15, 18, 59, 59, tzinfo=UTC)),
        ("2024-11-15", dt.datetime(2024, 11, 15, tzinfo=UTC)),
        ("2024-11-15T18:59:59Z", dt.datetime(2024, 11, 15, 18, 59, 59, tzinfo=UTC)),
        ("2024-11-15T20:59:59+02:00", dt.datetime(2024, 11, 15, 18, 59, 59, tzinfo=UTC)),
    ])
    def test_every_parsed_stamp_comes_back_aware(self, fake, stamp, expected):
        fake.server.body = FEED.replace("2024-11-15T18:59:59", stamp)
        first = source().fetch(limit=1)[0]
        assert first.published is not None and first.published.tzinfo is not None
        assert first.published == expected

    def test_one_naive_stamp_does_not_lose_the_whole_feed(self, fake):
        """It used to raise TypeError here and take every sound entry down with it."""
        items = source().fetch(since=dt.datetime(2024, 11, 15, tzinfo=UTC), limit=10)
        assert [i.title for i in items] == ["No zone on this one",
                                            "A date and nothing else"]

    def test_the_cutoff_still_ends_the_feed(self, fake):
        items = source().fetch(since=dt.datetime(2024, 11, 15, 19, tzinfo=UTC), limit=10)
        assert items == []

    def test_a_naive_since_is_read_as_utc_not_local(self, fake):
        """Documented behaviour, and the reason `_aware` exists at all."""
        naive = source().fetch(since=dt.datetime(2024, 11, 15, 19), limit=10)
        assert naive == source().fetch(since=dt.datetime(2024, 11, 15, 19, tzinfo=UTC),
                                       limit=10)


class TestEmptyCategories:
    """Defect 3: an empty list meant every paper on arXiv, and `available()` said OK."""

    EMPTY = ["", ",", " , ", [], ["", " "], ()]

    @pytest.mark.parametrize("categories", EMPTY)
    def test_available_refuses_and_says_what_to_set(self, categories):
        verdict = source(categories).available()
        assert not verdict.ok
        assert arxiv.ENV_CATEGORIES in verdict.fix
        assert arxiv.TAXONOMY in verdict.fix

    @pytest.mark.parametrize("categories", EMPTY)
    def test_nothing_is_asked_of_arxiv_at_all(self, fake, categories):
        """`available()` answers from configuration, so no request can leak out."""
        empty = source(categories)
        assert not empty.available().ok
        with pytest.raises(ValueError):
            empty.fetch(limit=5)
        with pytest.raises(ValueError):
            empty.fetch(query="diffusion models", limit=5)
        assert fake.queries == []

    @pytest.mark.parametrize("categories", EMPTY)
    def test_the_search_is_never_all(self, categories):
        with pytest.raises(ValueError) as exc:
            source(categories)._search("")
        assert "no arXiv categories configured" in str(exc.value)

    @pytest.mark.parametrize("categories", ["cs.AI.extra", "cs AI", "cs.AI/cs.LG"])
    def test_a_misspelt_category_is_still_caught_before_the_network(self, fake,
                                                                    categories):
        verdict = source(categories).available()
        assert not verdict.ok and arxiv.TAXONOMY in verdict.fix
        assert fake.queries == []

    def test_a_real_category_still_works(self, fake):
        verdict = source(["q-bio.NC"]).available()
        assert verdict.ok and verdict.detail.endswith("cat:q-bio.NC")
        assert fake.searches == ["cat:q-bio.NC"]


class TestConfiguration:
    """Defect 4: the categories argument was unreachable from any config a user writes."""

    def test_the_plugin_loader_passes_the_environment_through(self, monkeypatch):
        """`plugins.load` calls the factory with no arguments — this is the only way in."""
        monkeypatch.setenv(arxiv.ENV_CATEGORIES, "q-bio.NC, q-bio.QM")
        assert plugins.load("research", "arxiv").categories == ["q-bio.NC", "q-bio.QM"]

    def test_the_default_stands_when_the_variable_is_unset(self):
        assert ArXiv().categories == list(arxiv.DEFAULT_CATEGORIES)

    @pytest.mark.parametrize("value", ["", "   "])
    def test_an_empty_variable_reads_as_unset(self, monkeypatch, value):
        """A blank variable in a compose file means "not set", not "search nothing"."""
        monkeypatch.setenv(arxiv.ENV_CATEGORIES, value)
        assert ArXiv().categories == list(arxiv.DEFAULT_CATEGORIES)

    @pytest.mark.parametrize("value", [",", " , ,"])
    def test_a_variable_that_names_nothing_is_an_error_not_a_default(self, monkeypatch,
                                                                     value):
        monkeypatch.setenv(arxiv.ENV_CATEGORIES, value)
        verdict = ArXiv().available()
        assert not verdict.ok and arxiv.ENV_CATEGORIES in verdict.detail

    def test_an_argument_beats_the_environment(self, monkeypatch):
        monkeypatch.setenv(arxiv.ENV_CATEGORIES, "q-bio.NC")
        assert ArXiv("hep-th").categories == ["hep-th"]

    def test_the_docstring_names_the_variable_it_reads(self):
        """The claim that a biology channel can pass q-bio has to stay actionable."""
        assert arxiv.ENV_CATEGORIES in (arxiv.__doc__ or "")


class TestFetch:
    def test_entries_become_items(self, fake):
        items = source().fetch(limit=3)
        assert all(isinstance(i, Item) for i in items)
        assert items[1].url == "https://arxiv.org/abs/2411.00002"
        assert items[1].extra["version"] == "2"
        assert items[1].source == "arxiv"

    def test_a_maintenance_page_is_an_empty_day_not_a_crash(self, fake):
        fake.server.body = "<html><body>arXiv is down for maintenance</body></html>"
        assert source().fetch(limit=3) == []
