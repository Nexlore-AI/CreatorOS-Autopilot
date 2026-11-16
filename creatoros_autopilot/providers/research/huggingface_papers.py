# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which papers people are actually looking at today, from Hugging Face's daily list.

The arXiv source answers "what was published". Nothing in it answers "what did anyone
care about": arXiv publishes no popularity signal of any kind, so every `Item.score` it
produces is 0.0 and a run that wants the day's best topic has nothing to rank on. The
Hugging Face daily list is curated and then voted on, and those votes are the whole
reason this module exists. Upvotes become `Item.score`, and a window of days comes back
highest-score first rather than newest first — the protocol's "newest first" is the
sensible default for a feed, and this source would be pointless with it.

**Nothing here says who wrote a paper.** The org badge on a Hugging Face paper page, the
account shown as having submitted it and the accounts linked against its authors all
describe Hugging Face activity: who uploaded weights, who posted the paper to the daily
list, who claimed it. They are frequently not the paper's affiliation. This project has
already read an affiliation off that badge and put the wrong lab's name on screen in
front of that lab's own audience, so `extra` carries no affiliation field at all and the
submitter is labelled as the submitter. Affiliations come from the paper's own metadata,
which `providers/research/arxiv.py` reads and which is empty when the authors gave none —
an empty field is the honest answer and a badge is not a substitute for it.

No key is involved: the endpoint is a public read. Hugging Face caps anonymous API calls
at 500 per five-minute window per IP, which is the budget `fetch`'s day cap protects.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from ... import __version__
from ...interfaces.availability import Availability
from ...interfaces.research import Item

API = "https://huggingface.co/api/daily_papers"
PAPER_PAGE = "https://huggingface.co/papers"
USER_AGENT = (f"CreatorOS-Autopilot/{__version__} "
              "(+https://github.com/Nexlore-AI/CreatorOS-Autopilot)")

# The endpoint's own ceiling: `limit=101` is an HTTP 400. A day's list runs 20-50 papers
# and arrives sorted by upvotes descending, so one page is the whole of a normal day and,
# on a day that ever overflowed it, the page holds exactly the papers this source is for.
# That is why there is no pagination loop here.
DAY_LIMIT = 100

# One request per day in the window, so `since` decides how many calls this makes.
DEFAULT_DAYS = 3        # today, and enough of the past to clear a weekend
MAX_DAYS = 14

# An arXiv id goes straight into a URL and into `extra`, where another source will
# compare it. Anything with whitespace or a path escape in it is dropped rather than
# repaired. Old-style ids (cs/0112017) contain a slash and are legitimate.
IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]*(/[A-Za-z0-9.\-]+)?$")
VERSIONED = re.compile(r"^(?P<base>.+?)v(?P<n>\d+)$")

PROBE = f'curl -sS "{API}?limit=1" | head -c 200'
STATUS = "https://status.huggingface.co"
SHAPE_FIX = ("The endpoint's shape has changed, so this needs a code change rather than a "
             f"setting. Please open an issue with the output of: {PROBE}")


class HuggingFaceError(RuntimeError):
    """A request that did not come back with a day's list.

    `status` is the HTTP status when there was one and 0 when the request never arrived,
    because those two need different advice.
    """

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class HuggingFaceShape(HuggingFaceError):
    """The endpoint answered, but not with a list of papers.

    Separate from its parent because the advice is different in kind: no amount of
    retrying or checking the network fixes an endpoint that has changed shape.
    """


def _clean(text: object) -> str:
    """One line of whitespace-normalised text.

    Titles and abstracts arrive on a single line today, but the text is arXiv's and
    arXiv hard-wraps it. The renderer puts a title straight on screen, where a stray
    newline breaks a word in half for no reason the viewer can see, so this does not
    depend on Hugging Face continuing to normalise it.
    """
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _when(stamp: object) -> dt.datetime | None:
    text = _clean(stamp)
    if not text:
        return None
    try:
        # `fromisoformat` only learned to read a trailing Z in 3.11, and this project
        # supports 3.10.
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _aware(moment: dt.datetime) -> dt.datetime:
    """`since` as something comparable with a UTC date.

    A naive datetime is read as UTC, not as local time. Callers get `since` from the
    schedule, which works in UTC, and reading it locally would move the window's edge by
    a day depending on where the machine is.
    """
    return moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)


def _said(exc: urllib.error.HTTPError) -> str:
    """Hugging Face's own explanation of a rejected request.

    It answers with `{"error": "..."}`, and that sentence is worth more than the status
    line: it is how one learns that a date is past the newest list rather than malformed.
    Nothing here is authenticated, so the body cannot contain anything of the user's.
    """
    try:
        body = json.loads(exc.read() or b"{}")
    except Exception:
        return _clean(exc.reason)
    said = _clean(body.get("error")) if isinstance(body, dict) else ""
    return said[:200] or _clean(exc.reason)


