---
title: "A reading-level gate: narration too dense to speak in the time the scene has"
labels: good first issue, provider
---

Add a gate that predicts, from the text alone, how long each scene's narration will take to
speak, and flags the scenes whose line cannot fit the layout it was written for. It is a
check that costs milliseconds and runs before a render that costs hours.

## Why this matters

Density is the failure a preview cannot show you. `core/spec.py:99` warns when a scene has
*no* voice line; `core/narrate.py::_implausible` (line 58) catches a clip that came back
implausibly *short* after synthesis. Nothing looks at a line that is too long until the
renderer has already stretched the scene around it — and a stretched scene is a static card
sitting on screen while the voice keeps talking, which is exactly the "it feels rushed and
also somehow slow" complaint that is impossible to debug after the fact.

The pipeline has a measured number for this, and it is currently written down in nobody's
code.

## Where to start

- `creatoros_autopilot/providers/gate/langmix.py` — **the shape to copy.** `name`,
  `advisory`, a constructor holding the thresholds, `check(spec, **options)` reading
  `spec.get("scenes")`, and a `GateResult` with `score`, `target`, `messages` and a
  per-scene `detail` dict. Note its early return at line 79 for a spec with no narration at
  all: a `GateResult(ok=True, messages=["no narration to measure"])` rather than a division
  by zero.
- `creatoros_autopilot/interfaces/gate.py` — the protocol, and the `advisory` flag
  (line 44).
- `creatoros_autopilot/core/gates.py` — `run()` (line 53) and `_build()` (line 39).
  `from_brand` is optional: the module docstring says a gate that needs no brand data does
  not have to know a brand exists, so skip it.
- `creatoros_autopilot/core/narrate.py` — how duration is checked *after* the fact.
  `MIN_SECONDS_PER_CHAR = 0.012` (line 27) and `_implausible()` (line 58), which rejects a
  clip under half the expected length and deletes it so it cannot be cached. Read its
  docstring: that 0.012 is a floor for "the voice cannot pronounce this script at all", not
  a speaking rate.
- `creatoros_autopilot/render/engine.py` — what you are predicting against.
  `LEAD, TAIL = 0.2, 0.45` (line 696) and `timeline()` (line 700):
  `duration = max(scene["min_seconds"] or SCENES[type][1], LEAD + clip + TAIL)`. The
  per-type minimums in `SCENES` (line 692) are `hook` 4.0, `stack` 4.2, `section` 7.0,
  `predict` 7.0, `points` 6.0, `stat` 5.0, `cta` 4.5.
- The constant worth reusing: for this pipeline's voice, clip seconds ≈
  **16.68 + 0.04092 × characters**. It was fitted on whole clips, so the part that applies
  per scene is the slope — 0.04092 s/char, about 24 characters a second. The 16.68 intercept
  is per-video fixed cost (lead and tail on every scene, plus the scenes that run at their
  minimum) and must not be added per scene.
- A worked example you can check before writing a line of code. Taking character counts from
  the two shipped specs and applying the slope plus 0.65 s of lead and tail:
  `examples/specs/transformer-from-scratch.json` scene 5 is a `section` with 200 characters
  of narration — about 8.2 s of voice, 8.83 s with lead and tail, against the 7.0 s a
  `section` layout is designed for. It is the only scene in either example that overruns its
  minimum; everything else in both specs comes in under (`kv-cache.json` runs
  66/119/125/77/117/59 characters against 4.0/7.0/7.0/5.0/6.0/4.5 s). That one scene is the
  signal this gate exists to produce.
- Re-fitting the rate for yourself is twenty lines: every job directory holds
  `<spec stem>_vo/NN.mp3` (`core/spec.py::vo_dir`, line 151) and `core/media.py::duration`
  (line 71) reads a length, so you can regress seconds on `len(line)` over your own output
  rather than trusting a number from a docs file.
- `tests/test_brand.py` (around line 180) shows a gate driven through `gates.run()`.

One file, one entry point line in `pyproject.toml` under
`[project.entry-points."creatoros.gate"]`, tests. An afternoon.

## What done looks like

- [ ] One module under `creatoros_autopilot/providers/gate/` with the
      `SPDX-License-Identifier: AGPL-3.0-or-later` header, one entry point line, nothing
      changed under `creatoros_autopilot/core/`
      (`tests/test_plugins.py::test_core_names_no_provider`).
