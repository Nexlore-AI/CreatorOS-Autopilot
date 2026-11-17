---
title: Add an OpenAI TTS backend (creatoros.tts)
labels: good first issue, provider
---

Add a narration backend that speaks through OpenAI's `/v1/audio/speech`. It is one new file
in `creatoros_autopilot/providers/tts/`, one line in `pyproject.toml`, and no change to
`creatoros_autopilot/core/` — the endpoint returns mp3 bytes, which is exactly what the
narrator wants on disk. An afternoon's work if you copy the right file.

## Why this matters

ElevenLabs is currently the only key-based voice. Someone who already pays OpenAI has to
open an account with a second vendor or drop to `piper`/`say`, and on a machine where
Piper's phonemiser cannot read the script there is no paid alternative to compare against.
It is also the cheapest way to prove the plugin claim in the README is real: a second HTTP
vendor added without touching core.

## Where to start

- **Copy `creatoros_autopilot/providers/tts/elevenlabs.py`.** It is the closest existing
  example and already the right shape: `class ElevenLabs` with `name`, `paid = True`, a
  `key` property that reads `self.key_env` lazily, `_has_key()`, `available()`,
  `synthesize()`, `voices()`. Everything it does, yours needs.
- The protocol is `creatoros_autopilot/interfaces/tts.py`: the `TTS` Protocol and the
  `Voice` dataclass (`id`, `name`, `languages`, `gender`, `detail`). The method signature is
  `synthesize(self, text, out_path, *, voice="", model="", **options) -> Path`. You do not
  subclass anything — it is a Protocol.
- `available()` returns an `Availability` from
  `creatoros_autopilot/interfaces/availability.py`. Use `Availability.yes(detail)` and
  `Availability.no(detail, fix)`; never a bool.
- Register it under `[project.entry-points."creatoros.tts"]` in `pyproject.toml`, beside the
  existing `elevenlabs`, `piper` and `say` lines.
- **Prior art you may read but must not import:**
  `creatoros_autopilot/legacy/reel_engine.py` (around line 436) already POSTs to
  `https://api.openai.com/v1/audio/speech` with
  `{"model": ..., "voice": ..., "input": text, "response_format": "mp3"}`, taking the model
  from `OPENAI_TTS_MODEL` (default `gpt-4o-mini-tts`) and the voice from `OPENAI_TTS_VOICE`
  (default `alloy`). `creatoros_autopilot/legacy/codeswitch_bakeoff.py::render_openai` adds
  an `instructions` field. `legacy/` is quarantined and
  `tests/test_portability.py::test_nothing_live_imports_legacy` fails on any import from it,
  so read those, then retype what you need.
- The voices are a fixed set — `alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer` — so
  `voices()` can return a module-level list of `Voice` objects. Check whether that list has
  grown, and whether the API has gained a way to enumerate it, before hard-coding it.
- `tests/test_provider_say.py` and `tests/test_provider_piper.py` show the house style for a
  provider test: a fake that fails the way the real thing fails, not a stub that says yes.

## What done looks like

- [ ] One new module under `creatoros_autopilot/providers/tts/` with a class exposing
      `name`, `paid = True`, `available()`, `synthesize()` and `voices()`.
- [ ] One line in `pyproject.toml` under `[project.entry-points."creatoros.tts"]`; after
      `pip install -e .` the backend appears in `creatoros doctor` under `tts plugins`.
- [ ] Nothing under `creatoros_autopilot/core/` is changed —
      `tests/test_plugins.py::test_core_names_no_provider` reads the AST and fails if core
      so much as names a provider.
- [ ] With no key set, `available()` returns a no whose `detail` names `OPENAI_API_KEY` and
      whose `fix` is a line the user can paste, and it does not raise.
      `tests/test_plugins.py` asserts every no carries both.
- [ ] `synthesize()` writes to a `.part` file and `os.replace`s it into place; the `.part` is
      removed in a `finally`.
- [ ] `creatoros audio <spec>` narrates a spec end to end with
      `tts: {backend: <yours>, voice_id: nova}`.
- [ ] A test that a failed request leaves the previous clip untouched — model it on
      `tests/test_provider_piper.py::TestSynthesize::test_a_failed_run_leaves_the_previous_clip_alone`.
- [ ] `pytest -q`, `ruff check .` and `mypy` clean, with no network and no key in the
      environment.

## Gotchas

**Do not write caching.** `creatoros_autopilot/core/narrate.py` already hashes each line
(`_fingerprint`, which includes the backend name so switching voices re-voices everything),
skips unchanged scenes, deletes clips for scenes that were removed, writes the manifest and
runs a duration plausibility check (`_implausible`, `MIN_SECONDS_PER_CHAR`). A provider that
adds its own per-scene cache is listed in `CONTRIBUTING.md` under "Things that will be sent
back". You implement `synthesize()` and inherit all of it.

**Write atomically — this is the trap that actually ships broken videos.** `narrate` treats
an existing non-empty file whose hash matches as done. Stream the response straight into
`NN.mp3`, lose the connection at 80%, and you get a truncated clip, a manifest entry
recording it as correct, a render that uses it, and "unchanged" from every run after that: it
is never retried and nothing anywhere logs an error. Copy the `.part` + `os.replace` +
`finally: part.unlink()` sequence from `ElevenLabs.synthesize` exactly.

**HTTP 200 is not audio.** Check the body is non-empty before promoting the file, and raise
on an error status with the response body quoted in the message, as `ElevenLabsError` does.
An error JSON written into `NN.mp3` is a non-empty file — the duration check in
`_implausible` catches some of those, not all.

**Nothing at import time.** No `os.environ["OPENAI_API_KEY"]` at module level, no client
object built on import, no network. Read the key in a property, like `ElevenLabs.key`.
`tests/test_imports.py` imports every module in the package with nothing installed and
nothing running.

**`paid = True` is load-bearing.** `creatoros doctor` warns on a paid backend and the test
suite refuses to call one; `tests/test_render.py::TestKeylessRender` deletes
`OPENAI_API_KEY` from the environment, so your tests must pass without it.

**Use `urllib.request`.** There are three runtime dependencies (pillow, numpy, pyyaml) and a
new one needs a reason in the PR description. Do not add the `openai` SDK or `requests`.

**Never put the key in `creatoros.yaml` or a fixture.** `scripts/check_no_secrets.py` runs as
a pre-commit hook and rejects an `sk-`-shaped value in a config file.

**Check before you expose parameters.** `instructions`, `speed` and `response_format` are
plausible options to pass through `**options`, but check which of them `gpt-4o-mini-tts` and
`tts-1` actually accept against the current API reference rather than copying the legacy
module. An option the API ignores is a setting that silently does nothing, which is the same
class of bug as everything else on this list.
