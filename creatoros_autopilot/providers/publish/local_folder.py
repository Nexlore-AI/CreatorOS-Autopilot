# SPDX-License-Identifier: AGPL-3.0-or-later
"""Publishing to a folder on this machine, which is what a first run should do.

Every other publisher wants an account before it can do anything: Zernio wants an API key,
the YouTube backend wants an OAuth client you have to create yourself. That made the first
thing a new user met a sign-up form, and the only way to find out whether the renderer
worked at all was to connect a real social account to it.

So this backend publishes nothing. It copies the finished video somewhere findable and
writes a sibling JSON file holding what would have been sent to each platform — title,
description, caption, tags, visibility, the slot it was aimed at. That file exists to be
read by a person who then posts by hand, so it is written for a reader rather than as a
dump of the request.

Two consequences worth knowing:

* `available()` is always ok. There is no key, no binary and no service, so there is
  nothing to be unavailable, and that is the point: `creatoros doctor` can always name one
  publisher that works on this machine.
* it accepts any platform name, not only the `local` it claims. A practice run aimed at
  instagram and youtube writes both sets of metadata, which is how you see what the
  pipeline would have posted before handing it a key.

Nothing here raises. A missing video or a full disk comes back as a `PublishResult` with
`ok=False`, because every caller already handles a per-platform failure, and a failure
here is no more interesting than that one.
"""
from __future__ import annotations

import datetime as dt
import filecmp
import json
import os
import re
import shutil
from pathlib import Path

from ...interfaces.availability import Availability
from ...interfaces.publish import PlatformOutcome, PublishRequest, PublishResult

ENV_DIR = "CREATOROS_PUBLISH_DIR"
DEFAULT_SUBDIR = "published"
METADATA_SUFFIX = ".publish.json"
MISSING = "missing_from_spec"

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class LocalFolder:
    """Copy the video into a folder and write down what would have been posted."""

    name = "local_folder"
    # "*" means any platform: this backend writes the video plus the metadata that would
    # have been sent, which is meaningful for every destination. Routing still prefers a
    # real backend unless this one is named in publish.backends.
    platforms = ("*",)
    third_party = False
    # Nothing is posted: this writes the video and the metadata that would
    # have been sent. Recording it as published would mark the job done and
    # the real publisher would skip it forever.
    records_publication = False

    def __init__(self, directory: str | os.PathLike | None = None) -> None:
        self.directory = Path(directory).expanduser() if directory else None

    def available(self) -> Availability:
        # Deliberately does not check that the directory exists or can be written to. It is
        # created at publish time, and a first-run default able to answer "no" is not a
        # default — doctor could then show a machine with no usable publisher at all.
        configured = self._configured()
        if configured:
            return Availability.yes(f"writing to {configured}")
        return Availability.yes(f"writing to <the video's folder>/{DEFAULT_SUBDIR}")

    def requires(self, platform: str) -> tuple[str, ...]:
        """Nothing. Writing a file is the one publisher that cannot reject a spec, and
        that is what makes it usable as a first run before any metadata exists."""
        return ()

    def publish(self, request: PublishRequest) -> PublishResult:
        asked = [p.strip() for p in request.platforms if p.strip()]
        wanted = tuple(dict.fromkeys(asked)) or self.platforms
        video = Path(os.path.abspath(Path(request.video).expanduser()))
        if not video.is_file():
            return self._failed(request, wanted, f"no such video: {video}")

        out_dir = self._configured() or video.parent / DEFAULT_SUBDIR
        day = _date_of(request, video)
        if day:
            out_dir = out_dir / day
        target = _free_target(out_dir, _safe_stem(request.job_id or video.stem),
                              video.suffix or ".mp4", video)
        meta_path = target.with_name(target.stem + METADATA_SUFFIX)
        spec = request.spec or _spec_beside(video)
        payload = _metadata(request, wanted, video, target, spec, _caption_beside(video))

        if request.dry_run:
            print(f"would copy {video}\n        to {target}")
            print(f"would write {meta_path}")
            for platform in wanted:
                print(f"  {platform}: {_summary(payload['platforms'][platform])}")
        else:
            try:
                _write(video, target, meta_path, payload)
            except OSError as exc:
                return self._failed(request, wanted, f"{type(exc).__name__}: {exc}")
            print(f"video:    {target}")
            print(f"metadata: {meta_path}")

        return PublishResult(
            ok=True, mode=request.mode, dry_run=request.dry_run,
            # There is no post, so the identity is the file stem: enough for a ledger row
            # to lead back to the two files this wrote.
            post_id=target.stem,
            outcomes=[PlatformOutcome(p, True, url=_uri(target), post_id=target.stem)
                      for p in wanted],
        )

    def _configured(self) -> Path | None:
        """The output directory from the environment, else the one settings passed in.

        The environment wins, in the order `core/settings.py` documents: writing one run
        somewhere else should not mean editing `creatoros.yaml`.
        """
        env = os.environ.get(ENV_DIR, "").strip()
        if env:
            return Path(env).expanduser()
        return self.directory

    def _failed(self, request: PublishRequest, platforms: tuple[str, ...],
                error: str) -> PublishResult:
        return PublishResult(
            ok=False, mode=request.mode, dry_run=request.dry_run, error=error,
            outcomes=[PlatformOutcome(p, False, error=error) for p in platforms],
        )


