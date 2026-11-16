# How this repository is organised

```
creatoros_autopilot/
├── core/          the job, and nothing OS-specific
│   ├── settings.py    creatoros.yaml + .env; every path hangs off `workspace`
│   ├── spec.py        the job payload: load, validate (render vs publish), job id
│   ├── schedule.py    publish dates and slot decisions — see the note below
│   ├── ledger.py      append-only publish record, keyed on (job id, platform)
│   ├── lock.py        one run at a time, on every OS
│   ├── media.py       finding ffmpeg, asking it things
│   └── pipeline.py    a day's run: agent, then publish what was rendered
│
├── render/        the renderer. JSON spec -> 1080x1920 MP4 + cover frame
│   ├── engine.py      house style "nexlore-v3"
│   ├── fonts/         package data, so a pip install renders
│   └── assets/        package data
│
├── providers/     the backends
│   ├── tts/           elevenlabs
│   └── publish/       zernio (four copies of this client collapse into one in Phase 2)
│
├── quality/       gates a script must pass: langmix (language mix)
├── agents/        research and scripting; prompts as files
├── cli/           the `creatoros` command, and `doctor`
├── runtime/       THE ONLY PLACE for OS-specific code: notifications, config dirs, units
└── legacy/        works, not portable, imported by nothing. Quarantined, not deleted.

examples/
├── specs/         example specs — these are what `creatoros render` runs in the README
└── nexlore-ai/    a real channel's preset: config, house style, voice guide

platforms/         one JSON per destination: format and text limits, which key reaches it
pillars/           one JSON per content series: slot, ordering, schedule
prompts/           writing playbooks (story, Hinglish voice, metadata, critic)
tests/
docs/              ROADMAP.md, OPERATIONS.md, MUSIC.md
```

## The rules that keep this shape

**OS-specific code lives in `runtime/` and nowhere else.** Not as a convention —
`tests/test_portability.py` reads the AST of every module and fails if `core/`, `render/`,
`providers/`, `quality/` or `cli/` invokes `launchctl`, `osascript`, `systemctl`,
`schtasks` or `say`, branches on `sys.platform`, or contains a path from someone's home
folder.

**No date arithmetic in a shell.** The pipeline used to compute slot times with BSD
`date -j -v+Nd`, which is macOS-only and whose failure was swallowed by a fallback. On
Linux every slot compared as already past, so a run published the whole day at once
instead of scheduling it. All of it is now `core/schedule.py`, and the regression is
`tests/test_schedule.py::TestNeverPublishesEarly`.

**A new platform or pillar is a new file, not a new branch in the code.** `platforms/` and
`pillars/` are data. They are not read by code yet — Phase 2 wires them up, which is when
the publisher stops carrying its own limits table.

**Secrets are only ever in `.env`, and only ever in the environment.** No `Settings`
object holds a key, so nothing that logs or dumps settings can leak one.

**Job output never goes in the repo.** Renders, logs, the ledger and the music library all
live under `workspace`, which comes from config. A `pip install` must not write into
site-packages and a `git pull` must not fight with a day's output.
