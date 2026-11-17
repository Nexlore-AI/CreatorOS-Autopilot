---
title: Version the spec format with `spec_version` and a pydantic schema
labels: help wanted
---

A spec is the contract every stage reads, and it carries no statement of which format it is
in. Add a `spec_version` field and a pydantic schema behind the existing validation, so that
a spec written for an older engine gets one sentence explaining that, instead of a list of
per-scene type errors.

This is bigger than an afternoon: it touches the pipeline's central data structure, adds the
project's fourth runtime dependency, and has to keep a Protocol that third-party plugins
implement working. Expect the review to be about the error messages more than the code.

## Why this matters

Render `examples/nexlore-ai/legacy/example_paper.json` today and you get:

```
creatoros: .../example_paper.json: spec problems:
  scene 2: unknown type 'card' (expected one of hook, stack, section, predict, points, stat, cta)
  scene 3: unknown type 'list' (expected one of hook, stack, section, predict, points, stat, cta)
  scene 4: unknown type 'flow' (expected one of hook, stack, section, predict, points, stat, cta)
```

Every word of that is true and none of it is the answer. The answer is "this is a spec for
the reels engine that was retired on 2026-11-16" — which is written down in
`examples/nexlore-ai/legacy/README.md` and which the tool cannot say, because a spec has no
way to declare its format. Scenes 1, 5 and 6 of that file validate fine, so the failure is
also partial and looks like three typos. The next time the scene vocabulary changes, every
spec anyone has saved produces the same confusing shape.

## Where to start

- `creatoros_autopilot/core/spec.py` — `load()`, `validate_render()` (line 74),
  `validate_publish()` (line 106), `SpecError` (line 30, a `ValueError` carrying
  `.problems`), and the hard-coded `SCENE_TYPES` / `VISUAL_TYPES` at lines 26–27. **Read the
  module docstring before anything else**: it explains why validation is split in two, and
  line 16 claims the pydantic schema and `spec_version` "land in Phase 2". They did not —
  Phase 2 shipped the plugin system instead (`docs/ROADMAP.md`). Correcting that sentence is
  part of this issue.
- `creatoros_autopilot/render/engine.py` — `validate()` at line 755 calls
  `spec_mod.validate_render()`. This is the call site in the render path; `cli/main.py`'s
  `cmd_render` loads the spec and the style separately and hands both to `style.render()`,
  and the engine validates again internally.
- `creatoros_autopilot/interfaces/style.py` lines 19–25 — the `Style` protocol declares
  `scene_types` and `visual_types`, and the docstring already says "`scene_types` and
  `visual_types` are what `core.spec` validates against". That is the intent, not yet the
  code. `creatoros_autopilot/providers/style/nexlore_v3.py` lines 24–25 fills them from
  `engine.SCENES` and `engine.VISUALS`.
- `tests/test_spec.py` — the contract to keep green, in particular
  `test_render_does_not_need_platform_metadata`,
  `test_a_missing_voice_line_is_a_warning_not_an_error` and
  `test_every_problem_is_reported_at_once`.
- `tests/test_render.py::TestShippedExamples` and
  `TestFailures::test_an_invalid_spec_is_refused_before_any_frame_is_drawn`.
- `examples/nexlore-ai/legacy/example_paper.json` plus `legacy/README.md` — the v1 test
  case. Note it differs at the top level too (`slot`, `segment`, `brand`, `accent`,
  `voice_lang`), not only in its scene types.
- `examples/specs/kv-cache.json` and `transformer-from-scratch.json` — current specs. Top
  level is `id`, `tag`, `music`, `scenes`, `platforms`, `sources`. Neither has a
  `spec_version`, and neither may start needing one.
- `creatoros_autopilot/agents/prompts/shorts_daily.md` — the agent writes specs by hand from
  this prompt. A field that becomes required has to be taught here, or every generated spec
  fails validation on the next daily run.
- `pyproject.toml` lines 34–35 — the comment above `dependencies` already names pydantic
  as the plan for exactly this: "the spec schema moves to pydantic in Phase 2".

## What done looks like

- [ ] A `spec_version` field, written by everything that writes a spec, and **absent means
      current** — not an error.
