# SPDX-License-Identifier: AGPL-3.0-or-later
"""Recent papers from arXiv, through its public Atom API.

Choosing the day's topic used to happen inside the agent's prompt: it searched, picked a
paper, and the choice left no trace. A day that picked badly could not be reproduced and
a day that picked well could not be tested. Here the same job is a function of
(since, limit, query) -> items over a feed that needs no key, so it can be unit tested
against a recorded response and swapped for another source by name.

The categories are a constructor argument rather than a constant. cs.AI/cs.LG/cs.CL is
the default because that is the channel this grew out of, but a biology channel passes
q-bio and gets the identical code path; nothing in this module knows what a paper is
about.

arXiv's API Terms of Use ask callers for at most one request every three seconds over a
single connection, and ask clients to identify themselves in the User-Agent. Those are
arXiv's own numbers, not a guess at politeness, and they are honoured in `_wait_turn`
below rather than left to each caller's good behaviour.
"""
from __future__ import annotations

import datetime as dt
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from ... import __version__
from ...interfaces.availability import Availability
from ...interfaces.research import Item

# The endpoint arXiv documents. It redirects to https, which urllib follows, so the
# documented address is kept here rather than a guess at where the redirect lands.
API = "http://export.arxiv.org/api/query"
DEFAULT_CATEGORIES = ("cs.AI", "cs.LG", "cs.CL")
MAX_RESULTS = 2000              # arXiv's documented ceiling for one request
MIN_INTERVAL = 3.0              # seconds between requests, per arXiv's terms of use
USER_AGENT = (f"CreatorOS-Autopilot/{__version__} "
              "(+https://github.com/Nexlore-AI/CreatorOS-Autopilot)")

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"

# cs.AI, stat.ML, q-bio.NC, math-ph, hep-th: letters, dots and hyphens, nothing else.
CATEGORY = re.compile(r"^[A-Za-z][A-Za-z-]*(\.[A-Za-z][A-Za-z-]*)?$")
# A query the caller has already written in arXiv's own field syntax, which must be
# passed through untouched instead of being wrapped.
FIELDED = re.compile(r"\b(all|ti|abs|au|co|jr|cat|rn|id):")

PROBE = f'curl -sS "{API}?search_query=all&max_results=1" | head -5'
TAXONOMY = "https://arxiv.org/category_taxonomy"

_pace = threading.Lock()
_last_request = 0.0


