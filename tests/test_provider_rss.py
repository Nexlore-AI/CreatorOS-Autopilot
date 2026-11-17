# SPDX-License-Identifier: AGPL-3.0-or-later
"""The RSS source, read over a real socket.

A feed reader is all edge cases, and the ones that cost something are the quiet ones: a
200 that is not a feed, a relative link, one dead URL among five. None of those can be
reached by calling the helpers directly — they need a server that answers badly — so
these tests start one rather than monkeypatching `urlopen`.
"""
from __future__ import annotations

import datetime as dt
import http.server
import threading

import pytest

from creatoros_autopilot.providers.research.rss import RSS, FeedError


class _Feeds:
    """A local HTTP server answering only the paths a test has registered."""

    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, bytes]] = {}
        routes = self.routes

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_GET(self) -> None:
                found = routes.get(self.path)
                if found is None:
                    self.send_error(404, "Not Found")
                    return
                status, body = found
                if status >= 400:
                    self.send_error(status)
                    return
                if status in (301, 302, 307, 308):
                    self.send_response(status)
                    self.send_header("Location", body.decode())
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(status)
                self.send_header("Content-Type", "application/xml")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address[:2]
        self.base = f"http://{host}:{port}"

    def add(self, path: str, body: str, status: int = 200) -> str:
        self.routes[path] = (status, body.encode("utf-8"))
        return self.base + path

    def missing(self, path: str) -> str:
        return self.base + path

    def moved(self, path: str, to: str) -> str:
        self.routes[path] = (301, to.encode("utf-8"))
        return self.base + path

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def feeds():
    served = _Feeds()
    try:
        yield served
    finally:
        served.close()


RSS_ONE = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
  <title>Example News</title>
  <item>
    <title>A real story</title>
    <link>https://example.com/one</link>
    <description>&lt;p&gt;Body text&lt;/p&gt;</description>
    <pubDate>Tue, 11 Nov 2026 09:00:00 +0000</pubDate>
  </item>
</channel></rss>
"""

EMPTY_RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>Quiet Blog</title></channel></rss>
"""

SITEMAP = """<?xml version="1.0" encoding="utf-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/one</loc></url>
  <url><loc>https://example.com/two</loc></url>
</urlset>
"""

XHTML_ERROR = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>404 Not Found</title></head>
<body><p>The feed you asked for has moved.</p></body></html>
"""


def _atom(base: str | None, href: str, *, guid: str = "") -> str:
    at_base = f' xml:base="{base}"' if base else ""
    ident = f"<id>{guid}</id>" if guid else "<id>tag:example,2026:1</id>"
    return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"{at_base}>
  <title>Site Feed</title>
  <entry>
    <title>Hello</title>
    <link rel="alternate" href="{href}"/>
    {ident}
    <updated>2026-11-11T09:00:00Z</updated>
  </entry>
</feed>
"""


def _many(count: int) -> str:
    items = "".join(
        f"<item><title>Story {n}</title><link>https://example.com/{n}</link>"
        f"<pubDate>Tue, 11 Nov 2026 09:{n:02d}:00 +0000</pubDate></item>"
        for n in range(count)
    )
    return ('<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel>'
            f"<title>Busy</title>{items}</channel></rss>")


# --- the happy path, so the fixes below are not mistaken for the feed reader working ---

def test_reads_rss_and_atom(feeds):
    one = feeds.add("/rss.xml", RSS_ONE)
    two = feeds.add("/atom.xml", _atom(None, "https://other.example/hello"))
    items = RSS([one, two]).fetch()
    assert {i.url for i in items} == {"https://example.com/one",
                                      "https://other.example/hello"}
    assert all(i.published is not None and i.published.tzinfo is not None for i in items)
    assert {i.source for i in items} == {"Example News", "Site Feed"}


# --- defect 1: a 200 that is well-formed XML but not a feed ---

def test_sitemap_served_as_the_feed_is_not_a_quiet_day(feeds):
    url = feeds.add("/feed.xml", SITEMAP)
    source = RSS([url])
    with pytest.raises(FeedError) as caught:
        source.fetch()
    assert "urlset" in str(caught.value)


def test_xhtml_error_page_is_not_a_quiet_day(feeds):
    url = feeds.add("/feed.xml", XHTML_ERROR)
    with pytest.raises(FeedError):
        RSS([url]).fetch()


def test_one_feed_turning_into_a_web_page_does_not_hide_the_others(feeds):
    good = feeds.add("/rss.xml", RSS_ONE)
    page = feeds.add("/moved.xml", XHTML_ERROR)
    source = RSS([good, page])
    items = source.fetch()
    assert [i.url for i in items] == ["https://example.com/one"]
    assert len(source.problems) == 1
    assert "/moved.xml" in source.problems[0]


