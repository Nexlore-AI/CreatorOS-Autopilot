# The critic: what to check before anything is queued

You built it, so you are not the judge. Run every check below and write the results into
`out/DATE/STATUS.md`. A clean ffprobe says nothing about quality.

## 1. Measure

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,codec_name \
        -show_entries format=duration -of csv=p=0 FILE     # want 1080x1920 h264, 25-45s
ffmpeg -v error -i FILE -af loudnorm=print_format=json -f null -   # want about -14 LUFS, TP under -1
ffmpeg -v error -i FILE -vf blackdetect=d=0.3 -f null -            # want no black frames
ffmpeg -v error -i FILE -af silencedetect=noise=-40dB:d=1.2 -f null -
```

A freeze during a text hold is correct, not a fault. Flag it only when *nothing at all*
moves for more than 6 seconds — and fix that with a non-text layer, never by drifting the
text.

## 2. Look at it

Open the preview contact sheet **with the Read tool and actually look**, then judge:

- Would a stranger call this a video, or a narrated slide deck?
- Is any text cut off, overlapping, or past the caption-safe area?
- Do two scenes in a row share a layout?
- Is the smallest text readable on a phone?
- Does the screen show what the voice is saying at that moment?
- Does one colour carry more than one meaning?

Fix anything the renderer printed as `WARNING:`, then preview again.

## 3. Story check (from `01-story-engine.md`)

1. What loop does scene 1 open, as a question?
2. Which scene closes it — and does it close *that* question?
3. What is the one concrete example, and is it in both the hook and the payoff?
4. Which spine is it, and does every beat serve it?
5. Where does the viewer appear as "तुम", on a real situation?
6. Does the narration avoid every banned register item?

## 4. Fact check — a blocker, not a nit

For every number, date, name and claim in the narration **and** both captions:

- It came from a source opened today and listed in `sources`.
- The source actually says it. Quote the sentence.
- If it is the authors' own claim, the narration says "उनका दावा है" / "they report".
- No invented quotes. No converted or rounded numbers that the source does not give.
- On-screen numbers match the spoken numbers.

One unsupported number means the reel does not get queued.

## 5. Language check (from `02-hinglish-voice.md`)

- No digits, `%` or symbols anywhere in a `voice` line.
- Hindi in Devanagari; English tech terms in Latin.
- No Latin-script Hindi.
- Hard names given a phonetic respelling.
- On-screen text is English.
- Captions are Roman Hinglish.

## 6. Metadata check (from `03-platform-metadata.md`)

- Instagram first line stands alone inside 125 characters.
- YouTube title ≤100 chars, contains the searchable term, and is **not** a copy of the hook.
- The two packages are genuinely different.
- YouTube description carries full source URLs.
- `containsSyntheticMedia: true` — our reels use an AI voice, so this is always true.

## 7. Verdict

Write one of these into STATUS.md per reel, with reasons:

`SAFE_TO_QUEUE` · `FIX_FIRST` · `DO_NOT_QUEUE`

Nothing publishes publicly without Aman asking, regardless of verdict.
