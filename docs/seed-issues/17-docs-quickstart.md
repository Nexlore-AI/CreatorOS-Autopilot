---
title: Write QUICKSTART.md — from install to a first video in ten minutes
labels: good first issue, docs
---

Write the document that takes someone from nothing to a video they can watch, in order,
with no steps assumed. The README stops at a rendered MP4 and `docs/OPERATIONS.md` opens
with "Everything below assumes `creatoros doctor` passes" — the path between those two
sentences is not written down anywhere.

## Why this matters

The first ten minutes decide whether anyone comes back. Right now a new reader has to
assemble that path from three documents that each assume a different starting point: the
README's "Try it" block (three commands, render only), `CONTRIBUTING.md`'s "Getting set up"
(a clone and a venv, written for contributors), and `OPERATIONS.md` (day-to-day, after
everything already works). Nobody has walked the whole thing end to end and written down
what it actually prints.

## Where to start

Read these three first, because the quickstart has to agree with them and fill the gap
between them:

- `README.md`, "Try it — no keys, no accounts".
- `docs/OPERATIONS.md`, "Setup, once" and "One video at a time" — where your document hands
  off.
- `CONTRIBUTING.md`, "Getting set up" — the install path that works today.

Then the three commands you will be documenting:

- `creatoros_autopilot/cli/init_wizard.py` — `run()` and `interview()`. Every question has
  a default; `--yes` takes all of them; a key typed in goes to `.env` through
  `write_env()` and is never echoed. The wizard loads the config it just wrote before
  claiming success.
- `creatoros_autopilot/cli/doctor.py` — `Check`, `collect()` and `report()`. Checks are
  required or optional, and **only a required failure sets a non-zero exit code**.
- `creatoros_autopilot/cli/main.py` — `build_parser()` is the authoritative list of flags.
  Read it rather than copying command lines out of prose.
- `creatoros_autopilot/providers/publish/local_folder.py` — the keyless publisher, for the
  last step. Its docstring explains what it writes and why it exists.
- `creatoros_autopilot/providers/tts/piper.py` — the module docstring, if you include
  narration: free and offline, but a voice is a separate ~60 MB download of two files.

Measured on 2026-11-17 on an Apple-silicon Mac, ffmpeg from Homebrew, inside the repo's
venv, in an empty directory:

```
creatoros init --yes                                           0.4 s
creatoros doctor                                               0.1 s
creatoros render examples/specs/kv-cache.json -o first.mp4     18.4 s wall
```

That produced a 33.5-second 1080x1920 MP4 of 5.6 MB plus `first.cover.jpg`, with no key set
and nothing asked for. Re-time it on your own machine and name the machine — the render is
parallel across cores, so the number is hardware, not a promise.

## What done looks like

- [ ] `QUICKSTART.md` at the repo root, linked from the README near the "Try it" block.
- [ ] Every command in it has been run, in order, in an empty directory, by you, and any
      output you quote is what it actually printed.
- [ ] It covers: prerequisites (Python 3.10+, ffmpeg, with the per-OS install line),
      install, `creatoros init`, `creatoros doctor`, `creatoros render`, and then one free
      publish path with `local_folder` so the reader ends holding a video and the metadata
      that would have been posted.
- [ ] It says what costs money (nothing, until the reader chooses a paid provider) and what
      is not needed (no key, no account, no signup for the render path).
- [ ] It ends by pointing at `docs/OPERATIONS.md` for the daily run and
      `docs/plugins/first-plugin.md` for extending.
- [ ] "Ten minutes" is either true on the machine you tested or the number in the title
      changes.

## Gotchas

**`pip install creatoros-autopilot` does not work yet.** `pypi.org/pypi/creatoros-autopilot/json`
returned 404 on 2026-11-17: the distribution has not been published. Both `README.md` and
`docs/OPERATIONS.md` already open with that line, which is the mistake to not repeat a third
time. Check whether it is on PyPI on the day you write; if it is not, document the clone and
`pip install -e .` path from `CONTRIBUTING.md` and say plainly that the PyPI release is not
out.

**`examples/` is not in the package.** `pyproject.toml`'s `packages.find` includes only
`creatoros_autopilot*`, and no `examples/` path appears in the built distribution's file
list — so `creatoros render examples/specs/kv-cache.json` works from a clone and nowhere
else. `creatoros init --yes` prints that exact command as its suggested next step. If your
quickstart installs from a wheel, the reader has no spec: point them at the clone, or put a
minimal spec in the document.

**Do not copy `creatoros init youtube`.** The YouTube publisher's `available()` fix text
(`creatoros_autopilot/providers/publish/youtube.py:317`) says "run `creatoros init youtube`,
which does the Google consent once", and `creatoros doctor` prints it verbatim. `creatoros
init`'s only positional argument is a *directory* (`cli/main.py`, `init_p`), and the wizard
contains no OAuth flow. I ran `creatoros init youtube --yes`: it created a directory called
`youtube`, wrote a default `creatoros.yaml` inside it, and exited 0. Leave the YouTube OAuth
step out of the quickstart, send the reader to `local_folder` instead, and file the
misleading hint as its own issue.

**Doctor's `x` lines are not failures when the feature is optional.** On a clean directory
with narration and publishing unset it prints `all checks passed.` and exits 0; with a
config that names a backend whose binary or key is missing it prints the `x` lines and
`ready to render. N optional feature(s) not configured`, still exiting 0. A quickstart that
tells the reader to wait for "all checks passed" will be wrong for everyone who configured
piper but has not downloaded a voice yet.

**Promise only what you ran.** The README used to list seven TTS backends when exactly one
was wired up — it now says so against itself, and a test keeps the table honest. A
quickstart that names a step which does not work is worse than no quickstart, because the
reader cannot tell whether they broke it or it was never true.

**`creatoros publish VIDEO` writes nothing to the ledger.** Only `creatoros run` does
(`core/pipeline.py`). Run `creatoros publish` twice and you post twice. If your document
ends with a real publish rather than `local_folder`, say that in the same breath.

**`local_folder` sets `records_publication = False`, and that is the interesting part.** It
copies the video and writes a sibling JSON of what would have been sent to each platform,
and it is deliberately not recorded as a publication — a practice run that counted as the
real one would mean the real publisher skips that video for ever. Explain it in one line
rather than skipping it; it is the clearest example of how the tool treats "nothing
happened" and "done" as different things.