def test_rss_1_0_rdf_root_is_still_a_feed(feeds):
    rdf = """<?xml version="1.0" encoding="utf-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns="http://purl.org/rss/1.0/">
  <channel><title>Old School</title></channel>
  <item><title>Ancient</title><link>https://example.com/rdf</link></item>
</rdf:RDF>
"""
    url = feeds.add("/rdf.xml", rdf)
    items = RSS([url]).fetch()
    assert [i.url for i in items] == ["https://example.com/rdf"]
    assert items[0].source == "Old School"


# --- defect 2: a relative Atom href ---

def test_relative_hrefs_do_not_collide_across_sites(feeds):
    alpha = feeds.add("/alpha.xml", _atom("https://alpha.example/",
                                          "/2026/01/hello.html",
                                          guid="tag:alpha,2026:1"))
    beta = feeds.add("/beta.xml", _atom("https://beta.example/",
                                        "/2026/01/hello.html",
                                        guid="tag:beta,2026:1"))
    items = RSS([alpha, beta]).fetch()
    assert sorted(i.url for i in items) == ["https://alpha.example/2026/01/hello.html",
                                            "https://beta.example/2026/01/hello.html"]
    assert len({i.key() for i in items}) == 2


def test_relative_href_without_xml_base_resolves_against_the_feed_url(feeds):
    url = feeds.add("/blog/atom.xml", _atom(None, "../2026/hello.html"))
    items = RSS([url]).fetch()
    assert items[0].url == f"{feeds.base}/2026/hello.html"


def test_relative_href_resolves_against_the_redirect_target(feeds):
    """A feed that has moved host is still read, and its links resolve where it landed —
    not against the address still sitting in creatoros.yaml."""
    feeds.add("/new/atom.xml", _atom(None, "hello.html"))
    moved = feeds.moved("/old.xml", feeds.base + "/new/atom.xml")
    items = RSS([moved]).fetch()
    assert items[0].url == f"{feeds.base}/new/hello.html"


def test_xml_base_on_the_entry_wins_over_the_feed(feeds):
    body = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xml:base="https://outer.example/">
  <title>Syndicated</title>
  <entry xml:base="https://inner.example/posts/">
    <title>Hello</title>
    <link rel="alternate" href="hello.html"/>
    <id>tag:inner,2026:1</id>
  </entry>
</feed>
"""
    url = feeds.add("/atom.xml", body)
    items = RSS([url]).fetch()
    assert items[0].url == "https://inner.example/posts/hello.html"


def test_non_http_href_is_not_used_as_the_url(feeds):
    url = feeds.add("/atom.xml", _atom(None, "mailto:editor@example.com",
                                       guid="https://example.com/real"))
    items = RSS([url]).fetch()
    assert items[0].url == "https://example.com/real"


# --- defect 3: one quiet feed and one dead URL ---

def test_a_quiet_feed_beside_a_dead_url_does_not_claim_everything_failed(feeds):
    quiet = feeds.add("/quiet.xml", EMPTY_RSS)
    dead = feeds.missing("/gone.xml")
    source = RSS([quiet, dead])
    assert source.fetch() == []
    assert len(source.problems) == 1
    assert "/gone.xml" in source.problems[0] and "404" in source.problems[0]


def test_every_feed_failing_still_raises(feeds):
    source = RSS([feeds.missing("/a.xml"), feeds.missing("/b.xml")])
    with pytest.raises(FeedError) as caught:
        source.fetch()
    assert "/a.xml" in str(caught.value) and "/b.xml" in str(caught.value)


def test_all_feeds_quiet_is_a_quiet_day(feeds):
    source = RSS([feeds.add("/quiet.xml", EMPTY_RSS)])
    assert source.fetch() == []
    assert source.problems == []


# --- defect 4: a zero budget ---

@pytest.mark.parametrize("limit,expected", [(5, 5), (1, 1), (0, 0), (-1, 0), (99, 50)])
def test_limit_is_a_ceiling_not_a_switch(feeds, limit, expected):
    url = feeds.add("/many.xml", _many(50))
    assert len(RSS([url]).fetch(limit=limit)) == expected


# --- the contract the loader depends on ---

def test_available_says_what_to_do_when_there_are_no_feeds(monkeypatch):
    monkeypatch.delenv("CREATOROS_RSS_FEEDS", raising=False)
    answer = RSS().available()
    assert not answer and "creatoros.yaml" in answer.fix


def test_since_must_be_aware(feeds):
    source = RSS([feeds.add("/rss.xml", RSS_ONE)])
    with pytest.raises(ValueError):
        source.fetch(since=dt.datetime(2026, 1, 1))
