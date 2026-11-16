# SPDX-License-Identifier: AGPL-3.0-or-later
"""The plugin registry: how a name in `creatoros.yaml` becomes an object.

Six groups, one per interface. A plugin is a Python entry point:

    [project.entry-points."creatoros.tts"]
    piper = "my_package.piper:Piper"

and that is the entire contract. The factory is a class or a zero-argument callable
returning something that satisfies the matching protocol.

**Every provider CreatorOS ships is registered the same way** — see `pyproject.toml`.
There is no built-in table that gets consulted first, no `if name == "elevenlabs"` branch
anywhere in `core/`. That is deliberate: a mechanism only its authors use is a mechanism
that quietly stops working. If our own providers load through the public path, a
third-party plugin cannot be a second-class citizen.

Discovery is lazy and cached. `importlib.metadata.entry_points()` walks the whole
installed environment, which is slow enough to notice in a CLI that runs `doctor` and
then renders.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any, Callable

TTS, LLM, PUBLISH, RESEARCH, STYLE, GATE = "tts", "llm", "publish", "research", "style", "gate"
GROUPS = {
    TTS: "creatoros.tts",
    LLM: "creatoros.llm",
    PUBLISH: "creatoros.publish",
    RESEARCH: "creatoros.research",
    STYLE: "creatoros.style",
    GATE: "creatoros.gate",
}

# Registered at runtime rather than through entry points. Only tests and embedding
# applications should use this; a real plugin declares an entry point so that
# `creatoros doctor` can see it without the plugin being imported first.
_manual: dict[str, dict[str, Callable[[], Any]]] = {kind: {} for kind in GROUPS}
_cache: dict[str, dict[str, Any]] = {}


class PluginError(RuntimeError):
    """A plugin that does not exist, or that failed to load."""


@dataclass(frozen=True)
class Found:
    kind: str
    name: str
    factory: Callable[[], Any]
    source: str          # the entry point's value, or "<registered>"


def _discover(kind: str) -> dict[str, Found]:
    if kind not in GROUPS:
        raise PluginError(f"unknown plugin kind {kind!r}; expected one of {sorted(GROUPS)}")
    if kind in _cache:
        found = dict(_cache[kind])
    else:
        found = {}
        for point in entry_points(group=GROUPS[kind]):
            found[point.name] = Found(kind, point.name, point.load, point.value)
        _cache[kind] = dict(found)
    for name, factory in _manual[kind].items():
        found[name] = Found(kind, name, lambda f=factory: f, "<registered>")
    return found


def names(kind: str) -> list[str]:
    """Every plugin of this kind that is installed, whether or not it can run."""
    return sorted(_discover(kind))


def describe(kind: str) -> list[Found]:
    return [_discover(kind)[name] for name in names(kind)]


def load(kind: str, name: str) -> Any:
    """Instantiate one plugin by name.

    The error names what *is* installed, because "unknown tts backend 'pipr'" without the
    list of real ones is a worse message than it needs to be.
    """
    name = (name or "").strip()
    if not name:
        raise PluginError(f"no {kind} backend configured")
    found = _discover(kind)
    if name not in found:
        installed = ", ".join(found) or "(none)"
        raise PluginError(
            f"unknown {kind} backend {name!r}. Installed: {installed}. "
            f"A plugin registers itself with a '{GROUPS[kind]}' entry point."
        )
    try:
        factory = found[name].factory()
    except Exception as exc:
        raise PluginError(f"{kind} backend {name!r} failed to load: {exc}") from exc
    try:
        return factory() if callable(factory) else factory
    except Exception as exc:
        raise PluginError(f"{kind} backend {name!r} failed to initialise: {exc}") from exc


def load_all(kind: str, names_: list[str] | tuple[str, ...]) -> list[Any]:
    return [load(kind, name) for name in names_]


def register(kind: str, name: str, factory: Callable[[], Any]) -> None:
    """Add a plugin for this process only. For tests and embedders."""
    if kind not in GROUPS:
        raise PluginError(f"unknown plugin kind {kind!r}")
    _manual[kind][name] = factory


def unregister(kind: str, name: str) -> None:
    _manual.get(kind, {}).pop(name, None)


def reset_cache() -> None:
    """Forget what was discovered. Needed after installing a plugin in-process."""
    _cache.clear()


#: A publisher that declares this reaches any platform. `local_folder` uses it: writing a
#: video and the metadata that *would* have been sent is meaningful for every destination,
#: which is what makes it usable as a dry practice run for a real one.
ANY_PLATFORM = "*"


def publisher_for(platform: str, preferred: list[str] | tuple[str, ...] = ()) -> Any:
    """The publisher that will handle `platform`.

    Three rules, in order:

    1. **What the user asked for wins.** Naming `local_folder` in `publish.backends` sends
       everything there, even though it is a wildcard backend, because that is what a dry
       practice run means. Getting this wrong is not academic: a config that said
       `backends: [local_folder]` was silently routed to the live YouTube API instead.
    2. **An exact platform match beats a wildcard**, so with no preference set, YouTube
       goes to the YouTube backend rather than being written to a folder.
    3. **A backend that needs no third-party account wins the tie**, so a new user is not
       routed through a paid intermediary by default.
    """
    preferred = list(preferred)
    candidates = []
    for name in preferred + [n for n in names(PUBLISH) if n not in preferred]:
        try:
            backend = load(PUBLISH, name)
        except PluginError:
            continue
        reaches = tuple(getattr(backend, "platforms", ()))
        exact = platform in reaches
        wildcard = ANY_PLATFORM in reaches
        if not (exact or wildcard):
            continue
        candidates.append((
            name not in preferred,                              # asked for, first
            not exact,                                          # exact before wildcard
            bool(getattr(backend, "third_party", True)),        # direct before relay
            backend,
        ))
    if not candidates:
        raise PluginError(
            f"no publisher can reach {platform!r}. Installed publishers: "
            + (", ".join(names(PUBLISH)) or "(none)")
        )
    candidates.sort(key=lambda c: c[:3])
    return candidates[0][3]
