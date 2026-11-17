# SPDX-License-Identifier: AGPL-3.0-or-later
"""`creatoros migrate-ledger` — bring an old publish record into the new one.

This is the step of the cutover that can do real damage. The old pipeline kept two ledgers
that disagreed with each other:

* `publish_ledger.jsonl`, one row per `(repo-relative path, platform)`;
* `shorts/ledger.jsonl`, one row per post covering every platform at once, keyed on an
  **absolute** path.

The new ledger is keyed on `(job id, platform)`, because a path is not an identity — moving
the output folder used to re-arm every video in it for publishing.

So the migration has to resolve each old row's path to the job id in that video's spec. A
row it cannot resolve is **reported, never dropped**: a dropped row means a video that was
already published looks unpublished, and the next run posts it a second time. That is the
failure this command exists to prevent, so it defaults to a dry run and will not write
anything until asked twice.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from ..core import spec as spec_mod
from ..core.ledger import Entry, Ledger, read_legacy

LEGACY_NAMES = ("publish_ledger.jsonl", "shorts/ledger.jsonl", "ledger.jsonl")


@dataclass
class Migration:
    imported: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)
    bookkeeping: list = field(default_factory=list)
    assumed: int = 0
    already_present: int = 0
    sources: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unresolved


def is_bookkeeping(path: str) -> bool:
    """Whether an old row records an action rather than a video.

    The promotion tools wrote rows with a literal `(promote)` or `(backlog)` in the file
    field: they note that a draft was taken live, not that a video was published for the
    first time. The underlying publication has its own row with a real path.

    This distinction matters because the two look identical to a path resolver, and
    reporting them as unresolved would tell the user they are about to double-post 79
    videos when they are not. A row that cannot be resolved is a risk; a row that was never
    about a video is not.
    """
    name = Path(path).name
    return name.startswith("(") and name.endswith(")")


def job_id_for(path: str, roots: list) -> str:
    """Resolve an old ledger row's video path to the job id in its spec.

    Tries the path as recorded, then the same filename under each root — the old rows hold
    absolute paths from a machine whose layout has since changed, which is the whole reason
    paths make poor identities.
    """
    candidates = [Path(path)]
    name = Path(path).name
    for root in roots:
        candidates.append(Path(root) / name)
        candidates.extend(Path(root).rglob(name))
    for candidate in candidates:
        sibling = candidate.with_suffix(".json")
        if sibling.is_file():
            try:
                return spec_mod.job_id(spec_mod.load(sibling), sibling)
            except Exception:
                continue
    return ""


def plan(old_files: list, target: Ledger, roots: list,
         assume_platforms: list | None = None) -> Migration:
    """Work out what would be imported. Reads only.

    `assume_platforms` fills in rows from the shell ledger's earliest format, which
    recorded that a video published but not where. Fifteen of this channel's rows are like
    that. Without it they resolve to an empty platform, the ledger refuses them, and the
    summary still counts them as imported — fifteen already-published videos that the new
    ledger would not know about and the next run would post again.
    """
    result = Migration(sources=[str(f) for f in old_files])
    assume = [p for p in (assume_platforms or []) if p]
    seen = {(e.job_id, e.platform) for e in target.entries() if e.ok}
    for old in old_files:
        for row in read_legacy(old, job_id_for=lambda p: job_id_for(p, roots)):
            if not row.ok:
                # A failed attempt carries no claim about the platform's state, so there is
                # nothing to preserve and nothing that could cause a double-post.
                continue
            if not row.job_id:
                (result.bookkeeping if is_bookkeeping(row.file)
                 else result.unresolved).append(row)
                continue
            platforms = [row.platform] if row.platform else assume
            if not platforms:
                # Published, but we cannot say where. Reported, never dropped.
                result.unresolved.append(row)
                continue
            for platform in platforms:
                if (row.job_id, platform) in seen:
                    result.already_present += 1
                    continue
                seen.add((row.job_id, platform))
                copy = replace(row, platform=platform)
                if not row.platform:
                    result.assumed += 1
                result.imported.append(copy)
    return result


def apply(result: Migration, target: Ledger) -> int:
    """Write the planned rows, and return how many actually landed.

    Counting what was written rather than what was planned is the point: an earlier version
    reported 79 imported while 15 were rejected by the ledger and lost, which is exactly the
    silent-wrong failure this command exists to prevent.
    """
    written = 0
    for row in result.imported:
        target.record(Entry(
            job_id=row.job_id, platform=row.platform, ok=True, mode=row.mode,
            ts=row.ts, when=row.when, file=row.file, title=row.title,
            post_id=row.post_id, schema=row.schema,
            extra={"migrated_from": "legacy ledger"}))
        written += 1
    return written


def find_legacy(directory: Path) -> list:
    return [directory / name for name in LEGACY_NAMES if (directory / name).is_file()]


def run(args, settings) -> int:
    source_dir = Path(args.source).expanduser() if args.source else Path.cwd()
    old_files = find_legacy(source_dir)
    if not old_files:
        print(f"no legacy ledger in {source_dir}\n"
              f"  looked for: {', '.join(LEGACY_NAMES)}")
        return 1

    roots = [Path(r).expanduser() for r in (args.roots or [])] or [source_dir]
    assume = ([p.strip() for p in args.assume_platforms.split(",") if p.strip()]
              if args.assume_platforms else list(settings.publish.platforms))
    target = Ledger(settings.ledger_path)
    result = plan(old_files, target, roots, assume)

    print(f"source : {', '.join(result.sources)}")
    print(f"target : {settings.ledger_path}")
    print(f"roots  : {', '.join(str(r) for r in roots)}\n")
    if assume:
        print(f"assume : {', '.join(assume)}   (for rows that recorded no platform)\n")
    print(f"  would import     : {len(result.imported)}")
    if result.assumed:
        print(f"    of which        : {result.assumed} used the assumed platforms above")
    print(f"  already recorded : {result.already_present}")
    if result.bookkeeping:
        print(f"  bookkeeping rows : {len(result.bookkeeping)}  (not video publications; "
              f"skipped safely)")
    print(f"  unresolved       : {len(result.unresolved)}")

    for row in result.unresolved[:10]:
        print(f"    ? {row.platform or '(no platform)'}  {row.file or '(no file)'}")
    if len(result.unresolved) > 10:
        print(f"    … and {len(result.unresolved) - 10} more")

    if result.unresolved:
        print("\nAn unresolved row is a video that was published but whose spec could not be\n"
              "found, so the new ledger would not know about it and the next run would post\n"
              "it again. Point --roots at the folder holding those specs, or pass --force if\n"
              "you are certain those videos should be published again.")

    if not args.write:
        print("\nNothing was written. Re-run with --write to apply.")
        return 0 if result.ok else 2
    if result.unresolved and not args.force:
        print("\nRefusing to write with unresolved rows. Use --force to override.")
        return 2

    written = apply(result, target)
    print(f"\nwrote {written} row(s) to {settings.ledger_path}")
    if written != len(result.imported):
        print(f"!! planned {len(result.imported)} but wrote {written} — "
              f"{len(result.imported) - written} row(s) were rejected. Those videos are "
              f"published but unrecorded, and a run would post them again. Do not proceed.")
        return 2
    return 0
