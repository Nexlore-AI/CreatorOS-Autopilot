# SPDX-License-Identifier: AGPL-3.0-or-later
"""The job payload: a video as a JSON spec.

A spec is the contract between every stage — research and scripting write one, TTS reads
its `voice` lines, the renderer draws its scenes, the publisher reads its per-platform
metadata. It is already the seam that makes this pipeline a job queue in all but name, so
it is validated in one place rather than in each stage.

Validation is split in two on purpose. Rendering needs scenes and narration; it does not
need a YouTube title. The old single `validate()` demanded `platforms.youtube.{title,
description,tags}` before it would draw a frame, which makes "render an example with no
API keys and no accounts" impossible — and that is the path a new user takes first. So
render-time problems are errors, publish-time metadata is a warning until you actually
publish, and then it is an error for the platforms you asked for.

The pydantic schema and `spec_version` land in Phase 2; this is the same rules the engine
already applied, moved somewhere testable.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

SCENE_TYPES = ("hook", "stack", "section", "predict", "points", "stat", "cta")
VISUAL_TYPES = ("vectors", "attention", "flow", "bars", "steps", "compare")


class SpecError(ValueError):
    """A spec that cannot be rendered or published, with every problem listed."""

    def __init__(self, problems: list[str], path: str | None = None):
        self.problems = problems
        where = f"{path}: " if path else ""
        super().__init__(where + "spec problems:\n  " + "\n  ".join(problems))


def load(path: str | os.PathLike) -> dict:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SpecError([f"no such spec: {path}"]) from None
    try:
        spec = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SpecError([f"not valid JSON ({exc})"], str(path)) from None
    if not isinstance(spec, dict):
        raise SpecError(["the top level of a spec must be an object"], str(path))
    return spec


def save(spec: dict, path: str | os.PathLike) -> None:
    """Write a spec back, preserving non-ASCII (the narration is Devanagari)."""
    Path(path).write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")


def job_id(spec: dict, path: str | os.PathLike | None = None) -> str:
    """The spec's stable identity, used as the ledger key.

    Prefers `spec["id"]`. Falls back to the file stem, because a spec written by hand
    often has no id and refusing to render it would be unhelpful — but a spec with no id
    cannot be deduplicated across a move, which is why `validate_publish` insists on one.
    """
    ident = str(spec.get("id") or "").strip()
    if ident:
        return ident
    if path:
        return Path(path).stem
    return ""


def validate_render(spec: dict, path: str | os.PathLike | None = None) -> list[str]:
    """Raise on anything that stops a render. Return non-fatal warnings."""
    problems, warnings = [], []
    scenes = spec.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise SpecError(["a spec needs a non-empty 'scenes' list"], str(path) if path else None)

    for i, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            problems.append(f"scene {i}: must be an object")
            continue
        kind = scene.get("type")
        if kind not in SCENE_TYPES:
            problems.append(f"scene {i}: unknown type {kind!r} (expected one of {', '.join(SCENE_TYPES)})")
        visual = scene.get("visual")
        if visual is not None:
            if not isinstance(visual, dict):
                problems.append(f"scene {i}: 'visual' must be an object")
            elif visual.get("type") not in VISUAL_TYPES:
                problems.append(f"scene {i}: unknown visual {visual.get('type')!r} "
                                f"(expected one of {', '.join(VISUAL_TYPES)})")
        if not (scene.get("voice") or "").strip():
            # Not fatal: a scene with no narration still renders, at its minimum duration.
            # It was fatal before, which blocked rendering an example without a TTS key.
            warnings.append(f"scene {i}: no 'voice' line, so it will run at its minimum duration")

    if problems:
        raise SpecError(problems, str(path) if path else None)
    return warnings


def validate_publish(spec: dict, platforms: list[str], path: str | os.PathLike | None = None,
                     requires=None) -> list[str]:
    """Raise on missing metadata for the platforms being published to.

    `requires(platform) -> tuple[str, ...]` comes from the publisher that will actually
    handle the platform. Core used to hold that table itself, with `if platform ==
    "youtube"` branches, which meant a new platform meant editing core. Without a
    `requires` the only check is that the spec can be identified in the ledger.
    """
    problems = []
    if not job_id(spec, path):
        problems.append("no 'id': a spec needs one to be recorded in the ledger")
    meta = spec.get("platforms") or {}
    if not isinstance(meta, dict):
        raise SpecError(["'platforms' must be an object"], str(path) if path else None)

    for platform in platforms:
        entry = meta.get(platform) or {}
        if not isinstance(entry, dict):
            problems.append(f"platforms.{platform} must be an object")
            continue
        needed = ()
        if requires is not None:
            try:
                needed = requires(platform) or ()
            except Exception:
                needed = ()
        for key in needed:
            if not str(entry.get(key) or "").strip() if isinstance(entry.get(key), str) \
                    else not entry.get(key):
                problems.append(f"platforms.{platform}.{key} is missing or empty")
    if problems:
        raise SpecError(problems, str(path) if path else None)
    return []


def slot_of(path: str | os.PathLike) -> int | None:
    """The slot number from a `0N_slug` filename, which is how a video finds its post time."""
    match = re.match(r"^(\d+)[_-]", Path(path).stem)
    return int(match.group(1)) if match else None


def vo_dir(spec_path: str | os.PathLike) -> Path:
    """Where this spec's per-scene voice clips live."""
    path = Path(spec_path)
    return path.with_suffix("").with_name(path.stem + "_vo")
