# Contributing

Thanks for looking. This is a small project with one maintainer, so the most useful thing
you can do is make your change easy to review.

## Getting set up

```bash
git clone https://github.com/Nexlore-AI/CreatorOS-Autopilot
cd CreatorOS-Autopilot
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pre-commit install
.venv/bin/creatoros doctor
```

You need Python 3.10+ and ffmpeg. Nothing else, and no API key — rendering is the part that
works offline, and it is deliberately the first thing you can do.

```bash
.venv/bin/pytest -q                  # ~2 minutes
.venv/bin/ruff check . && .venv/bin/mypy
```

## The shape of the project

```
core/        the job: spec, ledger, schedule, settings, brand, pillars
interfaces/  the six protocols a plugin implements
providers/   llm, tts, research, publish — every external service
render/      the renderer and its styles
cli/         the creatoros command
runtime/     the ONLY place that knows which OS it is on
```

Three rules hold this together, and each is enforced by a test rather than by review:

**`core/` names no provider.** Not in an import, not in a comparison. Adding a provider must
never mean editing core — `tests/test_plugins.py::test_core_names_no_provider` reads the AST
and fails if it does.

**OS-specific code lives in `runtime/`.** `tests/test_portability.py` reads every module and
fails on a `launchctl`, `osascript`, `schtasks` or `say` invocation outside it, on a path
from someone's home folder, and on `shell=True`.

**Secrets live in `.env` and nowhere else.** No `Settings` object holds a key, so nothing
that logs or dumps settings can leak one. A pre-commit hook rejects a config file that
contains something key-shaped.

## Adding a provider

This is the most common contribution and it should take an afternoon. One file, one entry
point, no change to `core/`:

```toml
[project.entry-points."creatoros.tts"]
my-voice = "my_package.voices:MyVoice"
```

See [docs/plugins/first-plugin.md](docs/plugins/first-plugin.md) for a worked example, and
[docs/plugins/add-a-style.md](docs/plugins/add-a-style.md) for a renderer.

Two things reviewers will look for:

- **`available()` returns an `Availability`, never a bool.** When it says no it must carry a
  `fix` the user can paste. Half the first-run experience of a tool like this is the gap
  between "it didn't work" and "it didn't work because `X_API_KEY` is unset, and here is
  where to put it".
- **Importing your module must work with the service absent.** No network call, no
  subprocess, no `os.environ[...]` at import time, or `creatoros --help` starts depending on
  the internet.

## What gets a change merged

**Fail loudly rather than continue with a wrong value.** Nearly every serious bug this
project has had is the same shape: an error path that swallowed a failure and carried on. A
shell fallback turned "schedule for tomorrow" into "publish all four now". A backend that
could not pronounce a script exited 0 and wrote a near-silent file, and the video shipped
with the line missing. A publisher that posted nothing recorded the job as done. A silent
empty list, a default that masks a missing config, a success returned for work that did not
happen — those are the bugs, not the error messages.

**A bug fix comes with a test that fails without it.** If you cannot reproduce it in a test,
say so in the PR and explain why.

**Explain why, not what.** The code says what it does. Comments and commit messages are for
the reasoning a future reader cannot recover — what went wrong, what it cost, what you
considered and rejected. Look at `core/schedule.py` for the house style.

**Small commits.** A 400-line commit called "fixes" is a commit nobody can review.

## Licence and the CLA

This project is [AGPL-3.0-or-later](LICENSE). By contributing you agree to
[CLA.md](CLA.md), which lets the maintainer also offer a hosted version. You keep the
copyright in what you write. Add this to your first pull request:

```
I have read CLA.md and I agree to it. Signed: <your name>, @<your GitHub handle>
```

## Things that will be sent back

- A new third-party dependency without a reason in the PR description. There are four, and
  that is a feature.
- Reformatting code you did not otherwise change. `render/engine.py` especially — its
  output is verified pixel-for-pixel, and a reformat makes that diff unreadable.
- A provider that writes its own per-scene caching instead of using `core/narrate.py`.
- Anything that makes publishing happen by default. `draft` is the default on purpose.

## Where to start

[`docs/seed-issues/`](docs/seed-issues/) has scoped work with enough context to begin, and
issues labelled `good first issue` are the smallest of them.
