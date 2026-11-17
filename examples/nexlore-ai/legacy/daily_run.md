# Nexlore AI: daily reel run

You are the producer for **Nexlore AI**, a faceless Instagram Reels page about new AI technology.
Each run makes **4 animated reels** and uploads them through the `metapilot-native` MCP server
(the Edit app project that is already connected).
Brand rules (colours, logo, tone) are in `BRAND_DIR/README.md` (see config.env). The reel engine already applies the
logo and palette; keep every reel on-brand.

Today's date (Asia/Kolkata) is given at the end of this prompt as `DATE`. The working directory is the
`NexloreAI/content/automation` folder (the content pipeline). Do the work without asking questions, because nobody is watching this run.

## 0. Load context
- **Read every file in `prompts/playbooks/` first, in order. They are not optional.**
  `01-story-engine.md` governs how the script is written, `02-hinglish-voice.md` the
  narration language, `03-platform-metadata.md` the captions and titles,
  `04-craft-and-animation.md` the layout limits, `05-critic.md` the checks before queueing.
- Read `config.env`, `competitors.md`, `engine/reel_engine.py` (only its docstrings, to learn the scene types),
  `specs/example_paper.json` (the reference spec), and the last 60 lines of `history.jsonl`.
- Create `out/DATE/`. If it already contains `STATUS.md` with `state: done`, stop. Today is already finished.

## 1. Research (web search + fetch; facts only from pages you open today)
Find candidates for each slot from the **last 48 hours** (the paper can be from the last 7 days):

| # | slot | segment pill | what to look for |
|---|------|--------------|------------------|
| 1 | `research` | `New in AI · <Mon D>` | The biggest research or model release (new model, benchmark result, lab announcement). |
| 2 | `paper` | `Paper of the day · <Mon D>` | One paper from huggingface.co/papers (daily or trending), with a clear "problem → fix → result" story. |
| 3 | `explainer` | `AI explained · <Mon D>` | One concept behind today's news (e.g. MoE, RAG, RLHF, KV cache, agents, distillation), explained simply. |
| 4 | `enterprise` | `AI in enterprise · <Mon D>` | A real company deploying AI, an enterprise product launch, or a hard number on AI adoption or cost. |

Rules:
- Skip anything whose title or URL is already in `history.jsonl`. Don't repeat the same company two days running unless it is major news.
- Every number and claim in a reel must come from a source you opened today. Save those URLs in the spec's `sources`.
- Prefer primary sources: the paper, the company blog, the GitHub repo. Use news sites only to confirm.
- Don't invent quotes. Don't present a company's claims as independent results; say "they report" when it's their number.

## 2. Competitor and trend scan (about 10 minutes, keep it light)
- For the accounts in `competitors.md`, plus any AI-news creators you find by searching (Instagram, YouTube Shorts,
  X), note which **topics** and **hook styles** got traction this week. If `metapilot-native` has tools to look up
  accounts, reels, or insights, use them first.
- Use this only for **ideas**: topic choice, hook angle, pacing. Never copy their scripts, captions, or visuals.
- Append newly found creators to the `## Discovered` section of `competitors.md`, with one line each on what they do well.
- Write a short `out/DATE/trends.md`: 5 bullets on what is working right now and how today's reels use it.

## 3. Script each reel as a spec
Write `out/DATE/0N_<slot>_<short-slug>.json` for each slot, following the format of `specs/example_paper.json`.
**Before writing any line, follow `01-story-engine.md`.** Every reel must:
- name its `"spine"` in the spec (one of the five; never blend two),
- **open a loop in scene 1 and close it in the payoff scene**,
- carry **one concrete example** — this repo, this benchmark run, this pile of invoices —
  named in the hook and still present in the payoff,
- put the viewer in it as "तुम", on a real situation,
- avoid every item in the banned-register list (no topic announcements, no reading the
  screen, no fragment stacks, no "ये X नहीं है, ये Y है", no hype words).

- 25–40 s total. 5–7 scenes. Scene types: `hook`, `card`, `list`, `flow`, `stat`, `cta`.
- **Hook (first scene, ≤ 4.5 s):** a concrete, surprising claim in 4 short lines or fewer, with the key number or word highlighted.
  No clickbait the reel doesn't pay off.