- [ ] `advisory = True`.
- [ ] `check()` returns per-scene findings in `GateResult.detail` and an overall
      `score`/`target`, so the result reads as a number against a bar in
      `GateReport.lines()`.
- [ ] A message that names the scene, its characters, the predicted seconds and the minimum
      it overruns — e.g. "scene 5 (section): 200 chars ≈ 8.2 s of voice, 1.2 s past the
      7.0 s this layout is built for — cut a clause".
- [ ] The rate is a named constant with a comment saying what it was measured on and how to
      re-measure it.
- [ ] It runs with no audio on disk and no ffmpeg: this is a pre-render check, so no
      `media.duration`, no subprocess.
- [ ] A scene with no `voice` line is not a failure — `core/spec.py:99` already treats that
      as "runs at its minimum duration".
- [ ] Tests: a dense scene is flagged; both shipped example specs produce whatever result
      you decided is correct, with the test saying which and why; an empty spec returns a
      pass with a message rather than dividing by zero.
- [ ] `creatoros doctor` shows the gate as `advisory`.

## Gotchas

**Advisory by default, or nobody keeps it.** A hard gate on pacing is a gate people remove
from `gates:` the first morning it blocks a video they liked. `GateReport.ok`
(`core/gates.py:24`) and `GateReport.failures` (line 28) both skip advisory results, so you
get the warning in the report and the run continues. The `Gate` protocol docstring in
`interfaces/gate.py` makes the general version of this argument.

**Do not reuse `MIN_SECONDS_PER_CHAR`.** 0.012 s/char is roughly 3.4× looser than the
measured 0.04092, because its job is to be impossible for a real clip to trip — it exists to
catch a backend that exited 0 having spoken nothing, and a false positive there *deletes
audio*. Used as a pacing rate it would flag nothing. And do not tighten it in `narrate.py`
to make your gate's numbers agree: the looseness is deliberate and documented.

**Count characters, not words.** The narration is Devanagari, including the English
loanwords (`मॉडल`, `लॉस`, `स्टेप`) — the script is a TTS requirement, not a language choice.
A word-count regex written for one script silently returns zero on the other: the hook
scorer in `.claude/skills/yt-script/` has exactly that behaviour, and a Devanagari line
scores 25 out of 100 there no matter what it says. If you match anything with a regex,
assert it matched something and fail loudly when it did not, rather than reporting "0 words,
0.0 s" and passing. A gate that cannot read its input must say so, not approve.

**Where the number came from.** 0.04092 s/char and the 16.68 intercept were fitted from
this channel's own rendered clips. They are **not derived from code in this repository** —
nothing in the tree contains either figure — so treat them as a starting point to re-fit
against your own output, not as a constant of the pipeline.

**The rate belongs to one voice.** 0.04092 s/char was measured on this channel's Hindi
ElevenLabs voice. Piper, `say`, another language or another speaking style will differ.
Make it a constructor option, name the measurement in the message, and do not word it as a
fact about speech.

**Do not become a second source of truth on duration.** This gate predicts from text;
`core/narrate.py` measures the real clip after synthesis. Two checks that can disagree about
the same scene is worse than one that is honestly approximate, so keep the message phrased
as a prediction, and when a clip already exists on disk either say that the number is an
estimate of what was written or skip the scene and let `narrate` own it.

**The minimum you compare against belongs to a style, and nothing exposes it.** `SCENES` in
`render/engine.py` is `nexlore-v3`'s private table; the `Style` protocol publishes
`scene_types` but no minimums. Importing `engine` from a gate couples a text check to one
renderer and drags Pillow and NumPy into it as a side effect. Take the table as a
constructor option with the shipped values as the default, read `scene["min_seconds"]` when
the spec sets it (the engine does), and note in the PR that a style-supplied minimum would
need the protocol extended.

**Do not copy `langmix`'s scene numbering.** It builds `lines` from every scene's `voice`,
then filters out the empty ones, then enumerates from 1 (`langmix.py:75`–`90`) — so on a
spec where scene 2 has no narration, its "scene 2" is really scene 3, and every index after
it is wrong. Key your `detail` and your messages by the scene's own position in
`spec["scenes"]`, because the whole value of this gate is being able to say which scene to
cut.