def _names(paper: dict) -> list[str]:
    """Author names as the paper's metadata gives them.

    Each author may also carry a linked Hugging Face `user`: an account that claimed the
    paper, which is a claim and not verified authorship. Only the plain name is kept, and
    no affiliation is read from any of it.
    """
    out: list[str] = []
    for author in paper.get("authors") or []:
        if not isinstance(author, dict):
            continue
        name = _clean(author.get("name"))
        if name and name not in out:
            out.append(name)
    return out


def _item(entry: object, source: str, day: str = "") -> Item | None:
    """One list entry as an `Item`, or None if it is too broken to use."""
    if not isinstance(entry, dict):
        return None
    paper = entry.get("paper")
    if not isinstance(paper, dict):
        return None

    ident = _clean(paper.get("id"))
    versioned = VERSIONED.match(ident)
    if versioned:
        # The arXiv source builds its URL from a bare id, so the version suffix comes off
        # here too: `2411.1v2` and `2411.1` are the same paper and must dedupe as one.
        ident = versioned.group("base")
    title = _clean(paper.get("title")) or _clean(entry.get("title"))
    if not title or not ident or not IDENT.match(ident):
        return None

    try:
        score = float(paper.get("upvotes") or 0)
    except (TypeError, ValueError):
        score = 0.0

    submitter = entry.get("submittedBy")

    return Item(
        title=title,
        url=f"{PAPER_PAGE}/{ident}",
        summary=_clean(paper.get("summary")) or _clean(entry.get("summary")),
        published=_when(paper.get("publishedAt")) or _when(entry.get("publishedAt")),
        source=source,
        score=score,
        extra={
            # `Item.key()` deduplicates on the URL, and one paper has two of them: this
            # source links it at huggingface.co/papers/<id> and the arXiv source at
            # arxiv.org/abs/<id>, which are two keys and therefore two videos about the
            # same paper. The id is the only thing both sources agree on, so it travels
            # under the name the arXiv source already writes, with that source's URL
            # spelled out beside it.
            "arxiv_id": ident,
            "arxiv_url": f"https://arxiv.org/abs/{ident}",
            # Which day's list this came off, which is not the paper's publication date:
            # a list routinely carries a paper from last week.
            "daily_date": _clean(paper.get("submittedOnDailyAt"))[:10] or day,
            "authors": _names(paper),
            "num_comments": entry.get("numComments") or 0,
            "keywords": [k for k in (paper.get("ai_keywords") or []) if isinstance(k, str)],
            # Hugging Face's own model-written gloss, under a name that says so. The
            # paper's abstract is in `summary`; nothing should attribute this sentence to
            # the authors.
            "ai_summary": _clean(paper.get("ai_summary")),
            # The account that posted the paper to the daily list. Not an author and not
            # an affiliation — see the module docstring.
            "submitted_by": _clean(submitter.get("fullname")) if isinstance(
                submitter, dict) else "",
        },
    )


def _terms(query: str) -> list[str]:
    return [word for word in re.split(r"\s+", _clean(query).lower()) if word]


def _matches(terms: list[str], item: Item) -> bool:
    """Whether every word of the caller's query appears in the item.

    The endpoint accepts a `q` parameter and ignores it: a search for a word no paper
    contains comes back as the whole day's list. Silently unfiltered results are worse
    than a rejected query, so the filter happens here instead.
    """
    haystack = " ".join([item.title, item.summary, item.extra.get("ai_summary", ""),
                         " ".join(item.extra.get("keywords") or [])]).lower()
    return all(term in haystack for term in terms)


