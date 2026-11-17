---
title: "A second video style: plain typographic, no grid, no cards"
labels: help wanted, provider, enhancement
---

Write a second renderer: large type on a flat background, no drifting grid, no glass cards,
no gradient header bar. It would be the first style in this project that is not
`nexlore-v3`, which is the only way to find out whether `Style` is a real plugin point or
just a wrapper around one engine.

## Why this matters

`plugins.names("style")` returns exactly one name today, because `pyproject.toml` has
exactly one line under `[project.entry-points."creatoros.style"]`. Anyone who wants a different look
has to fork `creatoros_autopilot/render/engine.py`, which is ~800 lines of drawing code for
one channel's visual language. A plain typographic style is also the honest default for a
spec whose scenes carry no `visual`: today those scenes still get the full animated
treatment around them.

There is already one known gap in the interface (see Gotchas), and a second implementation
is how the rest get found.

## Where to start

Read, in this order:

- `docs/plugins/add-a-style.md` — the four things the shipped engine does that yours should.
- `creatoros_autopilot/interfaces/style.py` — the `Style` protocol: `name`, `scene_types`,
  `visual_types`, `width`, `height`, `available()`, and
  `render(spec, spec_path, out=None, brand=None)` / `preview(...)`. It is a
  `runtime_checkable` `Protocol`, so you subclass nothing.
- `creatoros_autopilot/providers/style/nexlore_v3.py` — **the closest existing example, and
  the file to copy the shape of.** `class NexloreV3` is 47 lines: `available()` calls
  `core.media.require_all()` and turns `media.FFmpegMissing` into
  `Availability.no(detail, media.INSTALL_HINT[...])`; `render()` and `preview()` delegate to
  `render.engine.run()`. It is 47 lines because 800 lines of engine sit behind it.
- `creatoros_autopilot/render/engine.py` — the reference implementation. Read it, do not
  edit it:
  - `SCENES` (line 692) maps a scene type to `(drawing function, minimum seconds)`:
    `hook` 4.0, `stack` 4.2, `section` 7.0, `predict` 7.0, `points` 6.0, `stat` 5.0,
    `cta` 4.5.
  - `LEAD, TAIL = 0.2, 0.45` (line 696) and `timeline()` (line 700) — the whole timing rule
    is five lines.
  - `run()` (line 760) — its docstring is the read-only-spec rule.
  - `mix_audio()` (line 735) — voice plus music ducked under it. It touches no drawing code
    and no scene type, so it is reusable as-is.
  - `apply_brand()` (line 105) and `F()` (line 140) — palette binding and font resolution
    with a fallback.
- `creatoros_autopilot/core/spec.py` — `vo_dir()` (line 151): the per-scene voice clips are
  `<spec stem>_vo/NN.mp3`, one-based.
- `creatoros_autopilot/core/brand.py` — `BrandKit`, `Palette`, `DEFAULT_PALETTE`,
  `DEFAULT_FONTS`. `creatoros_autopilot/core/media.py` — `require()`, `require_all()`,
  `duration()`, `INSTALL_HINT`.

Register it with one line in `pyproject.toml` beside `nexlore-v3`. To select it: there is
no `--style` flag and no `CREATOROS_STYLE` environment variable — `style: <name>` in
`creatoros.yaml` (`core/settings.py`, `Settings.style`) is the only switch, read by
`cli/main.py::_style_and_brand`.

**Size.** This is not an afternoon, which is why it is `help wanted`. Frame drawing, timing,
per-scene encode, concat and the audio mix are all yours. A reasonable first PR implements
`available()`, `preview()` and `render()` for a subset of the seven scene types and says in
the PR description which ones it does not draw.

## What done looks like

- [ ] One new module under `creatoros_autopilot/providers/style/`, carrying the
      `SPDX-License-Identifier: AGPL-3.0-or-later` header
      (`tests/test_portability.py::test_every_source_file_carries_its_licence`), plus one
      line under `[project.entry-points."creatoros.style"]`, and **no diff inside
      `creatoros_autopilot/core/`** (`tests/test_plugins.py::test_core_names_no_provider`).
- [ ] `available()` returns an `Availability`, never a bool, and when it says no the `fix`
      can be pasted. `cli/doctor.py::_plugin` calls it and prints `ready.fix`.
- [ ] Scene duration is `max(min_seconds, LEAD + clip seconds + TAIL)`, with the scene's own
      `min_seconds` honoured when the spec sets it, and the minimum used when the clip is
      absent.
