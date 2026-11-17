---
title: creatoros voices — list, install and verify Piper voices
labels: help wanted
---

Add a `creatoros voices` subcommand that shows which Piper voice models are installed, fetches
one into the directory the scheduled run will actually look in, and verifies it before
reporting success. Today the tool hands the user a `curl` line and a ~60 MB download to place
by hand.

Labelled `help wanted` rather than `good first issue`: the listing half is small, but doing
the download correctly — atomically, verified, with a real exit code — is the part that
matters and it is more than an afternoon.

## Why this matters

This is the first-run cliff. `piper` is the free narration path, and the free path is what
someone tries before deciding whether the project is worth setting up a key for. What they
currently get is `piper._download_fix()`: a correct but unfriendly `curl` invocation with two
`-o` flags, pointed at a directory whose name contains a space on macOS, for a voice that is
*two* files. If either file is missing or truncated, Piper's own error names neither the file
nor where to get it — which is why that pairing is checked in `piper.py` rather than left to
Piper. And the full voice list is dozens of languages deep in the Hugging Face repo, with
nothing in the tool to help you find the one for the language you are writing in.

## Where to start

- `creatoros_autopilot/cli/main.py`: `build_parser()` adds every subcommand with
  `sub.add_parser(...)` followed by `set_defaults(func=cmd_...)`. **`cmd_brands(args)` is the
  closest existing command to copy** — it lists what is available, marks the configured one
  and prints one line each. Keep heavy imports inside the function body, as every `cmd_*`
  there does.
- `creatoros_autopilot/providers/tts/piper.py` already knows the layout:
  - `search_path()` — every directory a model is looked for in, and the docstring explaining
    why it is not the working directory.
  - `voice_dir()` — the first directory on that path, i.e. where a download should go.
  - `models(models_dir=None)` — every `.onnx` found recursively, paired with its config or
    `None`.
  - `config_for(onnx)` — a model's config, accepting both `<name>.onnx.json` and
    `<name>.json`.
  - `VOICES_URL`, `EXAMPLE`, `EXAMPLE_URL`, `DEFAULT_VOICE_DIR`, `_download_fix()`,
    `_quoted()`, `_legacy_hint()`, `PiperError`.
  - `Piper.voices()` already returns `Voice` objects carrying the language code, sample rate
    and speaker count read out of the config, so `creatoros voices` with no arguments is
    largely that list plus "can piper and ffmpeg actually be found".
  - `Piper.available()` is the model for what to print when something is missing: one
    specific no per problem, each with its own fix.
- A voice is two files: `<name>.onnx` and `<name>.onnx.json`. Both, or neither.
- The CLI's error convention: `main()` catches `ValueError`, `FileNotFoundError` and
  `RuntimeError` and prints one line rather than a traceback, and returns 130 on
  `KeyboardInterrupt`. `PiperError` subclasses `RuntimeError`, so raising it reads correctly.
- Tests: `tests/test_cli.py` (its `workspace` fixture, then `main([...])` and assertions on
  the exit code and captured stdout) and the `elsewhere` fixture in
  `tests/test_provider_piper.py`, which redirects `HOME`, `APPDATA` and the `XDG_*` variables
  into `tmp_path` so a test cannot read the developer's real voices.

A surface worth proposing in a comment before you build all of it:

```
creatoros voices                      # installed: name, directory, language, usable?
creatoros voices --available          # what can be downloaded (optionally --language hi)
creatoros voices install en_US-lessac-medium
creatoros voices verify <name>        # speak one line, check the clip is plausible
```

The valuable half is listing installed voices plus `install <name>`. Agree the rest first.

## What done looks like

- [ ] `creatoros voices` lists installed models with their directory, language and whether the
      `.onnx.json` is beside them, and exits 0 with a useful message (not a traceback) when
      there are none.
- [ ] `creatoros voices install <name>` writes both files into `piper.voice_dir()`, creating
      the directory, and writes nowhere else.