class HuggingFacePapers:
    """The Hugging Face daily papers list as a `ResearchSource`."""

    name = "huggingface_papers"

    def __init__(self, *, timeout: float = 30.0, max_days: int = MAX_DAYS) -> None:
        self.timeout = timeout
        # Not clamped to MAX_DAYS: a `since` reaching back to 2023 is a mistake and the
        # cap exists to stop it, but a `max_days` of 60 in someone's config is a decision
        # they made about their own rate-limit budget.
        self.max_days = max(1, int(max_days))

    def available(self) -> Availability:
        """Whether the list is reachable right now. There is no key to be wrong."""
        try:
            entries = self._get(None)
        except HuggingFaceShape as exc:
            return Availability.no(str(exc), SHAPE_FIX)
        except HuggingFaceError as exc:
            if exc.status == 429:
                return Availability.no(
                    "rate limited by Hugging Face",
                    "Wait five minutes. Anonymous calls are capped at 500 per "
                    "five-minute window per IP, and no key raises that.",
                )
            if exc.status >= 500:
                return Availability.no(str(exc), f"Hugging Face's end, not yours — see {STATUS}")
            if exc.status:
                # This request carries no key, no date and a clamped limit, so there is
                # nothing in it for a 4xx to be about. The endpoint or the rules for
                # reaching it have changed.
                return Availability.no(str(exc), SHAPE_FIX)
            return Availability.no(str(exc), f"Check this machine can reach it: {PROBE}")
        if not entries:
            return Availability.no(
                "the newest daily papers list came back empty",
                f"Nothing is misconfigured — open {PAPER_PAGE} to see whether there is "
                "a list yet today, then retry.",
            )
        probe = _item(entries[0], self.name)
        if probe is None:
            return Availability.no("the newest entry carried no usable paper id or title",
                                   SHAPE_FIX)
        return Availability.yes(
            f"no key needed; newest list {probe.extra.get('daily_date') or 'unknown'}")

    def fetch(self, *, since: dt.datetime | None = None, limit: int = 20,
              query: str = "") -> list[Item]:
        """The window's papers, most upvoted first."""
        terms = _terms(query)
        seen: set[str] = set()
        items: list[Item] = []
        served, lost, failures = 0, [], []

        for day in self._window(since):
            try:
                entries = self._get(day)
            except HuggingFaceError as exc:
                if exc.status == 400:
                    # The only 400 a clamped request can earn is a date past the newest
                    # list, which the first day of the window is for the first hours of
                    # a UTC day. That is not a failure, it is a day that does not exist
                    # yet.
                    continue
                failures.append(exc)
                lost.append(day.isoformat())
                if exc.status == 429:
                    # Spending the rest of the window on requests that will also be
                    # refused makes the rate limit worse and the window no larger.
                    break
                continue
            served += 1
            for entry in entries:
                item = _item(entry, self.name, day.isoformat())
                if item is None:
                    continue
                if terms and not _matches(terms, item):
                    continue
                if item.extra["arxiv_id"] in seen:
                    # A paper can sit on two days' lists; it is still one video.
                    continue
                seen.add(item.extra["arxiv_id"])
                items.append(item)

        if failures and not served:
            # Every request failed, so this is the network or the service rather than a
            # quiet weekend, and the two must not look alike to the caller. One sentence,
            # raised once: a line per day would be the same message fourteen times on a
            # machine with no network. `available()` turns it into a fix.
            raise failures[0]
        if failures:
            # Some of the window arrived, so the run continues with it — but a short day
            # that was really a 500 must not pass for a quiet one.
            print(f"!! {self.name}: no list for {', '.join(lost)} ({failures[0]})")

        # Stable sort, and the days were walked newest first, so papers tied on upvotes
        # keep the newer day's one ahead.
        items.sort(key=lambda found: -found.score)
        return items[:max(1, int(limit))]

    def _window(self, since: dt.datetime | None) -> list[dt.date]:
        """The days to ask for, newest first.

        "Today" is the UTC date because the endpoint's own ceiling is, and asking for
        tomorrow is an HTTP 400 rather than an empty list — on a machine running ahead of
        UTC, a local date would make the first request of every run a rejected one.
        """
        today = dt.datetime.now(dt.timezone.utc).date()
        span = DEFAULT_DAYS if since is None else (today - _aware(since).date()).days + 1
        span = max(1, span)
        if span > self.max_days:
            # Truncating in silence would make a `since` of last year look like a
            # fortnight of quiet papers, and spend one request per day finding out.
            print(f"!! {self.name}: asked for {span} days of lists, using the newest "
                  f"{self.max_days}; one request per day, and a paper that old is not "
                  f"what this source is for")
            span = self.max_days
        return [today - dt.timedelta(days=n) for n in range(span)]

    def _get(self, day: dt.date | None) -> list:
        """One day's list, or the newest one when `day` is None."""
        params: dict[str, object] = {"limit": DAY_LIMIT}
        if day is not None:
            params["date"] = day.isoformat()
        url = f"{API}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise HuggingFaceError(
                f"Hugging Face returned HTTP {exc.code}: {_said(exc)}", exc.code) from exc
        except OSError as exc:
            # URLError, DNS failures and read timeouts all land here. On a machine with
            # no outbound network this is the first thing that fails, so it says where it
            # was trying to go.
            raise HuggingFaceError(f"could not reach {API} ({exc})") from exc
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise HuggingFaceError(
                f"{API} answered with something that is not JSON") from exc
        if not isinstance(payload, list):
            # A list is the whole contract of this endpoint. An object here is either an
            # error body or a different API than the one this was written against, and
            # either way there are no entries to read.
            raise HuggingFaceShape(
                f"{API} answered with {type(payload).__name__}, not a list of papers")
        return payload
