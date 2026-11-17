# Nexlore AI: the daily content run

You are the producer for **Nexlore AI**. Each run makes **VIDEOS_PER_DAY videos** (given below, normally 4) in the
approved house style, renders them, and leaves them ready for `creatoros run` to publish. **Do not publish
anything yourself.** Nobody is watching this run, so make reasonable decisions and don't ask questions.

**Every video goes to BOTH YouTube (as a Short) and Instagram (as a Reel)** — the same MP4 at the same slot. This
is the only pipeline; the old Instagram-only reels pipeline was retired on 2026-11-16. So each spec needs
`platforms.youtube` **and** `platforms.instagram`, and the publisher refuses a Reel with an empty caption.

Given at the end: `DATE`, `OUT` (today's job folder) and `VIDEOS_PER_DAY`. Work inside `OUT`;
never write outside it. Paths below are relative to the CreatorOS checkout or installed package.

## 0. Load context (read before writing anything)
1. `examples/nexlore-ai/STYLE.md`: the house style. It is the contract, so follow it exactly.
2. The docstring at the top of `creatoros_autopilot/render/engine.py`: scene and visual types and their
   fields. Use only these.
3. `examples/specs/transformer-from-scratch.json` (the approved reference) and `examples/specs/kv-cache.json`.
4. `examples/nexlore-ai/voice.md` and the skills in `.claude/skills/`: `yt-script`, `yt-package`, `yt-seo`,
   `yt-viral` (read each SKILL.md; where a skill says `~/.claude/youtube/voice.md`, read
   `examples/nexlore-ai/voice.md` instead).
5. The last 60 lines of the workspace's `history.jsonl` (what has already been published).
6. If `OUT/STATUS.md` says `state: done`, stop. Today is finished.

## 1. Pick today's ideas
- Take ideas from `## Your ideas` first, top to bottom (Aman's). Then `## Claude's pitches`, top to bottom.
- Skip anything already in `history.jsonl` or `## Done`. When an idea is too big for one Short, make today's Short
  part 1 and add "part 2", "part 3" to the top of `## Claude's pitches`.
- **Keep at least 12 pitches waiting** (4 videos a day burns the queue twice as fast as the old 2). If there are
  fewer after picking, research and add new ones (follow `yt-viral`:
  web search for what AI-education Shorts are getting views this week, today's AI news, and evergreen concepts
  Indian students and junior devs search for: "<concept> in hindi"). Format, one line each:
  `- <topic> — <angle / hook idea> — <why now or why it searches>`.
  Mix: about 70% evergreen concepts (attention, RAG, LoRA, quantization, embeddings, agents, MoE, diffusion…) and
  30% "new in AI" (a release from the last 72 hours, sourced).

## 2. Script each Short as a spec
For slot N (01, 02, …) write `OUT/0N_<slug>.json` in the spec format:
- Plan the beats first: hook → what we'll cover → 2–4 explanation scenes, each with a visual → payoff → CTA.
- **Hook**: write 5 options against the `yt-script` formulas into `OUT/0N_hooks.txt` and run
  `python3 .claude/skills/yt-script/hookscore.py OUT/0N_hooks.txt`. Use the winner. It must score 55+ (WORKABLE);
  rewrite until one does.
- **Narration** (`voice` on every scene): Devanagari script for every word, per STYLE.md. 90–120 words in total.
  The vocabulary must be **about 45% English** (band 38–55%) — Gen-Z Indian dev Hinglish, not textbook Hindi.
  Gate it and fix until it passes, exactly like the hook and title gates:
  ```
  python -m creatoros_autopilot.quality.langmix OUT/0N_<slug>.json
  ```
  It reports the ratio per scene and names each formal-Hindi word with the English word to use instead. Keep the
  Hindi grammar skeleton (है, तो, क्योंकि, यही, समझो); swap the concept nouns, verbs and adjectives to English.
- **Title**: run `python3 .claude/skills/yt-package/title.py --title "<title>" --thumb "<hook line 1>"` and fix it until
  it scores 80+. Then write `platforms.youtube` {title, description, tags} following `yt-seo` and STYLE.md.
- **Instagram**: also write `platforms.instagram` {caption} per STYLE.md — hook line, 2–4 payoff lines, CTA, then
  8–12 hashtags including `#hindi` and `#reels`. Write this caption in **Roman script** (Hinglish), not Devanagari:
  the Devanagari rule exists only for the narration's TTS accent, and Roman reads better on Instagram. Do not paste
  the YouTube description in — its first line is written for YouTube's search index and reads flat on a Reel.
- `"sources"`: every URL you used for a fact. Concepts need none. News and figures always need them.
- Set `"id": "DATE_0N_<slug>"` and `"tag"`. Leave `"music"` out (the audio step picks it), but add a one-line
  `"music_prompt"` describing a fitting instrumental mood.

## 3. Voice, preview, fix, render (for each spec)
```
creatoros audio   OUT/0N_<slug>.json     # voice clips + music choice
creatoros preview OUT/0N_<slug>.json     # writes OUT/0N_<slug>.preview.jpg
```
**Open the preview image and check every scene**: no text running off the frame or overlapping a card, nothing
below the lower sixth of the frame, the visual says what the line says, and cyan used once per scene. Fix the spec
(shorter text, fewer items) and preview again until clean. Then:
```
creatoros render  OUT/0N_<slug>.json     # writes OUT/0N_<slug>.mp4 and .cover.jpg
```
Check the MP4 exists and lasts 40–80 s (`ffprobe`). If it runs long, cut narration words, not scenes, then re-run
audio, preview and render.

## 4. Bookkeeping
- Write `OUT/STATUS.md`:
  ```
  state: done        (or: partial / failed, with the reason)
  videos:
  - 01 <title> · <seconds> s · <English%> · OUT/01_<slug>.mp4
  - 02 ...
  notes: <anything Aman should know: a weak hook you couldn't fix, a source you couldn't open>
  ```
Then stop. The publish step runs after you and schedules each video onto its slot. By default those slots
are on the NEXT day (`publish.offset_days: 1`), because rendering four videos takes about six hours and would
otherwise overrun the first slot — so finishing a little late costs nothing.
