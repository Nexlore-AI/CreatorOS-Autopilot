# SPDX-License-Identifier: AGPL-3.0-or-later
"""A single-run lock that works the same on every platform.

`os.mkdir` is atomic on macOS, Linux and Windows, which makes a directory the one lock
primitive that needs no `fcntl`, no `msvcrt` and no third-party dependency. The shell
version used the same trick; this adds the pieces it lacked — the owning pid is recorded,
and the staleness check does not depend on `find -mmin`.
"""
from __future__ import annotations

import os
import time
from pathlib import Path


class LockBusy(RuntimeError):
    """Another run holds the lock and it is not stale."""


class Lock:
    """Context manager around a lock directory.

    A lock older than `stale_after` seconds is assumed to belong to a run that was killed
    without unwinding — a render takes hours, so the default is deliberately generous.
    Raises `LockBusy` rather than exiting, so the caller decides whether a busy lock is an
    error (a manual run) or a normal no-op (a scheduled `--if-due` run).
    """

    def __init__(self, path: str | os.PathLike, stale_after: float = 3 * 3600):
        self.path = Path(path)
        self.stale_after = stale_after
        self.held = False

    def acquire(self) -> Lock:
        self._clear_if_stale()
        try:
            self.path.mkdir(parents=True)
        except FileExistsError:
            raise LockBusy(f"another run holds {self.path}") from None
        (self.path / "owner").write_text(f"{os.getpid()}\n{time.time()}\n")
        self.held = True
        return self

    def release(self) -> None:
        if not self.held:
            return
        for child in self.path.glob("*"):
            child.unlink(missing_ok=True)
        try:
            self.path.rmdir()
        except OSError:
            pass
        self.held = False

    def _clear_if_stale(self) -> None:
        try:
            age = time.time() - self.path.stat().st_mtime
        except FileNotFoundError:
            return
        if age > self.stale_after:
            for child in self.path.glob("*"):
                child.unlink(missing_ok=True)
            try:
                self.path.rmdir()
            except OSError:
                pass

    def __enter__(self) -> Lock:
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()