def _write(video: Path, target: Path, meta_path: Path, payload: dict) -> None:
    """Copy the video and write its metadata, leaving nothing half-done behind.

    The copy goes via `.part` because a video interrupted part-way through would otherwise
    look like a complete file that merely differs from its source, and the next run would
    keep it and write a second copy beside it.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    # `_free_target` only returns a path that is free or already byte-identical, so a
    # target that exists is this job rendered again and there is nothing to copy.
    if not target.exists():
        partial = target.with_name(target.name + ".part")
        try:
            shutil.copy2(video, partial)
            os.replace(partial, target)
        finally:
            if partial.exists():
                partial.unlink()
    # Rewritten even on a re-run: the video is unchanged, but a title or caption edited
    # since last time belongs here. The name is `.publish.json` rather than `.json` so it
    # can never land on top of a spec. `default=str` because the spec is user data, and one
    # odd value in it must not be able to stop the file being written.
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n"
    # Through `.part` too, for the reason the video is. Written directly, a rewrite that
    # died part-way — a full disk, a killed process — truncated the previous good sidecar,
    # which cost the only record of what to post for a video possibly already copied out
    # and replaced it with JSON nothing could parse.
    partial = meta_path.with_name(meta_path.name + ".part")
    try:
        partial.write_text(text, encoding="utf-8")
        os.replace(partial, meta_path)
    finally:
        if partial.exists():
            partial.unlink()


def _metadata(request: PublishRequest, platforms: tuple[str, ...], video: Path, target: Path,
              spec: dict, caption: str) -> dict:
    """The sidecar's contents, in the order someone reads them."""
    return {
        "how_to_post": [
            "Nothing was published: this publisher only writes files.",
            f"The video is {target.name}, beside this file.",
            "Each entry under 'platforms' is what would have been sent there, as it stands.",
            "To publish for real, set publish.platforms in creatoros.yaml.",
        ],
        "job_id": request.job_id,
        "video": target.name,
        "mode": request.mode,
        "scheduled_for": _when(request),
        "platforms": {p: _block(p, _entry_for(spec, p), caption) for p in platforms},
        "rendered_from": str(video),
        "written_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "written_by": f"creatoros {LocalFolder.name}",
    }


def _block(platform: str, entry: dict, caption: str) -> dict:
    """What one platform would have been sent, and what the spec never said.

    YouTube is the one platform with a shape worth knowing here, because it is the one
    whose post is more than a caption. For everything else — including a platform nobody
    has written a backend for yet — the caption is the post, and whatever else the spec
    says about it is passed through rather than dropped: we do not know what that platform
    needs, and the person reading this file does.
    """
    if platform == "youtube":
        description = str(entry.get("description") or caption)
        block = {
            "title": str(entry.get("title") or ""),
            "description": description,
            "tags": _tags(entry.get("tags"), description),
            # A publish request carries no visibility (it is a per-publisher setting), so
            # this is the spec's own value, else the public default the backends use.
            "visibility": str(entry.get("visibility") or "public"),
        }
        required = ("title", "description", "tags")
    else:
        text = str(entry.get("caption") or entry.get("description") or caption)
        # The hashtags are repeated on their own because on Instagram they are usually
        # pasted as a first comment rather than left in the caption.
        block = {"caption": text, "hashtags": _tags(entry.get("hashtags"), text)}
        required = ("caption",)

    for key, value in entry.items():
        block.setdefault(key, value)
    # Named as the dotted path it is missing from, so the gap can be closed by editing the
    # spec rather than by guessing where this field was supposed to come from.
    gaps = [f"platforms.{platform}.{key}" for key in required if not block.get(key)]
    if gaps:
        block[MISSING] = gaps
    return block


def _entry_for(spec: dict, platform: str) -> dict:
    platforms = spec.get("platforms")
    entry = platforms.get(platform) if isinstance(platforms, dict) else None
    return entry if isinstance(entry, dict) else {}


