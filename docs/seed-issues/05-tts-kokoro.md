---
title: Add a Kokoro TTS backend (free, local, offline)
labels: help wanted, provider
---

Add a narration backend that speaks through Kokoro, a small open-weights TTS model that runs
on CPU. It would be the second free, offline voice in the project, and the free path is the
one that decides whether anyone gets as far as trying a key.

This is labelled `help wanted` rather than `good first issue` on purpose: the HTTP backends
are an afternoon, this one is not. Most of the work is deciding how the model and its voices
are obtained and loaded, and getting that decision wrong is invisible until a scheduled run
fails.

## Why this matters

`piper` and `say` are the only free backends, and `say` is macOS-only — so on Linux and
Windows, Piper is the entire free path. When Piper's phonemiser has no voice for the script
you are writing in, there is currently nothing else to try without a credit card. Kokoro is
small enough to run locally and good enough to put on a channel, which would make "install it
and get a finished video with no account" true for more than one voice.

## Where to start

- **Read `creatoros_autopilot/providers/tts/piper.py` before writing anything**, especially
  the docstring on `search_path()`, then `voice_dir()`, `models()`, `config_for()`, `_find()`
  and `_resolve()`. That module is the catalogue of every problem a local-model backend has,
  and each comment in it is a bug that happened.
- The protocol is `creatoros_autopilot/interfaces/tts.py` (`TTS`, `Voice`); `available()`
  returns an `Availability` from `creatoros_autopilot/interfaces/availability.py`
  (`Availability.yes` / `Availability.no(detail, fix)`), never a bool. Register the class
  under `[project.entry-points."creatoros.tts"]` in `pyproject.toml`. One file, one entry
  point, no change to `creatoros_autopilot/core/` —
  `tests/test_plugins.py::test_core_names_no_provider` enforces that.
- **Settle the distribution shape first, and say which you chose in the PR.** Check how
  Kokoro is actually distributed today before asserting anything: it may be a pip package
  that fetches its own weights, or ONNX weights plus a separate voices file that the user
  places on disk the way a Piper voice is placed. Those give you two different `available()`
  implementations — "is the package importable and are the weights present" versus "is there
  a model file on an absolute search path" — and two different dependency conversations. A
  new runtime dependency needs a reason in the PR description (`CONTRIBUTING.md`, "Things
  that will be sent back"), and pulling in torch is a much bigger ask than an ONNX runtime.
- `creatoros_autopilot/core/media.py` gives you `find()`, `require()` and `duration()`. If
  you need ffmpeg to get to mp3, report unavailable when it is missing rather than failing
  three scenes into a run — `Piper.available()` does exactly this and explains why.
- `tests/test_provider_piper.py` is the test file to copy. Note its `elsewhere` fixture: it
  relocates `HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `XDG_CONFIG_HOME` and
  `XDG_DATA_HOME` into `tmp_path` so a test cannot pass because of models already on the
  developer's machine.
- `creatoros_autopilot/providers/tts/say.py`'s docstring is worth reading too: it is the
  project's write-up of how a local voice lies about what it can pronounce.

## What done looks like

- [ ] One new module under `creatoros_autopilot/providers/tts/` with `name`, `paid = False`,
      `available()`, `synthesize()`, `voices()`, plus one line in `pyproject.toml`.
- [ ] `available()` distinguishes the separate problems — runtime not installed, weights not
      downloaded, no voice chosen — and each no carries a `fix` the user can paste.
      Collapsing them into one answer sends half the people who hit it to the wrong page.
- [ ] The model/voice search path is absolute, comes from
      `creatoros_autopilot/runtime/__init__.py` (`user_config_dir()`, `default_workspace()`),
      and `Path.cwd()` appears nowhere in it.
- [ ] A test that the same voice is found with the process started from two different
      directories — copy
      `tests/test_provider_piper.py::TestWhereVoicesLive::test_the_search_path_does_not_depend_on_the_working_directory`.
- [ ] Importing the module works with the package uninstalled and the weights absent
      (`tests/test_imports.py`), which means the heavy import lives inside the methods.
- [ ] The clip is written atomically (`.part` then `os.replace`), and `synthesize()` raises
      rather than returning anything it has not verified is real audio.
- [ ] A row in the README provider table and the install line a user needs.
- [ ] `pytest -q`, `ruff check .` and `mypy` clean, with no network.

## Gotchas

**The working-directory bug, which is the whole reason this issue names piper.py.** The Piper
voice directory used to default to `Path.cwd() / "piper-voices"`, which made "is a voice
installed?" a question about where the user was standing. It answered yes from the project
directory and no one level up — and `creatoros_autopilot/runtime/units.py` emits no working
directory for the unit it installs, so the daily run looked in `/piper-voices` under launchd
and `~/piper-voices` under a systemd user timer. It was right whenever a human checked by hand
and wrong every morning at 06:30, which is the run nobody watches and this product's primary
mode. Read the `search_path()` docstring and do the same thing: `user_config_dir()` first
because it is the one location that does not move, then `default_workspace()`, with a single
env override (`PIPER_VOICE_DIR` is the precedent). Not the working directory, and not the
workspace alone — the workspace is set in config and `creatoros schedule install` bakes it
into the unit, so voices kept there are found interactively and missing at 06:30.

**Exit 0 with nothing spoken is the failure mode of a local model.** See
`Piper.synthesize`'s empty-WAV check and `_wav_is_empty()`: Piper exits cleanly and writes a
valid WAV containing zero samples when its phonemiser produces no phonemes for the text. That
used to be returned as a finished clip — the manifest recorded it, the renderer fell back to
the scene's minimum duration, and the video shipped with the line silent, and every later run
reported "unchanged" so it was never retried. Find out what Kokoro does with text outside its
languages (a short array? silence? an exception?) and detect it. A size check is not enough:
a 44-byte header is non-empty, and ffmpeg turns zero samples into a frameless mp3 that is also
non-empty. `core/narrate.py::_implausible` is a backstop, not your excuse.

**Lazy imports.** A torch or onnxruntime import at module scope makes every `creatoros`
invocation pay for it and makes `tests/test_imports.py` fail on a machine without it. Import
inside `available()`/`synthesize()` and turn `ImportError` into an `Availability.no` carrying
the install command.

**`available()` must not download anything.** It is called by `creatoros doctor` and before
every narration. A check that fetches a few hundred megabytes the first time it is asked is
not a check — report "not downloaded" with the command that downloads it.

**Portability rules are enforced, not reviewed.** `tests/test_portability.py` fails on
`shell=True`, on OS branching outside `runtime/`, and on a literal path from someone's home
folder.

**Do not bundle weights.** Nothing in this repo ships a model file — `piper.py` says voice
models "are not bundled and cannot be". `.gitignore` now covers `*.onnx` and `*.onnx.json`
(lines 22–23), so an accidental commit is unlikely, but whatever extension Kokoro's weights
use may not be listed: add it. Check Kokoro's model licence and state it in the PR — this
project is AGPL-3.0-or-later, and the weights' terms are a separate question from the code's.
