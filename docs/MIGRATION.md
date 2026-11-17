# Moving the Nexlore AI channel onto the CLI

Written for Aman. Nothing here has been done to the live setup — the launchd job still runs
`shorts/run_shorts.sh` from `content/automation`, and the old scripts are untouched.

Everything below was rehearsed against a **copy** of the real data. The numbers are from
that rehearsal.

## What the rehearsal found

| | |
|---|---|
| Ledger rows to import | **94** (79 with a platform, 15 more fanned out across both) |
| Already recorded | 35 |
| Bookkeeping rows skipped | 2 — `(promote)` and `(backlog)` markers, not publications |
| Unresolved | **0** |
| Dry run, 2026-11-15 (fully published) | posts nothing, both videos skipped |
| Dry run, 2026-11-16 (partly published) | skips `02_learning-rate`, retries `01_guidance-scale` |

**One bug was found and fixed during the rehearsal.** The first version of
`migrate-ledger` reported 79 rows imported while the ledger rejected 15 of them — the oldest
shell-ledger rows recorded *that* a video published but not *where*, and an entry with no
platform is refused. Fifteen already-published videos would have looked unpublished, and the
next run would have posted them again. The command now fills those in from
`publish.platforms`, says how many it assumed, and fails loudly if what it wrote differs
from what it planned.

## Before you start

- [ ] **Install a real Python.** The live pipeline runs on 3.9.6 (Apple's `/usr/bin/python3`)
      and CreatorOS needs 3.10+. `brew install python@3.12` or `uv python install 3.12`.
- [ ] Decide what to do about `legacy/` and the PyPI name — neither blocks this.
- [ ] Pick a quiet day. The cutover is reversible at every step until step 7, but you do not
      want to be doing it while a render is running.

## The steps

### 1. Install, outside the old tree

```bash
pipx install creatoros-autopilot      # or: pip install --user creatoros-autopilot
creatoros --version
```

Do not install into `content/automation/.venv`. `setup_shorts.sh` rebuilds that venv with
only pillow and numpy, which would wipe it.

### 2. Make the config directory

```bash
mkdir -p ~/.config/creatoros && cd ~/.config/creatoros
cp -r <clone>/examples/nexlore-ai/* .
```

That gives you `creatoros.yaml`, `brand.yaml`-equivalent (`brand: nexlore-ai`, bundled),
`pillars/`, `STYLE.md` and `voice.md`.

Edit `creatoros.yaml` and set the workspace to where your renders already live:

```yaml
workspace: /Users/aman/Developer/personal/NexloreAI/content/youtube
```

The preset already pins `backends: [zernio]`. **Leave that.** Without it, routing prefers a
backend needing no third-party account and would send your videos to the direct YouTube and
Instagram APIs, which are not configured for this channel — and one Zernio upload reaches
both platforms, which two direct backends cannot do.

### 3. Copy the secrets

```bash
cp ~/Developer/personal/NexloreAI/content/automation/.env ~/.config/creatoros/.env
chmod 600 ~/.config/creatoros/.env
```

`ELEVENLABS_API_KEY` and `ZERNIO_API_KEY` carry over unchanged.

### 4. Check it

```bash
creatoros doctor
```

Everything should be green except what you have deliberately not set up. If `brand` does not
say `Nexlore AI`, or `publishing` does not route both platforms through `zernio`, stop here.

### 5. Import the ledger — the step that matters

```bash
creatoros migrate-ledger ~/Developer/personal/NexloreAI/content/automation \
  --roots ~/Developer/personal/NexloreAI/content/youtube/shorts \
          ~/Developer/personal/NexloreAI/content/automation/out
```

Read the output. It must say **`unresolved : 0`**. If it does not, an already-published video
would be posted again — do not pass `--force` to make that message go away.

Then, and only then:

```bash
creatoros migrate-ledger ~/Developer/personal/NexloreAI/content/automation \
  --roots ~/Developer/personal/NexloreAI/content/youtube/shorts \
          ~/Developer/personal/NexloreAI/content/automation/out \
  --write
```

The last line must report the same number it planned.

### 6. Prove it would post nothing

```bash
creatoros status --date 2026-11-15
creatoros run --date 2026-11-15 --skip-agent --dry-run
```

Every video in a published day must say **"already published"**. If any says it would post,
the ledger import did not cover it — go back to step 5.

Then dry-run today:

```bash
creatoros run --skip-agent --dry-run
```

### 7. Switch the scheduled job

**This is the first irreversible-feeling step, and it is still reversible.**

```bash
creatoros schedule show          # read it first; writes nothing
```

Then stop the old job and start the new one:

```bash
launchctl bootout gui/$(id -u)/com.nexloreai.shorts
creatoros schedule install
launchctl list | grep com.creatoros.autopilot
```

The old plist stays on disk at `~/Library/LaunchAgents/com.nexloreai.shorts.plist`, so
rolling back is `creatoros schedule remove` then
`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.nexloreai.shorts.plist`.

### 8. Watch it for a week

```bash
creatoros status
```

Check each morning that the right number rendered and published. The old scripts still work
if you need them — nothing has been deleted.

### 9. Only after a clean week

Tell me, and I will remove `run_shorts.sh`, `setup_shorts.sh`, `short`, `reel`, `zernio`,
`run_daily.sh`, `setup.sh`, `make_voice_sample.sh` and `config.env` from
`content/automation`, and close out the two-copies problem (B-12) for good.

**I will not do this until you confirm a week of clean runs.**

## Two things the rehearsal surfaced about your data

**Your 2026-11-16 specs have no `platforms.instagram.caption`.** The publisher refuses to
post a Reel with an empty caption — a caption-less Reel gets no reach and cannot be found —
so `01_guidance-scale` fails the dry run for that reason, correctly. It also failed to
publish on the day for the same reason. Those specs need a caption added before that video
can go out; newer specs have one.

**The reels-era ledger and the shorts-era ledger use different job ids.** The old reels rows
key on the filename stem (`01_research_longcat-no-nvidia`); shorts specs carry a real
`spec["id"]` (`2026-11-16_01_guidance-scale`). Both import fine — they are different videos
and there is no collision — but if you ever re-render an old reel from its spec, it will not
match its old ledger row. That only matters for the retired pipeline.

## If something goes wrong

| | |
|---|---|
| A video posts twice | stop the job, check `<workspace>/ledger.jsonl` for the job id, and tell me — this should be impossible after step 6 |
| Nothing publishes | `creatoros doctor`, then `creatoros run --skip-agent --dry-run` to see each slot's decision |
| The agent does not run | `creatoros doctor` checks the `claude` CLI; `--skip-agent` publishes what is already rendered |
| Roll back entirely | step 7's rollback, then delete `~/.config/creatoros`. The old tree was never modified. |