- [ ] pydantic models for a spec, a scene and a visual, with the scene and visual vocabulary
      supplied by the configured style rather than baked into the model.
- [ ] `validate_render()` and `validate_publish()` still exist as two separate functions,
      still raise `SpecError` with every problem listed at once, and still let a render
      proceed with no platform metadata.
- [ ] Rendering `examples/nexlore-ai/legacy/example_paper.json` prints one message naming it
      as a v1 spec, saying the v1 engine was retired, and pointing at
      `examples/nexlore-ai/legacy/README.md`.
- [ ] A spec whose `spec_version` is newer than this install is refused with a message that
      names both versions.
- [ ] pydantic justified in the PR description, with a version floor in `pyproject.toml`.
      `CONTRIBUTING.md` sends back a new dependency with no reason given, and there are
      three today.
- [ ] Tests: the v1 file gets the v1 message; a spec with no `spec_version` renders
      unchanged; a future version is refused; a style with a different vocabulary validates
      against its own scene types.
- [ ] The stale sentence in `core/spec.py`'s docstring and the `pyproject.toml` comment
      updated to describe what actually happened.

## Gotchas

**`core/` may not name a provider, and that includes the style.**
`tests/test_plugins.py::test_core_names_no_provider` reads the AST of every file in `core/`
and fails on an import or even a comparison. So the vocabulary has to arrive as an argument
— `validate_render(spec, path, scene_types=..., visual_types=...)`, or a small object the
caller builds — never by importing a style module.

**A pydantic `Literal` built from `spec.SCENE_TYPES` looks like the obvious design and is the
regression.** It freezes `nexlore-v3`'s vocabulary into core at class-definition time, where
no other style can reach it, and makes `Style.scene_types` decorative. Those two tuples
happen to equal `engine.SCENES` and `engine.VISUALS` today, which is the only reason nobody
has noticed that validation does not consult the style.

**Do not merge the two validators.** The split exists because the old single `validate()`
demanded `platforms.youtube.{title, description, tags}` before it would draw a frame, which
made "render an example with no keys and no accounts" impossible — and that is the first
thing a new user does. `test_render_does_not_need_platform_metadata` is the guard; keep it.

**Keep "no voice line" a warning.** `validate_render()` returns it instead of raising,
deliberately, so an example renders without a TTS key. A model with a required `voice: str`
reinstates the block without anyone noticing it was a decision.

**Never coerce, and do not forgive unknown keys.** Every serious bug in this project is an
error path that swallowed a failure and carried on: a shell fallback that turned "schedule
for tomorrow" into "publish all four now"; a TTS backend that could not pronounce a line,
exited 0, and shipped a near-silent video. The schema version of that trap is
`extra="ignore"` plus defaults — a v1 `list` scene quietly validated into something
renderable, or a dropped key, gives a video that renders and is wrong, which is far harder
to notice than a crash. Prefer `extra="forbid"`, and no default that can stand in for
missing content.

**`SpecError` must stay the exception the outside sees.** Tests assert
`pytest.raises(sp.SpecError)` and read `exc.value.problems`, and `cli/main.py`'s top-level
handler catches only `ValueError`, `FileNotFoundError` and `RuntimeError` to print one clear
line instead of a traceback — `SpecError` is a `ValueError`, which is why that works. Check
whether the pydantic version you add raises a `ValidationError` that subclasses
`ValueError`; either way, catch it at the boundary and re-raise as `SpecError` so an invalid
spec never reaches the user as a traceback.

**There are two ways into validation and only one goes through the CLI.** `engine.run()`
validates internally for both `render` and `preview`, and tests call the engine directly. If
the style's vocabulary is threaded only through `cli/main.py`, the check `creatoros render`
actually performs is still the hard-coded one.

**Changing `Style.render()` / `preview()` signatures breaks plugins outside this repo.** If
the vocabulary has to reach the engine, add an optional keyword whose default preserves
today's behaviour, and say so in `docs/plugins/add-a-style.md`.

**A version check that only looks at scene types will mislabel a v1 spec as current** when
its scenes happen to be renderable — `example_paper.json` would pass on scenes 1, 5 and 6.
Version detection has to look at the whole shape, or at the absence of fields the current
format requires.
