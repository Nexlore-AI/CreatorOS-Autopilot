# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared test helpers."""
import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PACKAGE = REPO / "creatoros_autopilot"


def code_only(path) -> str:
    """A module's source with comments and docstrings removed, other strings intact.

    Needed because several modules document, in their docstrings, the exact thing they
    exist to prevent — `core/schedule.py` quotes the BSD `date -j` line it replaced, and
    `cli/doctor.py` names the `if backend == "elevenlabs"` branch it deleted. A check that
    cannot tell an invocation from an explanation either fails on the explanation or
    pushes it out of the file where it is most useful. Both have happened here.
    """
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body.pop(0)
            if not body:
                body.append(ast.Pass())
    return ast.unparse(ast.fix_missing_locations(tree))


@pytest.fixture
def repo():
    return REPO
