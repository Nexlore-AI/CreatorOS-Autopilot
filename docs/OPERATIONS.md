# Running it day to day

Everything below assumes `creatoros doctor` passes. If it does not, it tells you what to
fix and this document will not help you more than it does.

## Setup, once

```bash
pip install creatoros-autopilot          # or: pip install -e ".[dev]" from a clone
cp creatoros.example.yaml creatoros.yaml # settings
cp .env.example .env                     # secrets, gitignored
creatoros doctor
```

`ffmpeg` has to be on `PATH`: `brew install ffmpeg` (macOS), `apt install ffmpeg`
(Debian/Ubuntu), `winget install Gyan.FFmpeg` (Windows). Nothing else is a system
dependency — the renderer draws every frame itself.

## One video at a time

```bash
creatoros preview out/01_topic.json      # contact sheet; check layout BEFORE rendering
creatoros audio   out/01_topic.json      # narration + a music bed
creatoros render  out/01_topic.json      # the MP4 and its cover frame
creatoros publish out/01_topic.mp4 --dry-run
```

Preview first, always. It writes one still per scene as a single JPEG, and it takes a
second where a render takes minutes. Text running off the frame, a card overlapping a
visual, anything in the bottom sixth where the Shorts UI sits — all of it is visible there
and all of it is cheap to fix there.

## A whole day

```bash
creatoros run                 # agent, then publish what was rendered
creatoros run --skip-agent    # publish what is already rendered, nothing else
creatoros run --dry-run       # decide everything, publish nothing
creatoros run --if-due        # what the scheduler calls: run only if due and not done
creatoros status              # what has run, what is still pending
```

`run` takes a lock, so a scheduled run and a manual one cannot collide. A lock older than
three hours is assumed to belong to a run that was killed and is cleared.

## The daily job

```bash
creatoros schedule show       # print the unit; writes nothing
creatoros schedule install    # launchd / systemd timer / Scheduled Task, per OS
creatoros schedule remove
```

`show` first. It prints exactly what would be written and where, and the commands that
would register it. Nothing touches your machine until you run `install`.

Afterwards, inspect it the way you would any other job on that OS:

| | |
|---|---|
| macOS | `launchctl list \| grep com.creatoros.autopilot` |
| Linux | `systemctl --user list-timers creatoros-autopilot.timer` |
| Windows | `schtasks /Query /TN creatoros-autopilot` |

## Publishing

```
publish:
  mode: draft          # draft never publishes. This is the default, on purpose.
  platforms: []        # empty means render only
  offset_days: 1
  post_times: ["09:00", "13:00", "18:00", "21:00"]
```

**`draft` vs `auto`.** `draft` creates the post and parks it for you to review. `auto`
schedules a slot that is still ahead and publishes one that has passed. There is no
review window in `auto` — that is the trade, and it is why `draft` is the default.

**Why `offset_days: 1`.** Rendering four videos takes about six hours, so they are
scheduled onto the *next* day's slots and never race the clock. The pipeline always holds
a day of finished stock, and a slow or failed run does not leave a gap. Set `0` for
same-day, where a slot already behind you publishes immediately.

**Slots** are matched to a video's `0N_` filename prefix: `01_` takes the first post time,
`02_` the second. More videos than slots reuse the last one.

**The ledger** (`<workspace>/ledger.jsonl`) records every attempt, keyed on
`(job id, platform)`. A video already published to a platform is skipped; one that failed
on Instagram but succeeded on YouTube retries Instagram alone. It is append-only — do not
edit it to force a re-post, publish by hand instead.

## Where things are

Everything the tool writes lives under `workspace` from `creatoros.yaml`. `creatoros
doctor` prints the resolved path.

```
<workspace>/
├── jobs/YYYY-MM-DD/   that day's specs, voice clips, MP4s, covers, STATUS.md
├── music/             your royalty-free tracks (see MUSIC.md)
├── logs/
└── ledger.jsonl       what has been published where
```

Nothing is written into the installed package or the clone.

## When something goes wrong

| | |
|---|---|
| `creatoros doctor` | first, always |
| `creatoros status --date YYYY-MM-DD` | what the day actually produced |
| `creatoros publish VIDEO --dry-run` | the exact payload, without calling anything |
| `creatoros run --dry-run` | every slot decision, without publishing |

A video that rendered but did not publish is normal if `publish.platforms` is empty or
`mode` is `draft` — `status` says so. A video that did not render leaves its `_scenes`
folder behind; delete it and re-render.
