# CreatorOS Autopilot — Roadmap

**Status:** All seven phases complete (PRs #2–#8). The Phase 6 **cutover** needs Aman at
the keyboard and has not been performed; Phase 7 is a design document with no code. Phase 0's audit stands as written below; findings
it fixed are marked ✅. The four decisions in §6 were answered on
2026-11-17 and are recorded there. Phases 1–7 are proposed, not started.

This document is the plan for turning CreatorOS Autopilot from a pipeline that runs one
channel on one Mac into an open-source platform that anyone can run on their own machine
with their own keys — without the Nexlore AI channel missing a day.

It has three jobs: record what the code actually does today (§1), propose the shape it
should take (§2–§4), and define the work and how we know each phase is done (§5). The
decisions that were not mine to make are in §6, with the answers.

---

## 1. Where we are

### 1.1 The size of the thing

| | |
|---|---|
| Python | 3,771 lines across 17 files |
| Bash | 450 lines across 7 scripts |
| Third-party runtime dependencies | **two** — Pillow and NumPy (verified by AST scan of every `.py`) |
| External binaries | `ffmpeg`, `ffprobe`, `claude` |
| Config | `config.env` (shell), `platforms/*.json`, `pillars/*.json`, `.env` |
| Tests, CI, linting, packaging | none |
| Licence | **none** (see F-33) |

This is a small, legible codebase with an unusually clean dependency surface. That is an
asset and the plan protects it: the packaging work should not turn two dependencies into
twenty.

### 1.2 What works and must not regress

Anything below that breaks is a channel outage, so each becomes a test in Phase 4.

1. A spec JSON renders to a 1080×1920 MP4 with per-scene narration timing, music ducked
   under the voice, and a cover frame (`engine/shorts_engine.py`).
2. Scene duration is driven by the narration, not by a fixed value —
   `max(min_seconds, 0.2 + voice_length + 0.45)` (`shorts_engine.py:595-599`).
3. Voice clips are only regenerated when the line changed, via a SHA-1 manifest
   (`shorts/make_audio.py:43-44`). This is what keeps the TTS bill down on re-renders.
4. Publishing is idempotent: a video already marked `ok` is skipped on a re-run
   (`shorts/run_shorts.sh:87-88`, `publish_queue.py:83`).
5. Instagram posts are refused when the caption is empty (`publish_zernio.py:155-159`).
6. Transport failures retry with backoff; a broken pipe mid-upload is treated as retryable
   (`publish_zernio.py:24`, `:50-54`).
7. Videos render one day and publish on the next day's slots, so a six-hour render never
   races the 09:00 slot (`SHORTS_PUBLISH_OFFSET_DAYS=1`).
8. A lock directory prevents overlapping runs and self-clears after 180 minutes
   (`run_shorts.sh:51-57`).
9. `containsSyntheticMedia: true` is always set on YouTube uploads
   (`publish_zernio.py:201`). This is a disclosure obligation, not a preference.

### 1.3 Findings

Every item below is a thing that has to move, be replaced, or be made configurable.
IDs are referenced by the phase tasks in §5.

#### A. macOS-only

| # | Where | What |
|---|---|---|
| A-1 | `shorts/setup_shorts.sh:9,11,26-43`; `setup.sh:9,13,44,69-70` | Scheduling is a generated launchd plist plus `launchctl bootout/bootstrap`. There is no cron, systemd or Task Scheduler path. |
| A-2 | `shorts/run_shorts.sh:32`; `run_daily.sh:32` | Notifications are `osascript -e 'display notification'`. |
| A-3 | `run_shorts.sh:15`; `setup_shorts.sh:10`; `zernio:9`; `shorts/short:8`; `make_voice_sample.sh:7` | `/opt/homebrew/bin` is prepended to `PATH` in five scripts. |
| **A-4** | **`run_shorts.sh:77` and `:93`** | **BSD-only `date -j -v+Nd`. The failure is silent and wrong, not loud.** Line 77 falls back to today's date, so the publish offset vanishes. Line 93 falls back to `0`, which makes every slot compare as already past — so all four videos take the `--mode now` branch and **publish immediately instead of being scheduled**. On Linux this pipeline does not fail; it posts the whole day at once. Highest-priority correctness fix in Phase 1. |
| A-5 | `setup.sh:36-38`; `config.env:56-59` | The only offline TTS is the macOS `say` command. The zero-cost path — the feature meant to make people try this — does not exist off macOS. |
| A-6 | `engine/zernio.py:156` (`zoneinfo`) | `zoneinfo` needs the `tzdata` package on Windows; nothing declares it. |
| A-7 | `reel`, `zernio`, `shorts/short` | Bash wrappers with no Windows equivalent, and they work by `source`-ing `config.env`, which only a shell can read. |

#### B. Hard-coded to my machine

| # | Where | What |
|---|---|---|
| B-8 | `config.env:4,10` | `CONTENT_DIR` and `BRAND_DIR` pinned to `$HOME/Developer/personal/NexloreAI/...`. |
| B-9 | `run_shorts.sh:26-27` | The same absolute path again, as the fallback for `OUT` and `IDEAS`. |
| B-10 | `docs/OPERATIONS.md:3,23,27`; `prompts/daily_run.md:10`; `shorts/prompts/shorts_daily.md:11` | The prompts *tell the agent* its working directory is `NexloreAI/content/automation`. Prose and prompts both need de-pathing. |
| B-11 | `config.env` | Settings are a shell script. Python can only see them as environment variables, after Bash has sourced them. Config is not data. |
| **B-12** | — | **There are two copies of this code.** The channel runs from `content/automation`, which is not a git repository. This repo is a manually synced copy. Every tracked code file is byte-identical *today* (verified), but nothing keeps them that way. A fix merged here does not reach the channel. |
| B-13 | `publish_queue.py:49` vs `run_shorts.sh:108` | The two ledgers disagree on identity: one keys on a repo-relative path, the other writes absolute paths. Move the output folder and idempotency silently resets. |

#### C. Brand baked into the engine

| # | Where | What |
|---|---|---|
| C-14 | `shorts_engine.py:211`, `:583`, `:575` | The literal string `"Nexlore AI"` is drawn into the header of every frame and onto the end card; the CTA button defaults to `"Follow Nexlore AI"`. |
| C-15 | `shorts_engine.py:119` | `assets/nexlore-icon-512.png` is opened unconditionally in `init()`. Any other brand crashes at frame zero. |
| C-16 | `shorts_engine.py:59-66`, `:69-74`, `:53-57` | Palette, gradient, font map, margin `X0` and `SAFE_BOTTOM` are module constants. |
| C-17 | `shorts/langmix.py`; `prompts/playbooks/02-hinglish-voice.md`; `shorts/prompts/shorts_daily.md` | Hinglish is structural, not a setting: a Devanagari loanword lexicon, a hard 38–55% English band, and `exit 1` when a script falls outside it. A Spanish or English-only channel has no path through this gate. |
| C-18 | `publish_zernio.py:28`, and `--yt-visibility public` passed literally at `run_shorts.sh:104` | YouTube category is pinned to `28` (Science & Technology) and visibility to public. A cooking channel is mis-categorised with no way to say so. |
| C-19 | `pillars/*.json`, `prompts/playbooks/*`, `competitors.md`, `shorts/STYLE.md` | One channel's editorial strategy sits at the repo root as if it were the product. It should be `examples/nexlore-ai/`. |

#### D. One provider per job

| # | Where | What |
|---|---|---|
| D-20 | `run_shorts.sh:65-66` | The agent is `claude -p` with Claude-Code-specific flags (`--allowedTools`, `--permission-mode`, `--max-turns`, `--output-format`). There is no LLM abstraction at all. |
| D-21 | `shorts/make_audio.py` | ElevenLabs only: hard-coded API base, `eleven_multilingual_v2`, a voice-settings dict and the music endpoint. **The README's list of seven TTS backends is not accurate for the live path** — the others exist in `.env.example` and in `engine/tts/codeswitch_bakeoff.py`, a one-off benchmark that nothing calls. Fixing this claim is part of Phase 2, not a README edit. |
| D-22 | `publish_zernio.py`, `engine/zernio.py`, `publish_queue.py`, `promote_instagram_backlog.py` | **Four independent Zernio clients**, ~1,170 lines between them, each with its own auth, retry policy, limits table and presign/upload/post sequence. They have already drifted. |
| D-23 | `shorts/prompts/shorts_daily.md` | Research is whatever the agent chooses to do inside a prompt. There are no source modules, so nothing about research is reusable, swappable or testable. |
| D-24 | `shorts_engine.py:586` (`SCENES`), `:448` (`VISUALS`) | Scene and visual types are closed dicts. A new style means editing core. |

#### E. Not packaged

| # | Where | What |
|---|---|---|
| E-25 | `promote_drafts.py`, `promote_instagram_backlog.py` | `import publish_zernio` — sibling-module imports that only resolve when the CWD is the repo root. |
| E-26 | — | No `pyproject.toml`, no package, no `__init__.py`, no tests, no CI, no linting, no templates, no `CODEOWNERS`. |
| E-27 | `run_shorts.sh` | Orchestration is Bash: locking, the due-time gate, slot arithmetic, ledger writes, and idempotency via `grep -F` on a JSON substring (`:87`). None of it can be unit tested. |
| **E-28** | `/usr/bin/python3` | **The pipeline actually runs on Python 3.9.6** (Apple's system Python; `setup_shorts.sh:18` builds `.venv` from it). The README claims 3.10+. The declared floor is already wrong, and moving to 3.10+ means installing a real Python on the Mac — a migration step, not a footnote. |
| E-29 | `engine/zernio.py:24-26` | The Zernio SDK is deliberately avoided because `setup.sh` rebuilds `.venv` with only Pillow and NumPy and would wipe it. Packaging removes the reason for this workaround. |

#### F. Secrets and licensing

| # | What |
|---|---|
| F-30 | **Clean.** No secrets in the working tree and none in any commit — `git log -p --all` scanned for `sk-`, `sk_`, `AIza`, `ghp_` and bearer-token patterns. `.gitignore` covers `.env`, `.env.*`, `*.env.bak*`, ledgers, `out/`, `logs/` and media. |
| F-31 | `config.env` mixes "defaults for everyone" with "my settings" (voice IDs, IST slot times). Not secret, but it is why a new user has to edit the repo to configure it. |
| **F-32** | **The repo is public and has no LICENSE file.** `gh repo view` reports `licenseInfo: null`, `visibility: PUBLIC`, empty description. With no licence the default is all rights reserved: nobody can legally fork, use or contribute. This is the most urgent single fix in the repo and it is blocked on decision 1 in §6. |
| F-33 | Third-party licences present and to be preserved: `.claude/skills/YT_SKILLS_LICENSE` (MIT, Jake Schincariol) and `fonts/README.md` (Inter and Poppins under OFL-1.1, DejaVu under its own licence). No music is bundled — `shorts/music/*.mp3` is gitignored. Correct as-is. |

### 1.4 The three facts that shape the plan

1. **The spec JSON is already the right seam.** Research, scripting, voice, render and
   publish communicate through one file. That is a job payload in all but name, and it is
   why this refactor is a reorganisation rather than a rewrite.
2. **The dangerous code is the Bash, not the Python.** The engine is clean and portable
   apart from its brand constants. The Bash holds the scheduling, the clock arithmetic and
   the idempotency — and that is where the silent cross-platform failure lives (A-4).
3. **The repo is not what runs.** Until B-12 is closed, "don't break my channel" and "make
   it open source" are two codebases, and every phase pays that tax twice.

---

## 2. Target architecture

Close to the brief. Four changes, each argued below.

```
creatoros_autopilot/
  core/        job model, spec schema (pydantic), pipeline orchestration,
               ledger, storage + queue interfaces, settings loader
  agents/      research + scripting agents, provider-agnostic; prompts as files
  providers/
    llm/       claude_code, anthropic, openai, gemini, ollama
    tts/       elevenlabs, azure, openai, sarvam, piper (local), say (macOS)
    research/  rss, arxiv, huggingface_papers, web_search
    publish/   zernio, youtube, instagram, local_folder
  quality/     gates: hook score, title score, language mix, layout check
  render/      engine + styles (current look ships as the "nexlore-v3" style)
  brands/      brand kits: name, logo, colours, fonts, voice, language mix
  scheduler/   APScheduler loop + generators for launchd / systemd / schtasks
  cli/         creatoros init | run | render | publish | schedule | doctor | status
  runtime/     the only place for OS-specific code
examples/      presets: nexlore-ai plus 2-3 others in other niches and languages
docs/
tests/
```

**Change 1 — `platform/` is renamed `runtime/`.** The repo already uses "platform" for a
publishing destination (`platforms/*.json`, `spec["platforms"]["youtube"]`). A second
meaning for the same word in a directory name will cost every contributor five minutes,
forever.

**Change 2 — add `quality/`.** The brief does not mention the gates, but they are
load-bearing: the daily run will not ship a hook scoring under 55, a title under 80 or a
script outside the 38–55% English band. They are currently a mix of skill scripts and a
repo script with exit codes. Behind one `Gate` interface, a Spanish-language user can drop
`langmix` and register their own — which is exactly the "plugins, not branches" principle
applied to the part of the pipeline that actually encodes my house style.

**Change 3 — one ledger, keyed on `(job_id, platform)`, never on a path.** Today there are
two ledgers with two notions of identity (B-13) and the live publisher writes neither of
them — `run_shorts.sh` writes on its behalf. A path is not an identity: it changes when the
output folder moves, and that silently re-arms a double-post. The job id is already in every
spec (`spec["id"]`), so this is a small change with a large payoff.

**Change 4 — the workspace stays outside the repo.** Rendered output already lives in
`content/youtube/shorts/DATE/`, not in the repo, and that instinct is right. Formalise it:
a `workspace` root from config, behind a `Storage` interface whose first implementation is
local disk and whose second will be S3-compatible. Nothing in `render/` may construct a
path from `$HOME` or from `__file__`.

Everything else follows the brief: a job is a spec in and an MP4 plus metadata out, with no
reference to the scheduler, the CLI or a local path, so the same job can run on a server
worker later.

---

## 3. Naming

`creatoros` **is taken on PyPI** — version 1.0.0, an unrelated web-to-markdown and TTS
library — and its wheel ships a top-level `creatoros/` package. So `import creatoros` is
not safely available to us: a user with both installed gets one clobbering the other.

Checked and available: `creatoros-autopilot`, `creator-os`, `creatoros-cli`, `creatorkit`,
`postpilot`, `reelpilot`, `creatorctl`.

**Decided** (2026-11-17) — keep the product name, split the three identifiers:

| | |
|---|---|
| Product | CreatorOS Autopilot (unchanged) |
| PyPI distribution | `creatoros-autopilot` |
| Import package | `creatoros_autopilot` |
| CLI command | `creatoros` |

The taken package declares no console script, so the `creatoros` command — the name users
actually type and the one in the brief's user story — is free. The cost is a clunky import
line for contributors, which matters far less for an application than for a library.
Decision 2 in §6. Everything created from Phase 1 onward uses these three names.

---

## 4. Migration path

The channel keeps running from `content/automation` and is not touched until Phase 6.

1. **Phases 1–5** build the package in this repo. The live pipeline is untouched. The two
   copies (B-12) stay out of sync on purpose, and the only rule is that no fix lands here
   without also landing in `content/automation` until the cutover.
2. **Phase 6, step 1.** Install a real Python (3.11+) on the Mac — the current runtime is
   3.9.6 (E-28). `pipx install creatoros-autopilot` or a local editable install.
3. **Phase 6, step 2.** Generate `~/.creatoros/creatoros.yaml` and `.env` from the
   `examples/nexlore-ai` preset. Point `workspace` at the existing
   `content/youtube/shorts/` so no rendered stock is orphaned.
4. **Phase 6, step 3.** `creatoros migrate-ledger` imports both `publish_ledger.jsonl` and
   `shorts/ledger.jsonl` into the unified ledger, re-keying absolute paths onto job ids.
   **This is the step that can double-post if it is wrong**, so it is verified by
   `creatoros publish --dry-run` reporting "would skip N, would post 0" for a day that is
   already out.
5. **Phase 6, step 4.** Dry-run a full day end to end: research, script, voice, render,
   `--dry-run` publish. Compare the MP4s against that day's real output.
6. **Phase 6, step 5.** Swap the launchd plist to call `creatoros run --if-due`. Keep the
   old scripts in place, unloaded, for one week.
7. **Phase 6, step 6.** Delete the old scripts and the `content/automation` copy — only
   after you confirm a week of clean runs.

---

## 5. Phases

One PR per phase. Each leaves the repo working and its tests passing.

### Phase 0 — Audit and plan *(this document)*
- [x] Map the code; list every macOS-only, hard-coded, brand-specific and secret-adjacent spot (§1.3).
- [x] Propose the architecture, package and CLI name, and the migration path (§2–§4).
- [x] Check PyPI availability (§3).
- [x] Decisions in §6 — all four answered 2026-11-17.

**Done when:** §6 is answered — it is. Phase 1 is unblocked, and its first two commits are
the AGPL-3.0 `LICENSE` and the `pyproject.toml` that fixes the names, because both touch
every file created afterwards.

### Phase 1 — Package and run anywhere ✅

*Landed. Fixes A-1 … A-7, B-8 … B-13 (partly), E-25, E-26 (partly), E-27, E-29, F-32.*

- [x] Add `LICENSE` (AGPL-3.0) and a CLA, then `pyproject.toml` naming the distribution
  `creatoros-autopilot`, the package `creatoros_autopilot` and the `creatoros` console
  script. Python floor 3.10+. **Three runtime dependencies, not the five planned**: Pillow,
  NumPy and PyYAML, plus `tzdata` on Windows. pydantic and an HTTP client were deferred —
  the spec schema moves to pydantic in Phase 2 where it buys something, and `urllib`
  already does the job. A dependency every user installs should earn its place.
- [x] Move the modules into `creatoros_autopilot/`; kill the sibling imports (E-25).
- [x] Add `SPDX-License-Identifier` headers to every source file (and to no third-party one).
- [x] Replace the Bash wrappers with the `creatoros` CLI (A-7, E-27). Port the lock, the
  due-time gate and the slot arithmetic to Python. Seven scripts, 450 lines of shell, gone.
- [x] **Fix A-4**: all date and timezone arithmetic in Python with `zoneinfo`; `tzdata`
  declared on Windows (A-6). `TestNeverPublishesEarly` asserts the property across every
  offset and slot time, not one example. Verified on Linux, where `date -j` does fail.
- [x] De-path config (B-8, B-9, B-10): `creatoros.yaml` with a `workspace` root. No `$HOME`
  literal anywhere in code, prompts or defaults — enforced by `tests/test_portability.py`.
- [x] Detect `ffmpeg`/`ffprobe` on `PATH` with a per-platform install hint; `/opt/homebrew`
  gone from all five scripts that prepended it (A-3).
- [x] `creatoros doctor`: binaries, Python version, config, workspace, keys and agent,
  each with the exact fix. Required vs optional, so only a render-blocking failure exits
  non-zero. A test asserts it never prints a secret.
- [x] Dockerfile and `docker compose`, non-root, workspace as a volume.

**Acceptance — met on macOS and Linux, CI covers Windows.**

| | |
|---|---|
| macOS, Python 3.10, clean venv, outside the repo | 33.5 s 1080×1920 h264 MP4 in 22 s, no keys |
| Linux (Docker, Python 3.12), read-only spec | same video, no keys |
| Test suite | 352 tests, green on both |
| `creatoros doctor` | passes on both with nothing configured |
| Windows | covered by the CI matrix (ubuntu · macos · windows × 3.10 · 3.12) |

CI is deliberately minimal here — it proves the acceptance criterion and nothing more.
ruff, mypy, pre-commit and gitleaks are Phase 4, as planned.

**Two things Phase 0 did not predict.**

*The renderer could not render a read-only spec.* It wrote its scene intermediates and
cover frame beside the **input**, so `creatoros render /app/examples/specs/kv-cache.json
-o /workspace/out.mp4` died on a permission error for a path the user never named — in a
container, and after any `pip install`. Artifacts now hang off the output path. Docker
found this; macOS never would have, because there the repo is writable.

*Two modules are not portable and never were.* `reel_engine.py` (the retired v1 renderer)
and `codeswitch_bakeoff.py` (the TTS benchmark behind D-21's phantom backends) both shell
out to macOS `say`. Deleting a renderer is a product decision, so they moved to
`creatoros_autopilot/legacy/` instead: quarantined, imported by nothing — asserted, not
assumed — and exempt from the portability rules so those rules can be strict everywhere
else. **Phase 2 decides: port `say` into a real TTS provider with an `available()` check,
or delete both. That one is Aman's call.**

### Phase 2 — Plugin system ✅

*Landed. Fixes D-20 … D-24, and the three modules Phase 1 silently broke.*

- [x] Define the interfaces: `LLM`, `TTS`, `ResearchSource`, `Publisher`, `Style`, `Brand`, `Gate`.
- [x] Register via Python entry points. Core never imports a concrete provider —
  `test_core_names_no_provider` reads the AST and fails on an import or a comparison.
- Move the existing providers behind them. **Collapse the four Zernio clients into one**
  (D-22). The README's TTS claim is already narrowed to what is actually wired up (D-21),
  with a test to stop it drifting again.
- [x] Add Ollama (local LLM) and Piper (local TTS) — the zero-cost path (A-5).
- [x] Add `local_folder` publisher: writes the video plus the metadata that *would* have
  been sent to each platform, so it doubles as a dry practice run for a real one.
- [x] Add direct YouTube Data API and Instagram Graph API publishers beside Zernio, as the
  documented default for new users (decision 4). Zernio stays fully supported and stays the
  Nexlore preset's publisher.
- [x] Keep the AI-narration disclosure. Zernio sets `containsSyntheticMedia`; the Data API
  has no such field, so the YouTube backend says so loudly rather than dropping it silently.

**Acceptance — met.** `tests/test_plugins.py::TestAcceptance` writes a TTS provider,
registers it, narrates through it, proves the shared caching and stale-clip cleanup apply
to it unchanged, and then reads the AST of every file in `core/` to assert it names no
provider. A full keyless run — render plus `publish=local_folder`, no network, no key —
completes and writes the video with its would-be metadata.

**Four defects this phase surfaced, three of them mine:**

1. **Three modules had been broken since Phase 1.** `queue.py`, `promote.py` and
   `promote_instagram.py` still did `import publish_zernio` after the file was renamed —
   the exact sibling-import problem (E-25) the move was meant to end. 665 tests and six
   green CI jobs never noticed, because nothing imported them. `tests/test_imports.py` now
   imports every module in the package; it is the cheapest guard against that whole class
   of bug and it caught all three immediately.
2. **`backends: [local_folder]` was routed to the live YouTube API.** The backend advertised
   only `"local"`, so routing skipped it and fell through to a real publisher. A user asking
   for a dry practice run would have attempted a real upload. Wildcard backends and a
   three-rule routing order fix it.
3. **One backend was called once per platform.** Routes were grouped by object identity, but
   the registry returns a fresh instance per lookup — so Zernio, whose whole advantage is
   that one upload serves both platforms, would have uploaded twice and created two posts
   where Phase 1 created one.
4. The Zernio collapse is real: four clients, ~1,170 lines, became one `_api.py` that the
   publisher plugin and all three operational tools call.

### Phase 3 — Config, brands and onboarding ✅

*Landed. Fixes C-14 … C-19, F-31.*

- [x] `creatoros init`: interactive wizard writing `creatoros.yaml` and `.env`. Secrets go
  to `.env` only, are never echoed, and the file is written `0600`. It parses its own
  output before claiming success, and refuses to replace an existing config unasked.
- [x] Brand kits (C-14, C-15, C-16): name, logo, colours, fonts out of the engine into
  `brands/*.yaml`. A missing logo renders a wordmark, a missing font falls back to the
  bundled one — a day's run is not lost because an image moved.
- [x] **Verified byte-for-byte**: the Nexlore kit renders the examples pixel-identically to
  a pre-refactor baseline, so the channel that already exists sees no change at all.
- [x] Language as a setting (C-17). The gate reads the brand's `language` block, and a
  brand with no mix disables it — a single-language channel has nothing to measure rather
  than a check it always fails.
- [x] YouTube category and visibility are per-channel config (C-18).
- [x] Content pillars with daily, weekday, weekend, weekly and monthly cadences. The
  `pillars/*.json` files described all of this and **nothing read them**: `schedule_ist`
  was declared while launchd ran one job regardless, and `order: sequential` pointed at a
  state file no code opened. The loader reads the existing files unchanged.
- [x] Move the Nexlore strategy into `examples/nexlore-ai/` (C-19) and author two more
  (decision 3): `ember-fitness` (English, free providers, no logo) and `claro-es` (neither
  English nor mixed). Each preset is a self-contained folder — config, brand and pillars —
  and relative paths resolve against the config file, so a scheduled run does not depend on
  where it starts.

**Acceptance — met, with room to spare.** On a clean directory:

```
creatoros init --yes     # render-only defaults, no key requested
creatoros doctor         # all checks passed
creatoros render kv-cache.json -o first.mp4
```

**19 seconds**, start to a finished 33.5-second 1080x1920 MP4, no key set and no account.
The budget was ten minutes.

**Two bugs I introduced and caught here**, both in the brand work:

1. `apply_brand()` cleared the render cache unconditionally, so a 2160x3840 float array was
   rebuilt once per scene instead of once per worker. The suite went from two minutes to
   over nine.
2. `Palette.__getattr__` read `self.colours`, which recurses forever when the object is
   unpickled into a render worker — the attribute is missing until state is restored, so
   the lookup calls itself. The only symptom was a job that never finished.

The second is the more interesting one: it was invisible locally because the parent process
never unpickles, and it would have appeared only under the multiprocessing render.

### Phase 4 — Contributor experience ✅

*Landed. Closes E-26.*

- [x] pytest: spec validation, ledger idempotency (including the double-post regression),
  slot/timezone arithmetic, mocked providers, and a render smoke test on a 2-scene spec.
- [x] ruff, mypy, pre-commit — both clean on 64 files. ruff's first run found 403 issues
  and **242 were noise**: 182 in `render/engine.py`, written as dense drawing code on
  purpose and the only code whose output is verified pixel-for-pixel, and ~60 in the
  vendored MIT skill scripts, which are not ours to reformat. Both are excluded with the
  reasoning in the config. One autofix was reverted: it expanded the Hinglish lexicon from
  a readable word list into a single 4,000-character line.
- [x] CI grows a lint job, a types job and gitleaks over the **full history** — a key
  committed three commits ago and removed in the fourth is what a shallow scan misses. The
  matrix also runs the first-run path end to end from an empty directory.
- [x] `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), `SECURITY.md`,
  three issue templates, a PR template and `CODEOWNERS`.
- [x] `scripts/check_no_secrets.py`: a pre-commit hook that rejects a key-shaped value in
  any committed config file, which is what makes "settings in YAML, secrets in .env"
  enforceable rather than a convention.
- [x] `docs/plugins/first-plugin.md` and `docs/plugins/add-a-style.md`.
- [x] **18 drafts** in `docs/seed-issues/` — 10 `good first issue`, 8 `help wanted`. Every
  file path and function name in them was checked against the tree; where something depends
  on a third-party API that could not be verified, the issue says "check whether…" rather
  than asserting it.

**Acceptance — met, except the time budget.**

| | |
|---|---|
| ruff, mypy | clean, 64 files |
| gitleaks | clean over the full history |
| CI | lint + types + secrets + six-way OS matrix + the first-run path |
| `pytest` | **~2 minutes**, not the 60 s in the plan |

The suite is slower than planned because it renders real video: seven full MP4 encodes and
a dozen contact sheets, which is also why it catches what it catches. Splitting it so
`pytest -m "not render"` runs in seconds is worth doing and is not done — it is the one
Phase 4 item carried forward.

It was briefly **nine minutes**, from two bugs introduced in Phase 3 and caught here. Both
are written up in that phase; the second is the one worth remembering, because it would
only ever have appeared under the multiprocessing render.

### Phase 5 — Launch-ready README and assets ✅

- [x] Rewrite the README to the brief's structure: pitch, demo, 3-command quickstart, features,
  provider support matrix, Mermaid architecture diagram, roadmap, contributing, badges.
- [x] Example gallery (the same spec under three brands, rendered), GitHub topics, and a
  social preview spec with the crop-safe area called out.
- [x] `docs/assets/demo.gif` is a **real render** of a shipped example, not a placeholder.
- [x] `docs/LAUNCH.md`: Show HN, Product Hunt, Reddit, X, LinkedIn, Dev.to. Each draft
  leads with the `date -j` bug rather than a feature list, because an honest failure is the
  most credible thing this project has to say.
- [x] A pre-launch checklist that blocks on the things that would embarrass us: the PyPI
  name unclaimed, the live-API publishers unrun, zero open issues.

**Acceptance — met, and one claim corrected.**

Every command in the README was run before it was committed. The provider matrix matches
the registry.

**The track record in the first draft was wrong.** It said the pipeline had run "every day
since 2026-11-16" and, in the Show HN draft, that I had written it "about a year ago".
Checking `history.jsonl` and the ledger: **49 videos across 8 publishing days, 2026-11-07 to
2026-11-16**. Both claims are now the measured figure, and `LAUNCH.md` opens with a rule that
every number in a draft must be checkable — a launch post with one disprovable figure is
worse than no launch post.

### Phase 6 — Migrate the channel ✅ *(rehearsed; the cutover is Aman's to run)*

- [x] `creatoros migrate-ledger`, which imports both old ledgers and re-keys them onto job
  ids. Dry run by default; refuses to write with unresolved rows.
- [x] Rehearsed against a **copy** of the real data: 94 rows to import, 35 already recorded,
  2 bookkeeping markers skipped, **0 unresolved**.
- [x] Dry-ran two real days. A fully published day posts nothing; a partly published day
  skips the video that went out and retries only the one that did not.
- [x] `docs/MIGRATION.md`: nine steps, each reversible, with the rollback for the one that
  feels like it is not.
- [ ] The cutover. **Not done, and not mine to do** — the launchd job still runs
  `shorts/run_shorts.sh` and no old script has been deleted.

**The rehearsal found a bug in the migration itself.** The first version reported 79 rows
imported while the ledger rejected 15 of them: the oldest shell-ledger rows recorded *that*
a video published but not *where*, and an entry with no platform is refused. Fifteen
already-published videos would have looked unpublished, and the next run would have posted
them again — the exact failure the command exists to prevent, in the command that exists to
prevent it. It now fills those in from `publish.platforms`, reports how many it assumed, and
fails loudly if what it wrote differs from what it planned.

**Two things it surfaced about the channel's own data**, both written up in `MIGRATION.md`:
the 2026-11-16 specs carry no `platforms.instagram.caption`, which is why
`01_guidance-scale` failed to publish that day and still fails a dry run; and the reels-era
and shorts-era ledgers use different job-id conventions, which import cleanly but will not
match if an old reel is ever re-rendered.

### Phase 7 — Hosted platform readiness ✅ *(design only, as specified)*

[`docs/HOSTED_ARCHITECTURE.md`](HOSTED_ARCHITECTURE.md). No code.

Four conclusions worth pulling out, because they are decisions rather than descriptions:

**GPU workers are not useful.** Nothing in the renderer is a matrix operation a GPU would
accelerate — Pillow draws, NumPy composites, `libx264` encodes. A GPU tier only matters if a
future style does video diffusion, which is a different product.

**TTS is the variable cost, not CPU.** A render worker produces ~90 seconds of finished video
per CPU-minute, so a four-a-day tenant costs about two CPU-minutes daily. The same tenant
costs ~72,000 TTS characters a month, which is above several providers' entry tiers.
Per-tenant TTS budgeting is a launch requirement, and Piper on a worker is what makes a free
tier possible at all.

**OAuth review is on the critical path.** The hosted product has to *be* the OAuth app;
YouTube's `youtube.upload` scope and Instagram's publishing permissions both need platform
review, measured in weeks. It is the first thing to start and the last thing anyone thinks of.

**Build order matters more than the design.** Steps 2–4 of §9 — one containerised worker, the
Storage and Queue interfaces, the ledger as a table — are contributions to *this* repo. If
the web app is built first, the core gets forked within a month and the open-source project
becomes a demo of the real one.

It also names the question the architecture cannot answer: a hosted service that publishes
in a creator's name is a publisher in a way a CLI on a laptop is not, and what happens when
the agent writes something wrong about a real person is a product and legal decision to make
before launch rather than after the first incident.

---

## 6. Decisions — answered 2026-11-17

### ✅ 1. Licence → **AGPL-3.0, with a CLA**

The repo is public with no licence right now (F-32), which legally means nobody may use or
contribute to it. The options were:

- **MIT / Apache-2.0** — maximum adoption. Anyone may host a modified version commercially
  without sharing changes, including a competitor to the future hosted product. Apache-2.0
  adds an explicit patent grant and is the safer of the two for a project with a commercial
  future.
- **AGPL-3.0** — anyone who *hosts* a modified version must publish their changes. This is
  the standard open-core defence and exactly what it was designed for. The cost is real:
  many companies ban AGPL dependencies outright, so some contributors and most corporate
  users will stay away.

**Chosen: AGPL-3.0 with a CLA.** The hosted product is planned, and AGPL is the licence
that stops someone hosting a modified core against it. For a creator tool the corporate-ban
cost is small — the users are individuals, not procurement departments. The CLA preserves the
right to relicense the hosted version.

Consequences to carry into Phase 1: `LICENSE` is the first commit; every source file gets an
`SPDX-License-Identifier: AGPL-3.0-or-later` header; `CONTRIBUTING.md` (Phase 4) explains the
CLA and wires up a signature check; and the README must state the licence plainly, because
AGPL is the first thing a cautious adopter looks for.

### ✅ 2. Package and CLI name → **`creatoros-autopilot` / `creatoros_autopilot` / `creatoros`**

`creatoros` is taken on PyPI by an unrelated library whose wheel occupies the `creatoros`
import name (§3), so the three identifiers are split.

**Chosen:** PyPI distribution `creatoros-autopilot`, import package `creatoros_autopilot`,
CLI command `creatoros`. The command users actually type is free — the taken package ships no
console script — and the product and repo names are unchanged. The clean single-name
alternatives (`creatorkit`, `postpilot`, `reelpilot`, all available) were not taken.

### ✅ 3. Example presets → **three**

**Chosen:** three presets — `nexlore-ai` (Hinglish AI explainers, yours), one English
non-tech (fitness or finance), and one Spanish. Three is what proves the brand and language
settings actually work; one preset leaves those abstractions untested and the project reads
as "an AI-shorts tool" rather than a platform. Authored in Phase 3.

### ✅ 4. Publishing → **direct APIs documented as the default, Zernio fully supported**

**Chosen:** the direct YouTube Data API and Instagram Graph API publishers are the
*documented default* for new users; Zernio stays fully supported and stays the Nexlore
preset's publisher. Reasons: a new user should not need a paid third-party account to try the
project, direct APIs are what the hosted version will use anyway, and a Zernio outage should
not be able to take the project down.

The cost is that direct APIs need OAuth, which is much harsher onboarding — so `local_folder`
becomes the real first-run default and publishing is opt-in either way. Built in Phase 2.

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| **A-4 posts a whole day at once** on any non-macOS machine today. | Fix first in Phase 1; regression test asserts a future slot never takes the `now` branch. |
| **Ledger migration double-posts** during Phase 6. | Import both ledgers, then prove with `--dry-run` that an already-published day reports zero posts before anything runs live. |
| Python 3.9.6 → 3.11 on the Mac (E-28). | Phase 6 step 1, isolated, with the old `.venv` left intact until the cutover is confirmed. |
| The two copies (B-12) drift during Phases 1–5. | Every fix lands in both until cutover; Phase 6 closes it permanently. |
| Testing against ElevenLabs burns the channel's monthly character quota. | Piper fixtures and recorded responses for all tests; no test may call a paid TTS API. |
| Moving the agent behind an LLM interface changes output quality. | The Nexlore preset keeps `claude_code` as its provider through Phase 6; Ollama is validated separately, not by swapping yours. |
| Scope: seven phases is a lot of surface for one maintainer. | Phases 1 and 2 alone make the project usable and contributable. 3–5 are adoption. 6 is yours. 7 is a document. |
