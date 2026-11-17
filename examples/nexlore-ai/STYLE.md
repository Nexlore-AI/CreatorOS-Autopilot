# Nexlore AI · house style (v3)

This is the standard set by "Transformer From Scratch in 4 Pieces (Hindi)", the first Short Aman approved.
Every video follows it. The engine (`creatoros_autopilot/render/engine.py`) already draws the look; this file covers what
the script writer controls.

**Since 2026-11-16 this is the only pipeline, and every video goes to BOTH YouTube (as a Short) and
Instagram (as a Reel) — the same MP4, the same slot.** So each spec needs `platforms.youtube` *and*
`platforms.instagram`; the publisher refuses to post to Instagram without a caption. The old
Instagram-only reels pipeline (`creatoros_autopilot/legacy/reel_engine.py`, `legacy/daily_run.md`) is retired.

## Format
- 1080x1920, **45–70 s**. The narration sets the length (each scene = its voice line + 0.65 s).
  This suits both surfaces: YouTube Shorts allows up to 3 min, Instagram Reels up to 90 s.
- **6–8 scenes**: `hook` → optional `stack` (what we'll cover) → 2–4 `section`/`stat`/`points` → payoff (`predict`, `stat` or `points`) → `cta`.
- One idea per Short. If it needs more, make it a series ("Part 1 of 3") and pitch the rest.
- Reference specs: `examples/specs/transformer-from-scratch.json` (approved) and `examples/specs/kv-cache.json`.

## Look (fixed by the engine, do not imitate in text)
Night background with grid, stars and glows · header: icon + "Nexlore AI" + tag pill + gradient progress line ·
white headlines in Inter Display Black · **cyan only for the one key word or number** · Haze for secondary text ·
glass cards · code in cyan mono chips · gradient for the big number and the winning bar.
Set `"tag"` to `AI EXPLAINED · <TOPIC IN CAPS>`, or `NEW IN AI · <TOPIC>` for news. Keep it under 34 characters.

## On-screen text: English, short
- Hook: 2–4 lines, at most 14 characters each. One line in cyan (`accent`) or underlined (`underline`), plus an optional `big` number/word.
- Titles: at most 22 characters. Body: one sentence, at most 2 lines (about 90 characters).
- Labels like `PIECE 2 / 4` or `THE CATCH` give progress. Use them.
- Code: 1–2 lines, at most 38 characters per line, real and runnable-looking (PyTorch style).
- Every section gets a **visual** (vectors, attention, flow, bars, steps, compare). A slide that is only text needs a reason.

## Narration: Hinglish as this audience actually speaks it

**Script is always Devanagari. Vocabulary is roughly half English.** These are two separate
rules and they do not conflict — see below.

### 1. Devanagari script, always (a TTS requirement, not a language choice)
Write **every word in Devanagari, including the English ones**: मॉडल, लॉस, स्टेप, ट्रेनिंग,
जीपीयू, अटेंशन. Latin script makes ElevenLabs switch to an English accent mid-sentence, which
breaks the single Indian-accent voice. Never put Latin letters in a `voice` line.

### 2. About 45% of the words should be English (target band 38–55%)
The audience is Gen-Z Indian devs. They say "loss", "step", "slow", "blow up" — not "पहाड़",
"कीमत", "रेंगती". The 2026-11-15/16 Shorts measured **23% English**, which read as formal
and textbook-ish. Aman asked for more English on 2026-11-16.

- **English** (in Devanagari) for: every tech term; concept nouns (पार्ट, डायरेक्शन,
  डिफ़रेंस, कर्व, साइज़, नंबर); verbs this audience uses in English (चेक करो, रन करो, फ़िक्स करो,
  स्किप करो, मल्टिप्लाई करो, रिपीट करो); adjectives (स्लो, फ़ास्ट, बिग, स्मॉल, चीप, ट्रिकी);
  and sparingly, Gen-Z markers (बेसिकली, एक्चुअली, सुपर).
- **Hindi** keeps the skeleton: है / होता है / करता है, तो, लेकिन, क्योंकि, यही, अब, और the
  teaching moves समझो, देखो, सोचो, ध्यान दो. Do not translate these away — they are the
  grammar, and chasing a higher English number by replacing them stops sounding like speech.
- **Never** formal or literary Hindi: अनुसरण, परिवर्तन, गणना, प्रशिक्षण, स्मृति, दिशा, हिस्सा,
  कीमत, पहाड़. `langmix.py` carries the full swap table.

Check every spec and fix it until it passes:
```
python -m creatoros_autopilot.quality.langmix OUT/0N_<slug>.json
```
It prints the ratio per scene, flags formal-Hindi words with the English word to use instead,
and exits non-zero while anything is off. A scene below 38% is usually one or two nouns away.

Before → after, from the 2026-11-16 learning-rate Short:
> 11%: पहला **हिस्सा**, ग्रेडिएंट। लॉस एक **पहाड़** है, ग्रेडिएंट ऊपर की **दिशा** बताता है, तो हम **उल्टी तरफ़** चलते हैं।
> 44%: पहला **पार्ट**, ग्रेडिएंट। लॉस एक **कर्व** है, ग्रेडिएंट **अपहिल डायरेक्शन** बताता है, तो हम **ऑपोज़िट साइड** चलते हैं।

### 3. The rest
- Numbers as English words in Devanagari, which is how people say them: ज़ीरो पॉइंट ज़ीरो ज़ीरो वन, टू एक्स, सेवन पॉइंट फ़ाइव.
- Formulas are read aloud: "सॉफ्टमैक्स ऑफ़ क्यू के-ट्रांसपोज़, ओवर रूट डी, टाइम्स वी".
- One voice line per scene, 8–30 words. The whole video is about **90–120 words**.
- The line says what the screen shows, in the same order the screen builds it.
- CTA: "… नेक्सलोर एआई को फ़ॉलो करो।"
- Voice: Riya Rao (`tts.voice_id` in creatoros.yaml). The narration gets the voice, and the music is ducked under it automatically.

## Story
- The hook names a habit or a question the viewer has, then promises a countable payoff ("4 pieces", "one trick").
  Write 5 hooks, score them with `.claude/skills/yt-script/hookscore.py`, use the best one. It must be WORKABLE (55+).
- Payoff scene: deliver the promise explicitly ("That's the core of GPT.").
- The CTA teases the next Short in the series ("GQA explained next.") and only after that asks for the follow.
- **No invented numbers.** Concepts can come from knowledge. Any figure, benchmark or news claim needs a source
  you opened in this run, listed in `"sources"`. Illustrative bars and weights carry no numbers on screen.

## YouTube metadata (`platforms.youtube`)
- **title**: 40–60 characters, `<Topic>: <promise> (Hindi)` or `<Question>? (Hindi)`. Lint with
  `.claude/skills/yt-package/title.py` (score 80+, no truncation of the subject on mobile).
- **description**: line 1 = what they get, in Hinglish (Roman + Devanagari is fine), with the search words in it.
  Then 1–3 English lines. Then `#shorts` + 3–4 hashtags (`#hindi` always).
- **tags**: 8–12, real search phrases (include one "<topic> in hindi" and one "<topic> explained").
- Category, made-for-kids=false and synthetic-media=true are set by the publisher.

## Instagram metadata (`platforms.instagram`)
Required on every spec. `creatoros_autopilot/providers/publish/zernio.py` exits rather than post a Reel with an empty caption,
because a captionless Reel gets no reach and cannot be found.

- **caption**: line 1 is the hook as a scroll-stopper — the same promise as the video's hook line,
  written as speech, not a title. Then 2–4 short lines giving the actual payoff (someone who reads
  only the caption should still learn the thing). Hinglish is right here: Roman Hindi + English reads
  naturally on Instagram, and unlike the narration there is no TTS constraint, so **write the caption
  in Roman script, not Devanagari**.
- Close with a one-line CTA and then hashtags: **8–12**, on their own line. Mix broad
  (`#ai #machinelearning #coding`) with specific (`#learningrate #pytorch`) and
  always include `#hindi` and `#reels`.
- Keep the whole thing under 2,200 characters (the publisher truncates past that).
- Do **not** reuse the YouTube description verbatim: it opens with search keywords for YouTube's
  index, which reads as flat on Instagram. The facts are the same; the first line is not.
