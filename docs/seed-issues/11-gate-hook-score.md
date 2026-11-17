---
title: A hook-score gate that wraps the existing scorer
labels: good first issue, provider
---

Wrap the hook scorer that already ships in this repo — `.claude/skills/yt-script/hookscore.py` —
as a `creatoros.gate` plugin, so a weak opening line is a reportable result from `creatoros`
rather than something only the scripting agent catches if it remembers to run a script.

## Why this matters

The daily agent prompt already depends on this number.
`creatoros_autopilot/agents/prompts/shorts_daily.md:41` tells the model to write five hook
options, score them with `hookscore.py`, and use a winner that scores 55+ (`WORKABLE`) —
"rewrite until one does". Nothing verifies that it did. The spec lands on disk, six hours of
rendering follow, and the opening line nobody checked is the first 1.5 seconds of the video.

`langmix` is the only gate that exists today, and it is what turned "the narration reads too
formal" from an opinion into a number with a floor. Hook strength is the other number this
channel actually uses.

## Where to start

- `creatoros_autopilot/interfaces/gate.py` — the whole protocol: `name`, `advisory`, and
  `check(spec, **options) -> GateResult`. `GateResult` carries `gate`, `ok`, `score`,
  `target`, `messages`, a per-scene `detail` dict, and the `advisory` flag the runner copies
  from the gate. Note there is no `available()` on this protocol, unlike `TTS` or
  `Publisher`, so `check()` is the only place a missing dependency can be reported.
- `creatoros_autopilot/providers/gate/langmix.py` — **the file to copy the shape of.**
  `class LanguageMix` has `name = "langmix"`, `advisory = False`, a constructor taking its
  thresholds, an optional `from_brand(kit)` classmethod that returns `None` to disable the
  gate, and a `check()` that reads the scenes and returns a `GateResult` with `score`,
  `target`, `messages` and `detail`.
- `creatoros_autopilot/core/gates.py` — the runner. `_build()` (line 39) calls `from_brand`
  only when the class has one; `run()` (line 53) copies the gate's `advisory` flag onto the
  result, and `GateReport.ok` (line 24) ignores advisory results entirely.
- `pyproject.toml`, `[project.entry-points."creatoros.gate"]` — one line beside `langmix`.
  Then `gates: [<name>]` in `creatoros.yaml` (`core/settings.py`, `Settings.gates`, line
  112), and `creatoros doctor` lists it as `advisory` or `blocking`
  (`cli/doctor.py::_gates`, line 225).
- The scorer itself: `.claude/skills/yt-script/hookscore.py`. `score(t)` returns
  `(parts, verdict, name, hits)`; `band(v)` is `STRONG` at 72+, `WORKABLE` at 55+, else
  `WEAK`; `FIX` maps each property to the one-line fix for it. As a CLI:
  `python3 hookscore.py --json --hook "<line>"` prints a JSON list with `properties`,
  `verdict`, `band` and `formula`.
- `.claude/skills/YT_SKILLS_LICENSE` — MIT, Jake Schincariol. Shell out to the script or
  import it by path; **do not copy the scoring code into the package**, and do not put an
  AGPL header on anything under `.claude/`
  (`tests/test_portability.py::test_no_third_party_file_was_given_our_licence_header`).
  `.claude` is also excluded from ruff in `pyproject.toml`, so do not reformat it either.