def _tags(given: object, text: str) -> list[str]:
    """The tags the spec names, else the ones already written into the text.

    The caption branch computed this from the `#` matches in the text and wrote the result
    over the spec's own list, and the `setdefault` pass afterwards cannot put a value back
    under a key that is already present. So a caption carrying no `#` — exactly when a
    separate list of hashtags is worth keeping — reached the sidecar as `hashtags: []`, and
    the tags were gone from the one file that says what to post.

    One string holding several is accepted because a hand-written spec says `tags: ai,
    hindi` often enough, and `list()` on that is thirteen one-letter tags. A leading `#` is
    dropped so a spec writing `#ai` and one writing `ai` read the same here, and so they
    match what is extracted from the text.
    """
    if isinstance(given, str):
        items: list = re.split(r"[,\s]+", given)
    elif isinstance(given, (list, tuple)):
        items = list(given)
    else:
        # Anything else goes through as a single tag rather than being dropped: a reader of
        # this file can see an odd value and correct the spec, but cannot see an absence.
        items = [given] if given else []
    named = [str(item).strip().lstrip("#").strip() for item in items]
    return list(dict.fromkeys(tag for tag in named if tag)) or _hashtags(text)


def _hashtags(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"#(\w+)", text)))


def _summary(block: dict) -> str:
    """One platform's fields, short enough for a dry run's printout."""
    filled = ", ".join(key for key, value in block.items() if key != MISSING and value)
    gaps = block.get(MISSING) or []
    tail = f"; missing {', '.join(g.rsplit('.', 1)[-1] for g in gaps)}" if gaps else ""
    return (filled or "nothing in the spec") + tail


def _when(request: PublishRequest) -> str | None:
    """The slot as a person reads it, or None when the run scheduled nothing.

    `request.at` is naive wall-clock time and the zone is a separate field (see
    `core/schedule.isoformat` for why), so the two are shown side by side instead of being
    merged into an offset that would be wrong for half the year.
    """
    stamp = str(request.at or "").strip()
    if not stamp:
        return None
    try:
        moment = dt.datetime.fromisoformat(stamp)
    except ValueError:
        return f"{stamp} {request.tz}".strip()
    return f"{moment:%Y-%m-%d %H:%M} {request.tz}".strip()


def _date_of(request: PublishRequest, video: Path) -> str:
    """The day this video belongs to, for the output subfolder.

    `request.at` is the slot it was aimed at, which is what someone looking for "Tuesday's
    four videos" means. Draft runs carry no slot, so the day falls back to the job folder
    the renderer wrote into (`jobs/YYYY-MM-DD/`) — and draft is the default mode, so
    without that fallback the common case piles every day into one flat directory.
    """
    for text in (str(request.at or "")[:10], video.parent.name):
        if _DATE.fullmatch(text):
            try:
                return dt.date.fromisoformat(text).isoformat()
            except ValueError:
                continue
    return ""


def _safe_stem(job_id: str) -> str:
    """A job id as a filename.

    A job id is free text from the spec. One with a slash in it would write outside the
    output directory, one with a space makes a path that needs quoting everywhere it is
    pasted, and one starting with a dot hides the file it names.
    """
    return _UNSAFE.sub("-", job_id.strip()).strip("-.") or "video"


def _free_target(directory: Path, stem: str, suffix: str, source: Path) -> Path:
    """Where the video goes, without ever overwriting something else.

    A second render of the same job lands on the same name. When the bytes are identical
    that is a re-run and the existing file is kept, so re-running does not litter the
    folder with copies. When they differ the new one is suffixed, because a publisher that
    silently replaced yesterday's video with today's would destroy the only copy of
    something the user may already have posted.
    """
    candidate, attempt = directory / f"{stem}{suffix}", 2
    while candidate.exists() and not _same(candidate, source):
        candidate = directory / f"{stem}-{attempt}{suffix}"
        attempt += 1
    return candidate


def _same(left: Path, right: Path) -> bool:
    try:
        return (left.stat().st_size == right.stat().st_size
                and filecmp.cmp(left, right, shallow=False))
    except OSError:
        return False


def _spec_beside(video: Path) -> dict:
    """The video's spec, for when the caller passed none.

    `creatoros publish one.mp4` has no spec in hand, and the per-platform metadata is the
    only thing worth writing out, so it is read from the sibling JSON the renderer left.
    Read with `json` rather than `core.spec.load` because an unloadable spec here means a
    thinner metadata file, not a failed publish.
    """
    try:
        data = json.loads(video.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _caption_beside(video: Path) -> str:
    """The sibling `<name>.caption.txt`, which is where the pipeline leaves the caption."""
    try:
        return video.with_suffix(".caption.txt").read_text(encoding="utf-8",
                                                           errors="replace").strip()
    except OSError:
        return ""


def _uri(path: Path) -> str:
    """A file:// URL, built by pathlib so a Windows drive letter and a space both survive."""
    return Path(os.path.abspath(path)).as_uri()
