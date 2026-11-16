# Nexlore AI content automation

Lives in `NexloreAI/content/automation`. Today it makes 4 Instagram reels a day; the same engine will feed Facebook, X/Twitter and YouTube (folders are ready in `NexloreAI/content/`).

## Daily reels

Every morning, Claude Code on this Mac:

1. **Researches** the last 48 h of AI: a lab or model release, the top Hugging Face paper, a concept worth explaining, and an enterprise AI story.
2. **Scans competitors** (`competitors.md` + whatever it finds) for topics and hook styles that are working right now.
3. **Scripts** 4 reels as JSON specs (25–40 s each).
4. **Renders** them as animated 1080×1920 MP4s with `engine/reel_engine.py`, with a **voiceover** and background music that dips under the voice. It checks the previews and fixes layout problems.
5. **Uploads** them through the `metapilot-native` MCP server into the Edit app project, then logs everything to `history.jsonl` so no topic repeats.

| Slot | Series |
|---|---|
| 01 | New in AI (research and model news) |
| 02 | Paper of the day |
| 03 | AI explained (one concept) |
| 04 | AI in enterprise |

Every reel uses the Nexlore AI brand: Night background, cyan highlights, the signature gradient, the logo top-left on every frame, and a logo end card.
Finished reels and captions are archived in `NexloreAI/content/instagram/reels/YYYY-MM-DD/` (`REELS_DIR` in config.env).

## Setup (once)
```bash
cd ~/Developer/personal/NexloreAI/content/automation
brew install ffmpeg                          # if missing
./setup.sh                                   # checks everything, installs the 06:30 daily job
```

## Everyday use
```bash
./run_daily.sh                                 # run now (full pipeline)
./run_daily.sh --upload-only 2026-11-07        # only upload reels already in out/2026-11-07
launchctl kickstart -k gui/$(id -u)/com.nexloreai.reels   # trigger the scheduled job by hand
./setup.sh --remove                            # stop the daily job
```
Output for each day is in `out/YYYY-MM-DD/`: `*.mp4`, `*.caption.txt`, `*.json` (spec), `*.preview.png`, `trends.md`, `STATUS.md`. Logs are in `logs/`.

## Settings: `config.env`
- `UPLOAD_MODE=project`: reels land in the Edit app project as drafts, so you can review them before posting. Switch to `schedule` (uses `POST_TIMES`) or `publish` once you trust the output.
- `RUN_HOUR` / `RUN_MINUTE`: when the job runs. Re-run `./setup.sh` after changing these.
- `PUBLISH_MODE=schedule`: **there is no review window.** The 06:30 run hands each reel to Zernio already un-drafted, and Zernio posts it at its `POST_TIMES` slot. `draft` parks reels in the Zernio dashboard instead, where they stay until something promotes them — which is how Instagram once went 8 days without a post. `publish` sends all four out at once when the run finishes.
- `PUBLISH_PLATFORMS=instagram`: only relevant to the retired reels publisher (see above). The live pipeline is
  `shorts/run_shorts.sh`, which uses `SHORTS_PLATFORMS="instagram,youtube"` and sends the same video to both.

## The pipeline (since 2026-11-16)
One pipeline makes everything: `shorts/run_shorts.sh` renders `SHORTS_PER_DAY` (4) videos a day in the
`shorts/STYLE.md` house style and publishes each to **both Instagram and YouTube** at its `SHORTS_POST_TIMES`
slot. The old Instagram-only reels pipeline (`run_daily.sh`) is retired and its launchd job unloaded.

- Videos render one day and publish on the **next** day's slots (`SHORTS_PUBLISH_OFFSET_DAYS=1`). Rendering 4
  takes ~6 h, so a same-day 09:00 slot was unreachable. The pipeline therefore always holds a day of finished
  stock, and a slow or failed run does not leave a gap.
- Narration is Hinglish, ~45% English words, written in Devanagari. Gate it with
  `python3 shorts/langmix.py OUT/0N_<slug>.json`.
- Each spec needs `platforms.youtube` **and** `platforms.instagram`; the publisher refuses a Reel with no caption.

## Voiceover
Each scene in a spec has a `voice` line. Scenes stretch to fit the narration, and the music dips under it.
- **ElevenLabs (default):** set up by the ElevenLabs onboarding (`npx -y @elevenlabs/cli@latest onboard init`), which saves `ELEVENLABS_API_KEY` to `.env` / `.env.local` in this folder. Change the voice with `ELEVENLABS_VOICE_ID` in `config.env`.
- If no key is found, reels fall back to the Mac's built-in voice (`SAY_VOICE`). Set `TTS_BACKEND=say` to always use it, or `none` for music only.
- To voice one reel by hand: `bash make_voice_sample.sh out/DATE/x.json`

## Publishing with Zernio
Reels can go out through [Zernio](https://zernio.com), which fans one post out to the connected social accounts, instead of the local Edit app.

Switch backends in `config.env`:
```
UPLOAD_BACKEND=zernio      # or metapilot (default, the Edit app project)
ZERNIO_PLATFORMS=          # e.g. instagram,youtube — empty means every active account
POST_TZ=Asia/Kolkata       # the timezone POST_TIMES is written in
```
The API key is a secret, so it lives in `.env` as `ZERNIO_API_KEY`, never in `config.env`.

By hand, through the `./zernio` wrapper (it loads `config.env` + `.env` for you):
```bash
./zernio accounts                                        # what's connected (read-only)
./zernio post out/DATE/02_paper_x.mp4                    # a Zernio draft — nothing goes out
./zernio post out/DATE/02_paper_x.mp4 --mode schedule    # at this slot's POST_TIMES, in POST_TZ
./zernio post out/DATE/02_paper_x.mp4 --mode publish     # goes out immediately
./zernio post out/DATE/02_paper_x.mp4 --dry-run          # print the payload, call nothing
```
The caption comes from the reel's `*.caption.txt`, the title from its spec, and the `0N_` prefix picks the
`POST_TIMES` slot. Each successful post appends a line to `history.jsonl`. Accounts have to be connected
once in the Zernio dashboard before anything can post; `./zernio accounts` tells you if none are.

Implementation note: `engine/zernio.py` calls the REST API with stdlib `urllib`, like `reel_engine.py`,
rather than the `zernio-sdk` package — `setup.sh` and `make_voice_sample.sh` rebuild `.venv` with only
pillow + numpy, so a pip-installed SDK would be wiped on the next rebuild.

## Requirements at run time
- The Mac is awake (a missed run happens on the next wake).
- **Metapilot is open** — only when `UPLOAD_BACKEND=metapilot`. The run checks `127.0.0.1:19501` first and notifies you if it's down. With `UPLOAD_BACKEND=zernio` it checks for `ZERNIO_API_KEY` instead.
- Claude Code is logged in. The job uses your normal `claude` login.

## Editing the look
Everything visual is in `engine/reel_engine.py`. Scene types are `hook`, `card`, `list`, `flow`, `stat` and `cta`; see `specs/example_paper.json`.
To change a single reel, edit its JSON and re-render:
```bash
./reel out/DATE/02_paper_x.json --preview /tmp/p.png   # quick check
./reel out/DATE/02_paper_x.json out/DATE/02_paper_x.mp4
```
Fonts (Inter, Poppins; SIL Open Font License) are bundled in `fonts/`.
