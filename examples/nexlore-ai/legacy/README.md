# The retired v1 reels pipeline

Everything here belonged to the Instagram-only reels pipeline that was retired on
2026-11-16, when one pipeline took over both platforms. It is kept so that work can be
reproduced; none of it runs.

- `daily_run.md`, `upload_only.md` — the agent prompts for `run_daily.sh`, which uploaded
  through a local Edit app rather than publishing directly.
- `example_paper.json` — a reference spec for `creatoros_autopilot/legacy/reel_engine.py`.
  Its scene types (`card`, `list`, `flow`) do not exist in the current engine, so
  `creatoros render` will reject it.

The live equivalents are `../STYLE.md`, `../../specs/` and
`creatoros_autopilot/agents/prompts/shorts_daily.md`.