- [ ] The install is atomic: both files land under temporary names, are checked, then are
      `os.replace`d into place. An interrupted or failed install leaves nothing behind that
      `piper.models()` would report as a model.
- [ ] After a successful install, `plugins.load("tts", "piper").available().ok` is true on a
      machine with `piper` and `ffmpeg` on `PATH`.
- [ ] Any failure exits non-zero and says which file and why.
- [ ] `voices` is added to the set asserted by
      `tests/test_cli.py::TestParser::test_every_documented_command_exists`, so the command
      cannot be dropped silently.
- [ ] A test for the truncated download: a source that ends early must leave no `.onnx` in
      the voice directory and must exit non-zero.
- [ ] No test touches the network — serve the fixture from a local path or monkeypatch the
      fetch.
- [ ] `piper._download_fix()` — the text a user sees today when no voice is installed — names
      `creatoros voices install <name>` first and keeps its `curl` line as the fallback, and
      the README mentions the command where it introduces `piper`. Note that two existing
      tests in `tests/test_provider_piper.py` assert the shape of that string —
      `test_the_download_line_survives_a_space_in_the_path` (it shell-splits the `curl` line
      and checks both `-o` arguments) and `test_the_download_line_names_an_absolute_directory`
      — so update them deliberately rather than deleting them.
- [ ] `pytest -q`, `ruff check .` and `mypy` clean.

## Gotchas

**Verify before promoting into place. A truncated model is worse than a missing one.** A
half-written `<name>.onnx` is found by `models()`, passes `available()`, and then fails in the
middle of a render with nothing but Piper's stderr to go on — it looks installed. Download to
a temporary name, check what you can (the byte count against the response's `Content-Length`;
that the config parses as JSON and contains the keys `piper.py` reads — `language.code`,
`audio.sample_rate`, `num_speakers`, via `_read_config()` and `_section()`), and only then
`os.replace`. Check whether the Hugging Face repo publishes a checksum you can verify against;
if it does not, do not word the output as though the file was verified.

**Install the pair or install nothing.** Piper cannot load a model without its config, and
`Piper.available()` has a distinct no for exactly that state. If the JSON fails after the
`.onnx` succeeded, delete the `.onnx` too — otherwise you have manufactured the one situation
whose error message is "piper is installed, but no model has its .onnx.json".

**Write to `piper.voice_dir()`, never `Path.cwd()`.** That function exists because the default
used to be the working directory: the voice was found when a human checked and missing when
the scheduler ran, since `runtime/units.py` sets no working directory for the unit it
installs. An installer that writes into `./piper-voices` produces a user who can see the model
and a 06:30 run that still has no voice, with `_legacy_hint()` as the only thing that saves
them.

**Exit non-zero when nothing happened.** This project's recurring bug, in CLI form, is the
command that reports success for work it did not do — a publisher that posted nothing and
recorded the job as done, a backend that spoke nothing and exited 0. If the download failed,
the file did not verify, or the directory was not writable: print what and why, and return 1.
Do not print "installed" and let the next command discover the truth.

**Do not shell out to `curl`.** `tests/test_portability.py::test_no_shell_true_subprocesses`
forbids `shell=True`, and `urllib.request` is already how every provider fetches, so this
needs no new dependency. Mind the redirect — the `huggingface.co/.../resolve/...` URL
redirects to a CDN; `urlopen` follows redirects, but check the final response's status and
length rather than assuming you got the file.

**Paths with spaces are not hypothetical.** The default directory sits under
`~/Library/Application Support` on macOS. `piper._quoted()` exists because an unquoted `curl`
line read two half-paths and dropped a 60 MB model into a file called `Application`. Anything
you print for a user to paste goes through quoting; anything handed to a subprocess is a list
element, never a joined string.

**A silent 60 MB download looks hung.** Print progress, and clean up the temporary files on
`KeyboardInterrupt` — `main()` already returns 130, but the partial file is yours to remove.
