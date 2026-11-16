# Platform metadata: one video, two packages

The same MP4 goes to Instagram Reels and YouTube Shorts. **The metadata must not be the
same**, because the two platforms find videos in completely different ways.

- **Instagram** has no title field. Discovery runs on the caption's opening words, the
  audio, and a small number of hashtags, and ranking leans on completion rate. The
  caption is a *hook that continues the video*.
- **YouTube** has a title field that is indexed by YouTube search and by Google, and
  ranking leans on click-through and watch time. The title is a *search result*.

Writing one caption and posting it to both wastes the title field on YouTube and wastes
the search intent on Instagram.

## Hard limits (enforced by `publish_zernio.py`)

| Field | Instagram | YouTube |
|---|---|---|
| Title | *no such field* | **100 max**, aim 50–70 |
| Caption / description | **2,200 max** | **5,000 max** |
| Visible before the fold | ~125 chars | ~157 chars |
| Hashtags | 3–5, in the caption | top-level `tags`; 100 per tag, 500 combined |
| Reel/Short detection | vertical | vertical — auto-detected, no flag, `#shorts` not needed |

The character caps are what the publishing API actually rejects on. The fold numbers are
directional and worth designing to, not worth measuring to the character.

## Instagram caption recipe

1. **Line 1 is the hook continued, in Roman Hinglish, inside 125 characters.** Not the
   title of the topic. It should read like the next sentence after the video's first line.
2. A blank line, then 2–3 short paragraphs that pay something off the video did not have
   room for — the catch, the limitation, the second-order consequence.
3. One `Paper:` / `Source:` line with the primary source.
4. A one-line CTA naming the series.
5. **3–5 hashtags, specific.** Not 10. `#MixtureOfExperts` beats `#AI #ML #Tech #Coding`.
   Never invent an account handle.

Roman Hinglish throughout — the audience types in Latin script, and Devanagari in a
caption hurts reach.

## YouTube package recipe

**Title (50–70 chars):** front-load the searchable noun, then the surprise.
- Good: `Mixture of Experts explained: why a 30B model uses only 3B`
- Bad: `That 30B model you ran only used 3B of itself. On purpose` — a great *hook*, a
  weak *title*, because nobody searches for it.
- The reel's hook line is the right **Instagram** opener and the wrong **YouTube** title.
  Derive the title from the topic, not from the hook.
- English-leaning. Hinglish in a title cuts search reach; the audio is already Hinglish.

**Description:**
1. First ~157 characters restate the value in plain English with the key term in it —
   this is what shows under the player and what Google quotes.
2. Then the Hinglish summary (the Instagram caption body works here).
3. Then `Sources:` with full URLs, one per line. YouTube descriptions are indexed, so
   real source links do double duty as credibility and SEO.
4. Then the series line and channel CTA.

**Tags:** 8–12, specific, no duplicates, under 500 characters combined. Include the model
or paper name, the concept, and the series name.

## Where this lives in the spec

The script agent writes both packages into the spec. Do not make the publisher guess.

```json
{
  "spine": "the-cheap-trick-that-won",
  "platforms": {
    "instagram": {
      "caption": "Poora AI benchmark chalana mehenga hai...\n\n...\n\nPaper: arXiv 2611.00883\n\n#AIResearch #Benchmarks #NexloreAI"
    },
    "youtube": {
      "title": "Benchmark compression: when a random subset is almost enough",
      "description": "A new Carnegie Mellon paper shows a random subset of an AI benchmark tracks the full run closely - and where that breaks.\n\n<Hinglish body>\n\nSources:\nhttps://arxiv.org/abs/2611.00883\n\nRoz ek naya AI paper. Nexlore AI.",
      "tags": ["BestRand", "benchmark compression", "AI research", "LLM evaluation", "Carnegie Mellon", "Nexlore AI"]
    }
  }
}
```

`publish_zernio.py` uses `platforms.instagram.caption` and `platforms.youtube.*` when
present. When absent it falls back to the legacy single `caption` plus a title derived
from the hook — which works, but produces the weak-title case above, so write both.

## The check before publishing

- Does the Instagram first line stand alone inside 125 characters?
- Does the YouTube title contain the term someone would actually type?
- Are they genuinely different, or is one a copy of the other?
- Do the YouTube description's first 157 characters make sense with no video attached?
- Are the sources in the YouTube description as full URLs?