class ArXivError(RuntimeError):
    """A request that did not come back as a feed. `status` is 0 when it never arrived."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


def _wait_turn(min_interval: float) -> None:
    """Block until it has been `min_interval` seconds since anyone's last request.

    The clock is module-level because the rate limit belongs to arXiv's view of this
    machine, not to one object. `creatoros doctor` builds a source to call `available()`
    and the run builds another to call `fetch()`; two instances pacing themselves
    separately would hit arXiv at twice the rate they each think they are using.
    """
    global _last_request
    with _pace:
        gap = min_interval - (time.monotonic() - _last_request)
        if gap > 0:
            time.sleep(gap)
        _last_request = time.monotonic()


def _squash(text: str | None) -> str:
    """Collapse the feed's line wrapping into one line.

    arXiv hard-wraps titles and abstracts, so a raw title arrives with newlines and
    runs of spaces inside it. Those survive into the spec and then onto the screen,
    where a title breaks in the middle of a word for no reason the viewer can see.
    """
    return re.sub(r"\s+", " ", text or "").strip()


def _when(entry: ET.Element) -> dt.datetime | None:
    for tag in ("published", "updated"):
        stamp = (entry.findtext(f"{ATOM}{tag}") or "").strip()
        if not stamp:
            continue
        try:
            # `fromisoformat` only learned to read a trailing Z in 3.11, and this project
            # supports 3.10.
            return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


def _aware(moment: dt.datetime | None) -> dt.datetime | None:
    """`since` as something comparable with a feed timestamp, which is always UTC.

    A naive datetime is read as UTC rather than as local time. Callers get `since` from
    the schedule, which works in UTC, and reading it as local time would quietly move the
    cut-off by hours in either direction depending on where the machine is.
    """
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)


def _entries(raw: bytes) -> list[ET.Element] | None:
    """The feed's entries, or None when `raw` is not XML at all.

    A rejected query is not an HTTP error: arXiv answers with 200 and a feed holding one
    entry whose id points at `/api/errors`. Left alone that entry becomes a candidate
    topic titled "incorrect id format for ...", so it is turned back into an exception
    here, carrying arXiv's own explanation of what it disliked.
    """
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    entries = root.findall(f"{ATOM}entry")
    for entry in entries:
        if "/api/errors" in (entry.findtext(f"{ATOM}id") or ""):
            said = _squash(entry.findtext(f"{ATOM}summary")) or _squash(entry.findtext(f"{ATOM}title"))
            raise ArXivError(f"arXiv rejected the query: {said[:200]}")
    return entries


def _item(entry: ET.Element, source: str) -> Item | None:
    """One feed entry as an `Item`, or None if it is too broken to use."""
    title = _squash(entry.findtext(f"{ATOM}title"))
    raw_id = (entry.findtext(f"{ATOM}id") or "").strip()
    match = re.search(r"/abs/(.+)$", raw_id)
    ident, version = (match.group(1) if match else ""), ""
    versioned = re.match(r"^(?P<base>.+?)v(?P<n>\d+)$", ident)
    if versioned:
        ident, version = versioned.group("base"), versioned.group("n")
    if not title or not ident:
        return None

    # The abs URL is rebuilt rather than taken from the feed, because `Item.key()`
    # deduplicates on the URL string. arXiv's own id is `http://arxiv.org/abs/2411.1v2`
    # while Hugging Face links the same paper as `https://arxiv.org/abs/2411.1`, so
    # keeping either the scheme or the version suffix makes one paper two videos.
    url = f"https://arxiv.org/abs/{ident}"

    authors, affiliations = [], []
    for author in entry.findall(f"{ATOM}author"):
        name = _squash(author.findtext(f"{ATOM}name"))
        if name:
            authors.append(name)
        for node in author.findall(f"{ARXIV}affiliation"):
            stated = _squash(node.text)
            if stated and stated not in affiliations:
                affiliations.append(stated)

    primary = entry.find(f"{ARXIV}primary_category")
    pdf = ""
    for link in entry.findall(f"{ATOM}link"):
        if link.get("title") == "pdf":
            pdf = (link.get("href") or "").replace("http://", "https://", 1)

    return Item(
        title=title,
        url=url,
        summary=_squash(entry.findtext(f"{ATOM}summary")),
        published=_when(entry),
        source=source,
        # No score. arXiv publishes no popularity signal of any kind, so this stays 0.0
        # and anything that ranks items must take its signal from a source that has one
        # rather than read the zero as "nobody cared".
        extra={
            # Authorship and affiliation come from the paper's own metadata and from
            # nowhere else. The failure mode this replaces: affiliations were read off a
            # Hugging Face org badge, which says who uploaded the weights rather than who
            # wrote the paper, and was wrong often enough to put the wrong lab on screen
            # in front of that lab's own audience. arXiv's Atom feed carries an
            # affiliation only when the submitter supplied one, so `affiliations` is
            # usually empty — the honest answer, and the cue to read the paper's HTML
            # title block rather than to guess.
            "authors": authors,
            "affiliations": affiliations,
            "arxiv_id": ident,
            "version": version,
            "primary_category": (primary.get("term") if primary is not None else "") or "",
            "categories": [c.get("term") for c in entry.findall(f"{ATOM}category") if c.get("term")],
            "pdf_url": pdf,
            "comment": _squash(entry.findtext(f"{ARXIV}comment")),
        },
    )


class ArXiv:
    """arXiv's Atom API as a `ResearchSource`."""

    name = "arxiv"

    def __init__(self, categories: str | list[str] | tuple[str, ...] = DEFAULT_CATEGORIES,
                 *, timeout: float = 30.0, min_interval: float = MIN_INTERVAL) -> None:
        if isinstance(categories, str):
            categories = categories.split(",")
        self.categories = [c.strip() for c in categories if c and c.strip()]
        self.timeout = timeout
        self.min_interval = min_interval

    def available(self) -> Availability:
        bad = [c for c in self.categories if not CATEGORY.match(c)]
        if bad:
            return Availability.no(
                f"not arXiv category names: {', '.join(bad)}",
                f"Write them as arXiv does — cs.AI, stat.ML, q-bio.NC — see {TAXONOMY}",
            )
        search = self._search("")
        try:
            raw = self._get(search, 1)
        except ArXivError as exc:
            # The first live run of this module got a 429, and "check the network" was
            # the wrong thing to tell the operator: the network was fine and the client
            # was at fault. A status at all means arXiv answered, so the fix is never
            # about reachability.
            if exc.status == 429:
                return Availability.no(
                    str(exc),
                    "Wait a minute before retrying, and raise min_interval above the "
                    f"{MIN_INTERVAL:g}s default if something else here also queries arXiv",
                )
            if exc.status:
                return Availability.no(str(exc), "Retry shortly, or check https://status.arxiv.org")
            return Availability.no(str(exc), f"Check this machine can reach arXiv: {PROBE}")
        try:
            entries = _entries(raw)
        except ArXivError as exc:
            return Availability.no(str(exc), f"Fix the configured categories — see {TAXONOMY}")
        if entries is None:
            return Availability.no(
                "arXiv answered with something that is not Atom XML",
                "Usually a maintenance page; retry in a few minutes, or check "
                "https://status.arxiv.org",
            )
        if not entries:
            return Availability.no(
                f"arXiv has nothing for {search}",
                f"Check the categories you configured for typos — see {TAXONOMY}",
            )
        return Availability.yes(f"no key needed; {search}")

    def fetch(self, *, since: dt.datetime | None = None, limit: int = 20,
              query: str = "") -> list[Item]:
        entries = _entries(self._get(self._search(query), limit))
        if entries is None:
            # Not XML, so there is nothing to read. arXiv serves a maintenance page with
            # a 200 status, and a source with nothing to say today must not take the run
            # down with it — the pipeline can still use the other sources.
            return []
        cutoff = _aware(since)
        items: list[Item] = []
        for entry in entries:
            item = _item(entry, self.name)
            if item is None:
                continue
            if cutoff and item.published and item.published < cutoff:
                # The feed is sorted newest-first, so the first entry older than the
                # cut-off is the end of the useful part of it.
                break
            items.append(item)
        return items

    def _search(self, query: str) -> str:
        """The `search_query` expression for a caller's words, or for the categories."""
        query = (query or "").strip()
        by_category = " OR ".join(f"cat:{c}" for c in self.categories)
        if not query:
            return by_category or "all"
        if FIELDED.search(query):
            return query
        # Plain words are scoped to the configured categories: a source set up for q-bio
        # that answered a search with machine-learning papers would be a surprise, not a
        # feature. A caller who wants the whole of arXiv says so in arXiv's syntax.
        scoped = f'all:"{query}"'
        return f"{scoped} AND ({by_category})" if by_category else scoped

    def _get(self, search: str, limit: int) -> bytes:
        params = {
            "search_query": search,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "start": 0,
            "max_results": max(1, min(int(limit), MAX_RESULTS)),
        }
        url = f"{API}?{urllib.parse.urlencode(params)}"
        _wait_turn(self.min_interval)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise ArXivError("arXiv is rate-limiting this address (HTTP 429)", 429) from exc
            raise ArXivError(f"arXiv returned HTTP {exc.code} {exc.reason}".strip(), exc.code) from exc
        except OSError as exc:
            # URLError, DNS failures and read timeouts all land here. The message matters:
            # on a machine with no outbound network this is the first thing that fails.
            raise ArXivError(f"could not reach {API} ({exc})") from exc
