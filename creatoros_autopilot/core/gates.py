# SPDX-License-Identifier: AGPL-3.0-or-later
"""Running the quality gates a channel has chosen to enforce.

Gates encode a house style, which is why they are opt-in and configured rather than
compiled in. The language-mix gate in particular exists because narration in formal Hindi
reads wrong to the audience one specific channel is for — true, measured, and not a
universal law.

A gate that needs brand data gets it through `from_brand`. One that does not, does not
have to know a brand exists.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import plugins
from ..interfaces import GateResult


@dataclass
class GateReport:
    results: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results if not r.advisory)

    @property
    def failures(self) -> list:
        return [r for r in self.results if not r.ok and not r.advisory]

    def lines(self) -> list:
        out = []
        for result in self.results:
            out.append(("  " if result.ok else "!! ") + result.summary())
            out.extend(f"     {m}" for m in result.messages[:6])
        return out


def _build(name: str, kit):
    """Instantiate one gate, giving it brand data when it asks for it."""
    factory = plugins._discover(plugins.GATE).get(name)
    if factory is None:
        raise plugins.PluginError(
            f"unknown gate {name!r}. Installed: "
            f"{', '.join(plugins.names(plugins.GATE)) or '(none)'}")
    cls = factory.factory()
    from_brand = getattr(cls, "from_brand", None)
    if from_brand is not None and kit is not None:
        return from_brand(kit)
    return cls()


def run(spec: dict, names: list, kit=None) -> GateReport:
    """Check a spec against the named gates. A gate that does not apply is skipped."""
    report = GateReport()
    for name in names or []:
        gate = _build(name, kit)
        if gate is None:
            # The brand gives this gate nothing to check — a single-language channel and
            # a language-mix gate, for instance. Skipping is right; failing would punish
            # a configuration that is perfectly correct.
            continue
        result = gate.check(spec)
        result.advisory = getattr(gate, "advisory", False)
        report.results.append(result)
    return report