- [ ] With `style: <yours>` in `creatoros.yaml`,
      `creatoros render examples/specs/kv-cache.json -o /tmp/out.mp4` writes
      `/tmp/out.mp4` and `/tmp/out.cover.jpg` and leaves `examples/specs/` byte-identical —
      mirror `tests/test_render.py::TestReadOnlySpec::test_nothing_is_written_beside_a_read_only_spec`.
- [ ] `preview()` writes one still per scene as a contact sheet and takes seconds.
- [ ] Name, palette, logo and fonts all come from the `brand` argument. Rendering with
      `brand: claro-es` (`examples/claro-es/brand.yaml`) produces that palette, and a kit
      with no logo renders a wordmark rather than raising.
- [ ] `render()` raises on a scene type the style does not implement, naming the type and
      listing `scene_types`.
- [ ] A test like `tests/test_render.py::TestKeylessRender` that renders
      `tests/fixtures/mini.json` with no API keys set.

## Gotchas

**Time scenes to the narration.** `max(min_seconds, LEAD + voice + TAIL)` with
`LEAD, TAIL = 0.2, 0.45`. A fixed duration either clips the voice or leaves dead air, and
both are obvious to a viewer. Read the clip length once per scene, not once per frame:
`engine.timeline()` probes `<spec>_vo/NN.mp3` once and falls back to the minimum when the
file is not there (`if os.path.exists(p) else base`). A missing clip is normal — the audio
step may not have run — so it must not raise and must not be treated as zero seconds.

**Write artifacts beside the OUTPUT, never beside the spec.** The spec is an input and may
be read-only: in a container, in `site-packages`, in a shared examples folder. Everything —
scene intermediates, the concat list, the cover frame — hangs off `out` when `out` is given.
This is not hypothetical: `render /app/examples/specs/x.json -o /workspace/out.mp4` died
creating `/app/examples/specs/x_scenes/`, a path the user never named. Reading the voice
clips from beside the spec is fine; writing there is not.

**Take a brand rather than hard-coding one.** No palette, channel name or font filename in
your module. A missing logo renders a wordmark; a missing font falls back with a printed
warning (`engine.F()`). A moved asset must produce a plainer video, never an exception that
loses the day's run.

**The two caching bugs in `add-a-style.md` are real, and both cost days.** If you cache
anything derived from the brand, clear it **only when the brand actually changes** —
`apply_brand()` compares `_identity(kit)` and clears `G`/`_fc` only on a change, because
clearing unconditionally rebuilt a 2160×3840 float array once per scene instead of once per
worker and took the test suite from two minutes to over nine. And if a cached object defines
`__getattr__`, make it read `self.__dict__` directly: `core/brand.py::Palette.__getattr__`
does, because a spawned render worker unpickles an instance with an empty `__dict__`, so a
lookup through `self.colours` misses, calls `__getattr__` again and recurses forever. The
only symptom is a job that never finishes. Related: a spawned worker re-imports the module
and starts on the default brand, so re-apply the brand inside the worker — `engine._render`
does, and only Linux would have inherited the parent's state.

**Never draw a blank frame and carry on.** This project's whole bug family is an error path
that swallowed a failure: a TTS backend that could not pronounce a line exited 0 and shipped
a near-silent clip. The renderer's version is an unknown scene type, a font that did not
load, or a `visual` your style does not know, drawn as empty space with exit 0 — and the
video ships. Check the per-scene encoder's return code too: `engine._render` raises
`SystemExit("ffmpeg failed on scene N")`, and a `Popen` whose `stdin` broke mid-write and
whose `returncode` nobody reads leaves a short, valid, silent MP4 behind.

**The vocabulary gap to know before you design new scene types.** `interfaces/style.py` says
`scene_types` and `visual_types` are what `core.spec` validates against, but
`core/spec.py:87` still compares against its own module constants `SCENE_TYPES` and
`VISUAL_TYPES` (lines 26–27), and nothing passes a style's tuples in — `style.scene_types`
has no reader outside `nexlore_v3.py`. So a spec using a scene type only your style knows is
rejected by `validate_render` before a frame is drawn. Either stay inside the existing seven
types for this PR, or raise the plumbing question first: threading a style's vocabulary into
`validate_render` is a change to `core/`, and it still may not name a provider.

**Two smaller ones.** Keep text out of the bottom sixth of the frame, where the platform UI
sits. And if you bundle a new typeface, add it to `[tool.setuptools.package-data]` in
`pyproject.toml` or your style works from a clone and renders nothing from a `pip install` —
`tests/test_render.py::TestPackageData` checks exactly that for the bundled fonts.