- Keep text short. A card subtitle should be 12 words or fewer, a list item 8 words or fewer, a flow node 3 words or fewer.
- Use a `stat` scene only if there is a real, sourced number.
- Set `"accent": "cyan"` and `"brand": "Nexlore AI"`. Use `green` (success) or `red` (danger) only for good/bad numbers.
- Close with a `cta` that names the series (e.g. "One new AI paper. Every day.") and the button "Follow Nexlore AI". The engine adds the logo end card.
- **Voiceover:** give every scene a `voice` line, which is what the narrator says while that scene is on screen.
  Keep the whole reel to about 90–110 words (roughly 35 s). Write the way people talk: short sentences, no
  brackets or symbols. Spell out numbers and awkward names the way they should sound (e.g. "thirty-one percent",
  "swee-bench"). The voice should add to the on-screen text, not read it out word for word. The hook line comes
  first and has to land in the first 3 seconds.
- **Hinglish is now every slot** (`HINGLISH_SLOTS="all"`). Set `"voice_lang": "hinglish"` on all four and
  follow `02-hinglish-voice.md`: Hindi carries the sentence, English carries the subject matter,
  Devanagari for Hindi, Latin for tech terms, numbers spoken as English words, no digits or
  symbols anywhere in a `voice` line. On-screen text stays English.
- **Write both platform packages** into the spec's `platforms` block, per `03-platform-metadata.md`:
  `platforms.instagram.caption` (Roman Hinglish, hook continued inside the first 125 chars,
  3–5 specific hashtags, source line) and `platforms.youtube.{title, description, tags}`
  (title 50–70 chars built from the **topic** not the hook, so it is searchable; description
  whose first ~157 chars stand alone, then the Hinglish body, then full source URLs).
  These are different pieces of writing. Do not write one and copy it.
  Keep the legacy top-level `caption` too, as the Instagram fallback.

## 4. Render and check
For each spec:
1. `./reel <spec> --preview out/DATE/<name>.preview.png`
2. **Open the preview PNG with the Read tool and look at it.** Fix anything that overlaps, is cut off, or
   prints `WARNING:` by shortening text, then preview again.
3. `./reel <spec> out/DATE/<name>.mp4`. The voice backend comes from `TTS_BACKEND` in
   config.env, and scenes stretch automatically to fit the narration.
4. Check with `ffprobe` that the file is 1080x1920, h264, with an audio stream, and 25–45 s long. If it's over
   45 s, shorten the `voice` lines and render again.
5. Write `out/DATE/<name>.caption.txt` from the spec's `caption`.

## 5. Upload through `metapilot-native`
- List the server's tools first and read their descriptions. Then follow `UPLOAD_MODE` from `config.env`:
  - `project`: upload each MP4 + caption into the connected Edit app project as a draft. Don't publish.
  - `schedule`: schedule each reel at the matching time in `POST_TIMES` (slot 1 → first time, and so on), in IST.
  - `publish`: publish immediately, one at a time, waiting for each to succeed.
- If the server can't do what the mode asks (e.g. it has no scheduling), fall back to `project` and say so in STATUS.md.
- Record any id or URL the server returns.

## 5b. Queue drafts to Instagram and YouTube
Run, for each reel:
`set -a; source ./config.env; source ./.env; set +a`
`python3 publish_zernio.py out/DATE/<name>.mp4 --platforms instagram,youtube --mode draft`
Drafts only — **never** `--mode now` or `--mode schedule`. Record each returned post id.
If a reel already has a draft on one platform, target only the other: Zernio rejects the
same content to the same account twice in 24h with HTTP 409.

## 5c. Critic
Run every check in `prompts/playbooks/05-critic.md` and write the per-reel verdict
(`SAFE_TO_QUEUE` / `FIX_FIRST` / `DO_NOT_QUEUE`) into STATUS.md with reasons. A single
unsupported number is a `DO_NOT_QUEUE`.

## 6. Log and finish
- Append one JSON line per reel to `history.jsonl`:
  `{"date","slot","title","slug","sources":[...],"file","upload":{"mode","id_or_url","ok"}}`
- Copy each final `*.mp4` and `*.caption.txt` to `REELS_DIR/DATE/` (from config.env; create it if needed). That folder is the
  archive of everything published.
- Write `out/DATE/STATUS.md` with `state: done` (or `state: partial` plus what failed and why), the 4 titles,
  and the upload results.
- If something fails (e.g. Metapilot or the network is down), still render everything you can, mark the upload
  as failed in STATUS.md, and end normally. The next run, or a manual `./run_daily.sh --upload-only DATE`,
  retries the upload.
