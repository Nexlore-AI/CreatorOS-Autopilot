# SPDX-License-Identifier: AGPL-3.0-or-later
"""Topics from RSS and Atom feeds.

Research used to be whatever the agent decided to do inside a prompt, so none of it could
be reused, swapped or tested. This is the most general replacement: every blog, newsroom,
subreddit and YouTube channel already publishes a feed, which makes this the one source
most users will ever configure and the one that has to be dull and reliable.

Three things make a hand-rolled feed reader go quietly wrong, and they are the reason
this module is longer than it looks like it should be.

The first is Atom. Its entries are the same shape as RSS's items but they live in a
namespace, so a reader written against RSS 2.0 finds no `<item>` anywhere and returns an
empty list — indistinguishable from a quiet week. About half the feeds you meet are Atom,
YouTube's among them, so elements are matched on local name here.

The second is dates. RSS carries RFC 822, Atom carries ISO 8601, and plenty of feeds carry
something naive or nothing at all. `core.schedule` refuses naive datetimes and is right
to, so every timestamp that leaves here is timezone-aware or absent.

The third is a feed that has stopped being one. A moved URL answers 200 with a sitemap or
a styled error page, both well-formed XML containing no `<item>` — so the root element is
checked rather than inferred from what the scan happened to find.

`feedparser` would do all of this. It would also be a fourth dependency, and the part that
matters is a page of standard library.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import html
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator

from ... import __version__
from ...interfaces.availability import Availability
from ...interfaces.research import Item

#: Reddit and a few CDNs answer urllib's default User-Agent with 403 or 429. Naming the
#: project and version is enough to be served. Nothing here identifies the person running
#: it, and nothing ever should: a feed request is not a place to leak who the user is.
USER_AGENT = f"CreatorOS-Autopilot/{__version__} (+https://github.com/Nexlore-AI/CreatorOS-Autopilot)"
ACCEPT = "application/atom+xml, application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.1"

MAX_BYTES = 8 * 1024 * 1024        # a URL that turns out to be a video should fail, not download
DEFAULT_SUMMARY_CHARS = 400

#: The three roots a feed is allowed to have: RSS 2.0, Atom, RSS 1.0. Matched on local
#: name and lowercased, because RSS 1.0's is `rdf:RDF`.
FEED_ROOTS = ("rss", "feed", "rdf")
#: xml:base, which Atom links are relative to when the feed sets it.
XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"

_TAGS = re.compile(r"<[^>]*>")
_USERINFO = re.compile(r"//[^/@]*@")
_FRACTION = re.compile(r"\.(\d+)")
_BARE_OFFSET = re.compile(r"([+-]\d{2})(\d{2})$")


class FeedError(RuntimeError):
    """A feed that could not be read."""


class RSS:
    """Items from a list of feed URLs, RSS 2.0 or Atom."""

    name = "rss"

    def __init__(self, feeds: Iterable[str] | str | None = None, *, timeout: float = 20.0,
                 summary_chars: int = DEFAULT_SUMMARY_CHARS) -> None:
        # The environment variable is a fallback for being constructed with nothing, which
        # is how the plugin loader does it, not an override: `creatoros.yaml` has already
        # resolved precedence by the time a caller passes feeds in.
        self.feeds = _split(feeds if feeds else os.environ.get("CREATOROS_RSS_FEEDS", ""))
        self.timeout = timeout
        self.summary_chars = summary_chars
        #: Why individual feeds failed on the last `fetch`, so a caller can report a feed
        #: that has been quietly 404ing for a fortnight.
        self.problems: list[str] = []

    def available(self) -> Availability:
        """Whether there is anything to fetch. Deliberately offline.

        `creatoros doctor` calls this for every installed provider, so it does not touch
        the network: fetching ten feeds to answer a question about configuration would be
        slow and would report a failure for a feed that happens to be returning 503 this
        minute. A feed being down is not a configuration problem — `fetch` survives one —
        so the only thing checked here is whether the URLs could ever work.
        """
        if not self.feeds:
            return Availability.no(
                "no feeds configured",
                "List the feeds you want in creatoros.yaml:\n"
                "  research:\n"
                "    rss:\n"
                "      feeds:\n"
                "        - https://hnrss.org/frontpage\n"
                "        - https://www.youtube.com/feeds/videos.xml?channel_id=UCxxxxxxxx\n"
                "or set CREATOROS_RSS_FEEDS to the same list, comma-separated.",
            )
        bad = [_redact(url) for url in self.feeds if not _is_http(url)]
        if bad:
            return Availability.no(
                f"not fetchable over HTTP: {', '.join(bad)}",
                "Give each feed its full address, scheme included — "
                "https://example.com/feed.xml, not example.com/feed.xml and not a file path.",
            )
        return Availability.yes(f"{len(self.feeds)} feed{'' if len(self.feeds) == 1 else 's'}")

    def fetch(self, *, since: dt.datetime | None = None, limit: int = 20,
              query: str = "") -> list[Item]:
        """Items from every feed, newest first.

        One unreachable or malformed feed does not take out the rest; its reason goes to
        `self.problems` and the others are still returned. Only when *no* feed could be
        read at all does this raise — a source that returns `[]` when it reached nothing
        is how a pipeline makes no videos and says nothing. A feed that was read and had
        nothing to say is not a failure and is not counted as one.

        `limit` is a ceiling, and zero or less is a ceiling of zero: callers work theirs
        out as `cap - len(already_have)`, so asking for nothing has to get nothing.
        """
        if since is not None and since.tzinfo is None:
            raise ValueError("since must be timezone-aware")
        if not self.feeds:
            raise ValueError(
                "no RSS feeds configured: set research.rss.feeds in creatoros.yaml, or "
                "CREATOROS_RSS_FEEDS"
            )

        self.problems = []
        reached = 0
        found: dict[str, Item] = {}
        for feed in self.feeds:
            try:
                harvest = self._read(feed)
            except Exception as exc:
                self.problems.append(f"{_redact(feed)}: {_why(exc)}")
                continue
            reached += 1
            for item in harvest:
                # The same post shows up in both a site feed and one of its category feeds
                # often enough to collapse here rather than leaving every caller to notice.
                # A dated copy beats an undated one, because that one can be sorted.
                seen = found.get(item.key())
                if seen is None or (seen.published is None and item.published is not None):
                    found[item.key()] = item
        if self.problems and not reached:
            # This used to test `not found`, which asks a different question. One live
            # feed having a genuinely quiet day beside one stale URL then raised "every
            # feed failed": untrue, and it buried the single feed that really was broken.
            raise FeedError("no feed could be read — " + "; ".join(self.problems))

        items = list(found.values())
        needle = query.strip().lower()
        if needle:
            # One phrase, not a bag of words: "ai agents" matches "AI agents" and not
            # every item that happens to mention AI.
            items = [i for i in items if needle in f"{i.title}\n{i.summary}".lower()]

        # Undated items are kept, not dropped. Feeds with no usable timestamp are common,
        # and `since` cannot rule out an item whose age is unknown, so dropping them would
        # make such a feed look permanently empty — the exact silent-nothing failure this
        # module exists to avoid. They sort last, so an item of unknown age never outranks
        # one that is genuinely new, and `limit` sheds them first.
        dated = [i for i in items if i.published is not None]
        undated = [i for i in items if i.published is None]
        if since is not None:
            dated = [i for i in dated if i.published >= since]
        dated.sort(key=lambda i: i.published, reverse=True)

        ordered = dated + undated
        # `limit <= 0` used to mean "no ceiling", so a caller whose budget was already
        # spent asked for nothing and was handed the whole feed to render.
        return ordered[:limit] if limit > 0 else []

    def _read(self, feed: str) -> list[Item]:
        if not _is_http(feed):
            # urllib opens file:// and ftp:// just as happily, so a pasted path or a typo
            # in the feed list must not become a local file read.
            raise FeedError("not an http(s) URL")
        request = urllib.request.Request(
            feed, headers={"User-Agent": USER_AGENT, "Accept": ACCEPT})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = response.read(MAX_BYTES + 1)
            # Relative links resolve against where the feed was actually served from, so
            # a feed that has moved host resolves against the redirect target rather than
            # the address still sitting in creatoros.yaml.
            landed = response.geturl() or feed
        if len(raw) > MAX_BYTES:
            raise FeedError(f"over {MAX_BYTES // (1024 * 1024)} MiB, so probably not a feed")

        root = _parse(raw)
        kind = _local(root.tag)
        if kind.lower() not in FEED_ROOTS:
            # Only the scan for item/entry used to stand between a web page and a report
            # of success: a feed replaced by an HTML error page or a sitemap returned []
            # and raised nothing, which reads exactly like a fortnight of quiet days.
            raise FeedError(f"200 but not a feed: root element is <{kind or '?'}>")
        channel = _child(root, "channel")           # RSS nests one; Atom's root is the feed
        title = _plain(_text_of(_child(root if channel is None else channel, "title")), 80)
        base = landed if _is_http(landed) else feed
        items = []
        for node, item_base in _entries(root, base):
            item = self._item(node, feed, title, item_base)
            if item is not None:
                items.append(item)
        return items

    def _item(self, node: ET.Element, feed: str, feed_title: str,
              base: str) -> Item | None:
        title = _plain(_text_of(_child(node, "title")), 300)
        url = _link(node, base)
        if not title and not url:
            return None
        return Item(
            # Microblog-style feeds publish untitled items. The URL is a poor headline but
            # a reviewer can at least tell two of them apart, which an empty string loses.
            title=title or _redact(url),
            url=url,
            # Atom prefers `summary` over the full `content`; RSS 2.0 has `description`,
            # and `content:encoded` is what WordPress puts the real text in.
            summary=_plain(_first_text(node, "description", "summary", "encoded", "content"),
                           self.summary_chars),
            published=_date(_first_text(node, "pubDate", "published", "updated", "date")),
            # The feed's own title, because with ten feeds configured a `source` of "rss"
            # tells whoever reviews the candidates nothing about where this came from.
            source=feed_title or _host(feed),
            extra={"feed": _redact(feed)},
        )


def _split(feeds: Iterable[str] | str) -> list[str]:
    raw = feeds.split(",") if isinstance(feeds, str) else list(feeds)
    out: list[str] = []
    for entry in raw:
        url = str(entry).strip().strip("'\"")
        if url and url not in out:          # a feed listed twice would be fetched twice
            out.append(url)
    return out


def _is_http(url: str) -> bool:
    return url.lower().startswith(("http://", "https://"))


def _host(url: str) -> str:
    return url.split("://", 1)[-1].split("/", 1)[0].rsplit("@", 1)[-1]


def _redact(url: str) -> str:
    """A feed URL that is safe to put in a message.

    A private feed carries its token in the query string, and some carry credentials in
    the authority, so neither is ever repeated back to the user or into a log.
    """
    return _USERINFO.sub("//", url.split("?", 1)[0])[:120]


def _local(tag: object) -> str:
    """An element's name without its namespace.

    Atom 1.0, Atom 0.3 and RSS 1.0 each namespace their elements and RSS 2.0 namespaces
    none of them, so matching `{ns}entry` against one fixed namespace is precisely how a
    reader ends up silently returning nothing for half its feeds. A comment node's tag is
    a callable rather than a string, hence the type check.
    """
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child(node: ET.Element, name: str) -> ET.Element | None:
    for child in list(node):
        if _local(child.tag) == name:
            return child
    return None


def _text_of(node: ET.Element | None) -> str:
    """Everything inside an element, child elements included.

    Atom's `type="xhtml"` content is real markup in child elements rather than escaped
    text, and there `node.text` is the handful of words before the first `<p>`. The pieces
    are joined with a space because that markup is usually paragraphs, and concatenating
    them gives `end of one.Start of the next`; `_plain` collapses the runs of whitespace
    that inline markup leaves behind in exchange.
    """
    return "" if node is None else " ".join(node.itertext())


def _first_text(node: ET.Element, *names: str) -> str:
    for name in names:
        text = _text_of(_child(node, name)).strip()
        if text:
            return text
    # YouTube's entries keep the description in <media:group><media:description>, a level
    # deeper than Atom puts anything, so a descendant of the same local name is the
    # fallback before giving up.
    for descendant in node.iter():
        if _local(descendant.tag) in names:
            text = _text_of(descendant).strip()
            if text:
                return text
    return ""


def _entries(node: ET.Element, base: str) -> Iterator[tuple[ET.Element, str]]:
    """Every item or entry in document order, each with the xml:base in force on it.

    ElementTree elements have no parent, so the base is carried down the tree instead of
    looked up from the element: Atom lets a feed set xml:base on the feed, on the entry,
    or on neither, and a relative link is relative to the nearest one.
    """
    here = _rebase(base, node)
    if _local(node.tag) in ("item", "entry"):
        yield node, here
        return
    for child in list(node):
        yield from _entries(child, here)


def _rebase(base: str, node: ET.Element) -> str:
    """`base` as this element changes it, which is usually not at all."""
    attr = (node.get(XML_BASE) or "").strip()
    if not attr:
        return base
    return _resolve(base, attr) or base


def _resolve(base: str, reference: str) -> str:
    try:
        return urllib.parse.urljoin(base, reference)
    except ValueError:      # a malformed authority, e.g. "http://[::1" — not a URL
        return ""


def _absolute(base: str, reference: str) -> str:
    """`reference` as an absolute http(s) URL, or "" when it cannot be one.

    Resolution happens before the scheme check rather than instead of it, so `mailto:`
    and `javascript:` are still refused, and an xml:base pointing somewhere odd cannot
    turn a relative href into a `file://` read.
    """
    if not reference:
        return ""
    resolved = _resolve(base, reference) if base else reference
    return resolved if _is_http(resolved) else ""


def _link(node: ET.Element, base: str) -> str:
    """The item's own page, as an absolute http(s) URL.

    RSS puts it in `<link>`'s text, Atom in a `href` attribute — and Atom reuses that
    element for machine links (`rel="self"`, the comments feed, enclosures), so `rel` has
    to be read instead of taking the first `href` and hoping.

    An Atom href is a URI reference, so relative is legal and common, and it used to be
    taken verbatim: two unrelated sites whose newest post sat at `/2026/01/hello.html`
    produced the same `Item.key()`, and dedup across sources threw a real story away. An
    RSS `<link>`, which the spec requires to be absolute, is still only accepted absolute
    — resolving scheme-less text like "example.com/post" against the feed would invent a
    URL on the wrong host instead of falling through to the permalink guid below.
    """
    other = ""
    for child in list(node):
        if _local(child.tag) != "link":
            continue
        text = (child.text or "").strip()
        if _is_http(text):
            return text
        href = _absolute(base, (child.get("href") or "").strip())
        if not href:
            continue
        if (child.get("rel") or "alternate").strip().lower() == "alternate":
            return href
        other = other or href
    for name in ("guid", "id"):
        text = _text_of(_child(node, name)).strip()
        if _is_http(text):                  # a permalink guid, which beats rel="self"
            return text
    return other


def _plain(raw: str, cap: int) -> str:
    """Feed text as something that can be printed, scored or read aloud.

    Tags are stripped, entities are unescaped, then tags are stripped once more: plenty of
    feeds double-escape their HTML, where `&lt;p&gt;` only becomes a tag after the
    unescape. The pattern is `<[^>]*>`, which has nothing to backtrack over — a summary is
    text from an arbitrary site and is not worth a regex that can hang the pipeline.
    """
    text = html.unescape(_TAGS.sub(" ", raw or ""))
    text = " ".join(_TAGS.sub(" ", text).split())
    if 0 < cap < len(text):
        head = text[:cap].rsplit(" ", 1)[0] or text[:cap]
        text = head.rstrip(" ,;:.") + "…"
    return text


def _parse(raw: bytes) -> ET.Element:
    """The feed's root element.

    `raw` stays bytes so that the XML declaration decides the encoding; decoding it here
    and guessing wrong is how a feed of accented titles becomes mojibake. A BOM or a blank
    line in front of the declaration is a hard parse error and common enough in the wild
    to be worth trimming.
    """
    start = raw.find(b"<")
    if start > 0:
        raw = raw[start:]
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise FeedError(f"not parseable as XML ({exc})") from exc


def _date(raw: str) -> dt.datetime | None:
    """One timestamp, in whichever of the two formats the feed actually used.

    RSS 2.0 specifies RFC 822 and Atom specifies ISO 8601, but feeds mix them freely, so
    both are tried rather than trusting the feed's flavour. A timestamp that parses with
    no offset is read as UTC: assuming the machine's zone instead would make one feed sort
    differently on a laptop and on a server.
    """
    text = (raw or "").strip()
    if not text:
        return None
    moment = _iso(text) or _rfc822(text)
    if moment is None:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=dt.timezone.utc)


def _iso(text: str) -> dt.datetime | None:
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    # 3.10's `fromisoformat` is stricter than 3.11's: it takes a fractional second of
    # exactly three or six digits and an offset only with its colon.
    text = _FRACTION.sub(lambda m: "." + (m.group(1) + "000000")[:6], text, count=1)
    text = _BARE_OFFSET.sub(r"\1:\2", text)
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return None


def _rfc822(text: str) -> dt.datetime | None:
    try:
        return email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        return None


def _why(exc: Exception) -> str:
    """One line saying what went wrong, with no URL in it.

    A feed URL can carry a token, and an HTTP error body can quote the URL back, so only
    the status and the socket reason are used.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code} {exc.reason}"
    if isinstance(exc, urllib.error.URLError):
        return f"unreachable ({exc.reason})"
    return f"{type(exc).__name__}: {exc}"
