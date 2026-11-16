# How this folder is organised

The pipeline started as "4 Instagram reels a day". It now has to carry six platforms,
several content pillars and a comment-to-DM flow. This file is the map.

**The rule that keeps it scalable: a new platform or a new pillar is a new JSON file, not
a new branch in the code.** Anything that differs per platform or per pillar lives in
`platforms/` or `pillars/` as data. Code reads those files.

```
automation/
├── engine/              the renderer. JSON spec -> 1080x1920 MP4 + cover.jpg
│   └── reel_engine.py
│
├── platforms/           ONE FILE PER PLATFORM - limits, formats, language, which key
│   ├── instagram.json       enabled
│   ├── youtube.json         enabled
│   ├── linkedin.json        off, limits unverified
│   ├── twitter.json         off, limits unverified
│   ├── facebook.json        off, limits unverified
│   └── medium.json          off, probably needs its own adapter
│
├── pillars/             ONE FILE PER CONTENT PILLAR - slot, order, schedule, spine
│   ├── concept-series.json  10:00 IST, SEQUENTIAL
│   ├── paper.json           11:15 IST
│   ├── enterprise.json      12:30 IST
│   ├── interview.json       13:45 IST, SEQUENTIAL
│   ├── toolbox.json         15:00 IST
│   └── myth.json            16:30 IST, weekends
│
├── prompts/
│   ├── daily_run.md         what the agent does each run
│   ├── upload_only.md
│   └── playbooks/           the writing and craft rules (read these first)
│
├── state/               machine state, not content
│   ├── series.json          arc queue + cursor for sequential pillars
│   └── (resources.json)     comment -> resource delivery, when built
│
├── out/YYYY-MM-DD/      working files for one day: spec, mp4, cover, caption, STATUS.md
├── specs/               reference specs
├── assets/  fonts/      brand assets
├── logs/
│
├── publish_queue.py     batch publisher, idempotent, ledger-backed
├── publish_zernio.py    single-reel publisher + shared helpers
├── promote_drafts.py    drafts -> live, whole post (both platforms)
├── promote_instagram_backlog.py  drafts -> live on Instagram only, paced, dedup'd
├── run_daily.sh         the daily runner launchd calls
├── setup.sh             dependency check + launchd install
├── config.env           settings (no secrets)
├── .env                 secrets only, gitignored
└── publish_ledger.jsonl what has been posted where - the idempotency record
```

## platforms/*.json

Each file describes one destination: formats and their limits, text limits and the fold,
hashtag counts, where the cover image comes from, the narration language register, and
**which Zernio API key reaches it**. `"enabled": false` means the publisher skips it.

`"verified": true` marks a number confirmed against a primary source in this project.
Anything `false` is a placeholder and must be checked before that platform is turned on.

Adding LinkedIn = fill in its limits, set `enabled: true`, point `zernio_key` at the key
whose Zernio account actually has LinkedIn attached. No code change.

## The multi-key problem

Zernio allows **2 connected accounts per Zernio account**, so six platforms need three
Zernio accounts and three API keys: `ZERNIO_API_KEY`, `ZERNIO_API_KEY_2`,
`ZERNIO_API_KEY_3`.

Consequence worth understanding: today one post can target Instagram *and* YouTube
together because both sit behind one key. Once platforms are split across keys, a single
reel becomes **one Zernio post per key**. The ledger is already keyed on
`(relative path, platform)`, so that fan-out is safe — but the idempotency key and the
"one upload, many platforms" optimisation both become per-key.

## pillars/*.json

`order: "sequential"` pillars pull their next topic from `state/series.json`, which holds
an arc queue and a cursor, so tomorrow's episode is deterministic and a week adds up to
one subject. `order: "random"` pillars pick fresh each day and dedupe against
`history.jsonl`.

`schedule_ist` is the IST time that pillar runs. `also_story` means the same vertical
video is also posted as an Instagram Story. `resource` names what a commenter gets sent
when the comment-to-DM flow is built.

## What is NOT here yet

- **Comment -> DM resource delivery.** Feasibility is being researched; it depends on
  whether Zernio exposes comments and DMs, and on Meta's policy for follow-gated
  automated messages.
- **LinkedIn / X / Facebook / Medium adapters.** The descriptors exist; the limits are
  unverified and the platforms are off.
- **Stories.** `also_story` is declared but nothing reads it yet.
- **Per-pillar scheduling.** `schedule_ist` is declared; launchd still runs one daily job.
