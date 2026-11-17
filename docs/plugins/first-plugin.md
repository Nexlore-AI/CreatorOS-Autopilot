# Write your first plugin

A plugin is one file and one entry point. Nothing in `creatoros_autopilot/core/` changes —
there is a test that reads the AST of every file in `core/` and fails if it so much as names
a provider, so this is a guarantee rather than a convention.

We will add a TTS backend, because that is the most common contribution. The other five
kinds work the same way.

## The whole thing

```python
# my_creatoros_voices/espeak.py
# SPDX-License-Identifier: AGPL-3.0-or-later
"""eSpeak NG: a small, fast, very robotic local voice."""
import shutil
import subprocess
from pathlib import Path

from creatoros_autopilot.core import media
from creatoros_autopilot.interfaces import Availability, Voice


class ESpeak:
    name = "espeak"
    paid = False

    def available(self) -> Availability:
        found = shutil.which("espeak-ng")
        if not found:
            return Availability.no(
                "espeak-ng is not on PATH",
                "brew install espeak-ng   (or: apt install espeak-ng)",
            )
        return Availability.yes(found)

    def synthesize(self, text, out_path, *, voice="", model="", **options) -> Path:
        out_path = Path(out_path)
        wav = out_path.with_suffix(".wav")
        subprocess.run(
            ["espeak-ng", "-v", voice or "en", "-w", str(wav), "--", text],
            check=True, capture_output=True,
        )
        # Write atomically. core.narrate treats any existing non-empty file as done, so a
        # half-written clip would ship inside the video with no error anywhere.
        part = out_path.with_suffix(".part")
        subprocess.run([media.require("ffmpeg"), "-y", "-loglevel", "error",
                        "-i", str(wav), str(part)], check=True)
        part.replace(out_path)
        wav.unlink(missing_ok=True)
        return out_path

    def voices(self) -> list[Voice]:
        return []
```

```toml
# pyproject.toml of your package
[project.entry-points."creatoros.tts"]
espeak = "my_creatoros_voices.espeak:ESpeak"
```

```bash
pip install -e .
creatoros doctor          # espeak now appears under "tts plugins"
```

Set `tts: {backend: espeak}` in `creatoros.yaml` and it is in the pipeline.

## What you did not have to write

This is the part worth noticing. `core/narrate.py` already walks the scenes, hashes each
line so an unchanged one is not re-synthesised, deletes clips for scenes that were removed,
and rejects a clip too short to plausibly be the line it claims to be. Your backend gets all
of it by implementing one method.

That caching used to live inside the ElevenLabs module — the reason a re-render did not cost
money belonged to one vendor, and every other backend would have had to reimplement it or
quietly not have it.

## The two things reviewers check

**`available()` returns an `Availability`, never a bool.** When it says no it carries a `fix`
the user can paste. Half the first-run experience of a tool like this is the gap between "it
didn't work" and "it didn't work because `espeak-ng` is not installed, and here is the
command". `creatoros doctor` renders these; nothing else decides what a missing dependency
looks like.

**Importing your module must work with the service absent.** No network call, no subprocess,
no `os.environ[...]` at import time — or `creatoros --help` starts depending on the internet.
`tests/test_imports.py` imports every module with nothing installed and nothing running.

## The other five kinds

| Kind | Entry point group | The method that matters |
|---|---|---|
| TTS | `creatoros.tts` | `synthesize(text, out_path, *, voice, model)` |
| LLM | `creatoros.llm` | `complete(prompt)`, and `run_agent()` if `agentic` |
| Publisher | `creatoros.publish` | `publish(request) -> PublishResult` |
| Research | `creatoros.research` | `fetch(*, since, limit, query) -> list[Item]` |
| Style | `creatoros.style` | `render()` / `preview()` — see [add-a-style.md](add-a-style.md) |
| Gate | `creatoros.gate` | `check(spec) -> GateResult` |

Read the protocol in `creatoros_autopilot/interfaces/` before you start. Each one's docstring
explains why it has the shape it does, which is usually because of something that went wrong.

## Writing a publisher: the traps

Publishers are where the real difficulty is, and all of these are mistakes already made here:

- **Honour `request.dry_run`** by making zero state-changing calls. It is how a user checks
  what would happen.
- **Report per platform.** A multi-platform post can be created while one platform inside it
  fails. Collapsing that into one bool either hides a real failure or re-posts the platform
  that worked.
- **Set `records_publication = False`** if a success does not mean the video is on the
  platform. `local_folder` does. Otherwise a practice run marks the job done and the real
  publisher skips it forever.
- **Declare `requires(platform)`** — the spec keys your platform needs. Core holds no such
  table; that is why adding a platform does not mean editing core.
- **Never drop a disclosure silently.** If the API has no field for AI-generated narration,
  say so loudly rather than omitting it.

## Testing it

```python
from creatoros_autopilot import plugins

plugins.register("tts", "espeak", ESpeak)        # no install needed, for tests
assert plugins.load("tts", "espeak").available()
```

Run the suite. `tests/test_plugins.py` checks every installed plugin against its protocol,
so yours is covered the moment it is registered.
