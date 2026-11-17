# SPDX-License-Identifier: AGPL-3.0-or-later
"""The rules that keep "runs anywhere" true, enforced rather than documented.

Principle 1 says no OS-specific code outside `runtime/`, and no path from my home folder.
Those are easy to state and easy to break by accident six months from now, so they are
tests.

They read *code*, not prose. Several modules explain in their docstrings exactly which
macOS-only command they replaced — `core/schedule.py` quotes the BSD `date -j -v+Nd` line
it exists to kill — and a check that cannot tell an invocation from an explanation would
either fail on the explanation or push it out of the file where it is most useful. So
every file is reduced with `ast.unparse`, which drops comments and docstrings and keeps
every other string literal, and OS-specific binaries are looked for where they would
actually do something: inside a subprocess call.
"""
import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "creatoros_autopilot"
ALL_PY = sorted(PACKAGE.rglob("*.py"))
# `runtime/` is the one place OS-specific code is allowed. `legacy/` is quarantined, not
# portable, and imported by nothing — which is asserted below rather than assumed.
EXEMPT = {"runtime", "legacy"}
PORTABLE = [p for p in ALL_PY if not EXEMPT & set(p.relative_to(PACKAGE).parts)]
LIVE = [p for p in ALL_PY if "legacy" not in p.relative_to(PACKAGE).parts]

OS_SPECIFIC_BINARIES = {"osascript", "launchctl", "schtasks", "systemctl",
                        "notify-send", "powershell", "brew", "say", "date"}


def _ids(paths):
    return [str(p.relative_to(PACKAGE)) for p in paths]


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


from .conftest import code_only


def subprocess_literals(path: Path) -> set[str]:
    """Every string literal passed to a subprocess call — i.e. things we actually run."""
    found = set()
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = ""
        if isinstance(target, ast.Attribute):
            name = target.attr
            if isinstance(target.value, ast.Name):
                name = f"{target.value.id}.{name}"
        elif isinstance(target, ast.Name):
            name = target.id
        if not (name.startswith("subprocess.") or name in ("Popen", "os.system", "os.popen")):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                found.add(sub.value)
    return found


@pytest.mark.parametrize("path", PORTABLE, ids=_ids(PORTABLE))
def test_no_os_specific_command_is_run_outside_runtime(path):
    """launchd, osascript and friends are invoked in exactly one directory."""
    for literal in subprocess_literals(path):
        head = literal.split("/")[-1].split()[0] if literal else ""
        assert head not in OS_SPECIFIC_BINARIES, (
            f"{path.name} runs {head!r}; OS-specific commands belong in runtime/"
        )


@pytest.mark.parametrize("path", LIVE, ids=_ids(LIVE))
def test_no_bsd_date_anywhere(path):
    """A-4: `date -j -v+Nd` is BSD-only and failed silently. All date maths is Python."""
    literals = subprocess_literals(path)
    assert "date" not in {l.split()[0] for l in literals if l}, f"{path.name} shells out to date"
    assert not any(l.startswith("-v+") or l == "-j" for l in literals), f"{path.name} uses BSD date flags"


@pytest.mark.parametrize("path", ALL_PY, ids=_ids(ALL_PY))
def test_no_hard_coded_home_paths(path):
    """`$HOME/Developer/personal/NexloreAI/...` was baked into config and two scripts."""
    code = code_only(path)
    for needle in ("/Users/", "$HOME/", "C:\\Users", "Developer/personal", "NexloreAI/content"):
        assert needle not in code, f"{path.name} contains the literal {needle!r} in code"


@pytest.mark.parametrize("path", PORTABLE, ids=_ids(PORTABLE))
def test_only_runtime_branches_on_the_operating_system(path):
    code = code_only(path)
    for needle in ("sys.platform", "platform.system()", "os.uname"):
        assert needle not in code, f"{path.name} branches on the OS; that belongs in runtime/"


@pytest.mark.parametrize("path", ALL_PY, ids=_ids(ALL_PY))
def test_no_shell_true_subprocesses(path):
    """shell=True makes quoting a security question and behaviour OS-dependent."""
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.keyword) and node.arg == "shell":
            assert not (isinstance(node.value, ast.Constant) and node.value.value is True), \
                f"{path.name} uses shell=True"


@pytest.mark.parametrize("path", ALL_PY, ids=_ids(ALL_PY))
def test_every_source_file_carries_its_licence(path):
    assert "SPDX-License-Identifier: AGPL-3.0-or-later" in path.read_text(encoding="utf-8")


def test_third_party_licences_are_intact():
    """The YouTube skills are MIT (Jake Schincariol) and the fonts are OFL. Keeping those
    notices is a condition of redistributing them."""
    skills = PACKAGE.parent / ".claude" / "skills" / "YT_SKILLS_LICENSE"
    text = skills.read_text()
    assert "MIT License" in text and "Jake Schincariol" in text
    fonts = (PACKAGE / "render" / "fonts" / "README.md").read_text()
    assert "Open Font License" in fonts


def test_no_music_is_bundled():
    """Shipping audio we cannot licence would make the repo undistributable."""
    assert not list(PACKAGE.rglob("*.mp3"))


def test_no_third_party_file_was_given_our_licence_header():
    """The bundled YouTube skills are MIT and must not be relabelled AGPL."""
    for path in (PACKAGE.parent / ".claude" / "skills").rglob("*.py"):
        assert "AGPL" not in path.read_text(encoding="utf-8"), f"{path} was relicensed"


@pytest.mark.parametrize("path", LIVE, ids=_ids(LIVE))
def test_nothing_live_imports_legacy(path):
    """The quarantine only means something if it is one-way."""
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ImportFrom) and node.module and "legacy" in node.module:
            pytest.fail(f"{path.name} imports from legacy/")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "legacy" not in alias.name, f"{path.name} imports {alias.name}"
    # A relative `from ..legacy import x` has module="legacy" handled above; catch the
    # `from . import legacy` form too.
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ImportFrom):
            assert "legacy" not in {a.name for a in node.names}, f"{path.name} imports legacy"
