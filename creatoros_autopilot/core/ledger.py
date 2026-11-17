# SPDX-License-Identifier: AGPL-3.0-or-later
"""The record of what has been published, and the thing that stops a double-post.

There used to be two ledgers that disagreed with each other:

  * `publish_ledger.jsonl`, written by the batch publisher, keyed on a repo-relative path
    and one row per `(path, platform)`;
  * `shorts/ledger.jsonl`, written by the shell runner on the single publisher's behalf,
    keyed on an **absolute** path and one row per post covering every platform at once.

That split had two consequences. The live publisher wrote neither file itself, so a draft
it created could not be found later by the promotion tools. And because identity was a
filesystem path, moving the output folder re-armed the idempotency check: every video in
it looked unpublished again.

So identity here is `(job_id, platform)`. The job id already exists in every spec as
`spec["id"]`, it does not change when a file moves, and making it per-platform means a
run that posted to YouTube and failed on Instagram retries only Instagram.

The file is append-only JSONL. It is the only thing standing between a re-run and a
duplicate post, so it is never rewritten in place and a row is never deleted.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA = 2


@dataclass
class Entry:
    job_id: str
    platform: str
    ok: bool
    mode: str = ""                  # draft | schedule | now
    ts: str = ""                    # UTC ISO8601
    when: str = ""                  # the scheduled slot, when mode == schedule
    file: str = ""                  # informational only, never an identity
    title: str = ""
    post_id: str = ""
    post_url: str = ""
    error: str = ""
    schema: int = SCHEMA
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str]:
        return (self.job_id, self.platform)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Ledger:
    """Append-only publish record with an idempotency check."""

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)

    # ------------------------------------------------------------------ reading
    def entries(self) -> Iterator[Entry]:
        """Every readable row, oldest first. Unparseable rows are skipped, not fatal.

        A single corrupt line — a half-written row from a machine that lost power
        mid-append — must not make the whole ledger unreadable, because an unreadable
        ledger means every video looks unpublished and the next run posts the lot again.
        """
        if not self.path.is_file():
            return
        with self.path.open(encoding="utf-8") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict) or "job_id" not in data:
                    continue
                known = set(Entry.__dataclass_fields__)
                extra = {k: v for k, v in data.items() if k not in known}
                row = {k: v for k, v in data.items() if k in known}
                row.setdefault("platform", "")
                row.setdefault("ok", False)
                if extra:
                    row["extra"] = {**row.get("extra", {}), **extra}
                try:
                    yield Entry(**row)
                except TypeError:
                    continue

    def done(self, job_id: str, platform: str) -> bool:
        """Whether this job already published successfully to this platform."""
        return any(e.ok and e.key == (job_id, platform) for e in self.entries())

    def done_platforms(self, job_id: str) -> set[str]:
        return {e.platform for e in self.entries() if e.ok and e.job_id == job_id}

    def pending(self, job_id: str, platforms: list[str]) -> list[str]:
        """The platforms this job still needs, preserving the caller's order."""
        already = self.done_platforms(job_id)
        return [p for p in platforms if p not in already]

    def last(self, job_id: str, platform: str) -> Entry | None:
        match = [e for e in self.entries() if e.key == (job_id, platform)]
        return match[-1] if match else None

    # ------------------------------------------------------------------ writing
    def record(self, entry: Entry) -> Entry:
        if not entry.job_id:
            raise ValueError("a ledger entry needs a job_id")
        if not entry.platform:
            raise ValueError("a ledger entry needs a platform")
        if not entry.ts:
            entry.ts = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v for k, v in asdict(entry).items() if v not in ("", {}, None)}
        payload["ok"] = entry.ok
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return entry

    def record_ok(self, job_id: str, platform: str, **kw) -> Entry:
        return self.record(Entry(job_id=job_id, platform=platform, ok=True, **kw))

    def record_fail(self, job_id: str, platform: str, error: str, **kw) -> Entry:
        return self.record(Entry(job_id=job_id, platform=platform, ok=False, error=error[:500], **kw))


# ------------------------------------------------------------------ legacy import
def read_legacy(path: str | os.PathLike,
                job_id_for: Callable[[str], str | None] | None = None) -> list[Entry]:
    """Rows from one of the two old ledger formats, re-keyed onto job ids.

    Used by the Phase 6 cutover. Both old formats identified a video by path, so the
    caller supplies `job_id_for(path) -> str` — normally "read the sibling spec's id".
    Rows whose job id cannot be resolved are returned with an empty `job_id` so the
    caller can report them rather than silently dropping a published video, which would
    let it be published twice.
    """
    path = Path(path)
    out: list[Entry] = []
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        file_path = str(data.get("file") or "")
        job_id = ""
        if job_id_for and file_path:
            try:
                job_id = job_id_for(file_path) or ""
            except Exception:
                job_id = ""
        # The shell ledger recorded a comma list of platforms on one row; the batch
        # publisher recorded one platform per row. Fan the first out to match the second.
        platforms = data.get("platform") or data.get("platforms") or ""
        names = [p.strip() for p in str(platforms).split(",") if p.strip()] or [""]
        for name in names:
            out.append(Entry(
                job_id=job_id,
                platform=name,
                ok=bool(data.get("ok", False)),
                mode=str(data.get("mode") or ""),
                ts=str(data.get("ts") or ""),
                when=str(data.get("when") or ""),
                file=file_path,
                title=str(data.get("title") or ""),
                post_id=str(data.get("post_id") or data.get("id") or ""),
                schema=1,
            ))
    return out
