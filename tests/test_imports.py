# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every module imports.

This test exists because three modules did not, and nothing noticed. Phase 1 moved
`publish_zernio.py` into the package but left `queue.py`, `promote.py` and
`promote_instagram.py` doing `import publish_zernio` — the exact sibling-import problem the
move was supposed to end. The suite was green, CI was green on six platforms, and three
modules had been broken since the move, because no test ever imported them.

A module nothing imports is a module nothing tests. This is the cheapest possible guard
against that whole class of bug, and it is why it runs over every file rather than a list
someone has to remember to extend.
"""
import importlib
import pkgutil

import pytest

import creatoros_autopilot

MODULES = sorted(
    m.name for m in pkgutil.walk_packages(
        creatoros_autopilot.__path__, prefix="creatoros_autopilot."
    )
)


def test_the_package_has_modules_to_check():
    assert len(MODULES) > 20, "walk_packages found almost nothing — the test is not working"


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    """Importing must work with no key, no binary and no service running.

    An import-time network call or key read is a bug in its own right: it makes
    `creatoros --help` depend on the internet.
    """
    importlib.import_module(name)