- Where the hook text lives: the spec's first scene is `{"type": "hook", "lines": [...],
  "kicker": ..., "big": ..., "big_suffix": ...}`, drawn in that order by
  `render/engine.py::sc_hook`. Its `voice` field is the Devanagari narration, not the hook
  text. See `examples/specs/kv-cache.json`.
- `tests/test_brand.py` (around line 180) shows a gate being driven through `gates.run()`.

This is an afternoon: one file, one entry point line, two or three tests.

## What done looks like

- [ ] One module under `creatoros_autopilot/providers/gate/` with the
      `SPDX-License-Identifier: AGPL-3.0-or-later` header, one entry point line, and no
      change under `creatoros_autopilot/core/`
      (`tests/test_plugins.py::test_core_names_no_provider`).
- [ ] `advisory = True`, and `check()` returns a `GateResult` whose `score` is the scorer's
      verdict, whose `target` is the threshold, and whose `messages` name the weakest
      property and the scorer's own fix line for it.
- [ ] The threshold is an option — a constructor argument plus `options.get(...)` in
      `check()`, the way `langmix` handles `low`/`high` — defaulting to 55 to match the
      agent prompt.
- [ ] Importing the module works with the scorer absent: resolve and run it inside
      `check()`, never at import time. `tests/test_imports.py` imports every module in the
      package with nothing installed and nothing on disk.
- [ ] If the scorer cannot be found, run, or parsed, `check()` returns `ok=False` with a
      message saying where it looked and how to point it elsewhere — **not** `ok=True`.
- [ ] The docstring says which text is scored and why that text and not another.
- [ ] Tests: a deliberately weak hook fails, a strong one passes, and a wrong scorer path
      is reported as a failure rather than a pass.
- [ ] `creatoros doctor` shows `gate <name>   advisory`.

## Gotchas

**The scorer exits 0 for a hook it calls WEAK.** Verified:
`python3 .claude/skills/yt-script/hookscore.py --json --hook "..."` returns exit status 0
for a verdict of 25. Only the `--json` payload's `verdict`/`band` carries the answer. A gate
that read the return code would pass every hook forever — which is this project's signature
bug in its purest form, and the reason `CONTRIBUTING.md` leads with it. Do not infer
otherwise from `creatoros_autopilot/quality/langmix.py`, whose docstring says it exits
nonzero "like `hookscore.py` and `title.py`": `langmix.main()` really does return 1 when the
ratio is out of band, and `hookscore.main()` never does.

**The scorer only sees ASCII.** Its `words()` is `re.findall(r"[a-z0-9'%$.]+", t.lower())`,
so Devanagari is invisible to it. Measured: the narration line
`मॉडल का लॉस slow हो जाता है तो step बढ़ाओ` scores 25 (`WEAK`) — it found two words, `slow`
and `step`, and `BREVITY` floors at 30. So do **not** score `scene["voice"]`. Score the
on-screen hook (`lines`, `kicker`, `big`, `big_suffix`), which is English in this pipeline,
and say so in the docstring.

**How you glue the fields changes the verdict, and the shipped specs do not clear 55.**
Measured against both examples, joined in on-screen order (`lines`, then `kicker`, then
`big` + `big_suffix`):

| spec | scored text | verdict |
|---|---|---|
| `kv-cache.json` | "Why does ChatGPT reply so fast? One trick: KV cache." | 37 WEAK |
| `transformer-from-scratch.json` | "Stop copying transformer code before you know these 4 pieces." | 60 WORKABLE |

Scoring `lines` alone gives 34 and 31 — both WEAK, because three fragments of under 17
characters each are nothing like the 9–24 spoken words `BREVITY` is calibrated for. And
joining in JSON field order instead (kicker first) produces the scrambled
"before you know these Stop copying transformer code 4 pieces." which still scores 60: a
nonsense concatenation outscoring the real reading order. Calibrate against these two specs
before you pick a default, and expect to justify the number; a threshold of 55 fails one of
the two specs the test suite renders.

**Make it opt-in and advisory.** This gate encodes one channel's taste — the `Gate` protocol
docstring in `interfaces/gate.py` says exactly that about `langmix`, and offers a
Spanish-language channel as the counter-example. Set `advisory = True`: `GateReport.ok`
(`core/gates.py:24`) excludes advisory results, so it reports without blocking the day's
run. A blocking default is a gate people delete from `gates:`, and a deleted gate measures
nothing.

**Locating the script portably.** `.claude/` is not package data —
`[tool.setuptools.package-data]` ships fonts, brand files and agent prompts only — so a
`pip install`ed CreatorOS has no `hookscore.py` anywhere. Do not hard-code a path from a
home directory: `tests/test_portability.py::test_no_hard_coded_home_paths` fails on
`/Users/`, `$HOME/`, `C:\Users` and `Developer/personal`. Take the path as a constructor
option (or read an environment variable inside `check()`), default to the clone layout, and
when it is missing fail loudly with the option name in the message.

**If you shell out:** `[sys.executable, str(script), "--json", "--hook", line]` with
`capture_output=True`. No `shell=True` —
`tests/test_portability.py::test_no_shell_true_subprocesses` fails on it, and quoting a hook
line through a shell is its own bug. Treat a nonzero return code or unparseable stdout as a
gate failure, not a pass. **If you import it instead:** use
`importlib.util.spec_from_file_location` inside `check()` and catch the import — the module
does `json.load(open(os.path.join(HERE, "hooks.json")))` at import time, so it fails on a
partial checkout, and you are running third-party code in-process. Either choice is fine;
say which you made in the PR.

**Nothing in the daily run calls `gates.run()` yet.** Gates are built, reported by
`creatoros doctor` and exercised in `tests/test_brand.py`, but
`creatoros_autopilot/core/pipeline.py` does not invoke them — the agent prompt runs the
standalone scripts instead. So do not expect your gate to start blocking anything on merge,
and do not fix that here: wiring the gate runner into the pipeline is a separate change with
its own argument about what a failure should do.
