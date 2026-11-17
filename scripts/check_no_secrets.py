#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Refuse a config file that contains something shaped like a credential.

`creatoros.yaml` is settings and `.env` is secrets, and the split only holds if something
enforces it. This is the cheapest enforcement there is: a commit hook that reads the YAML
a contributor is about to commit and fails if a value looks like a key.

It errs towards false positives on purpose. A hook that wrongly blocks a commit costs a
minute; one that misses a key committed to a public repository costs a key rotation and
whatever happened in between.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Names that should only ever appear in .env.
SECRET_NAMES = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd|credential|refresh[_-]?token|"
    r"client[_-]?secret|access[_-]?token)\b")

# Shapes of real credentials, so an obviously-a-key value is caught under any name.
SECRET_SHAPES = [
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "OpenAI-style key"),
    (re.compile(r"\bsk_[a-z]+_[A-Za-z0-9]{16,}"), "secret key"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), "Google API key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "GitHub token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
    (re.compile(r"\bEAA[A-Za-z0-9]{40,}"), "Meta access token"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
]

PLACEHOLDERS = {"", '""', "''", "...", "<your key>", "changeme", "xxx", "null", "~"}


def check(path: Path) -> list[str]:
    problems = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for pattern, what in SECRET_SHAPES:
            if pattern.search(line):
                problems.append(f"{path}:{number}: looks like a {what}")
        key, sep, value = stripped.partition(":")
        if sep and SECRET_NAMES.search(key) and value.strip().strip("\"'") not in PLACEHOLDERS:
            problems.append(
                f"{path}:{number}: {key.strip()!r} has a value — secrets belong in .env")
    return problems


def main(argv: list[str]) -> int:
    problems = []
    for name in argv:
        path = Path(name)
        if path.is_file():
            problems += check(path)
    if problems:
        print("Secrets do not belong in a config file:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print("\nPut the value in .env (gitignored) and reference it by name.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
