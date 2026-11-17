# Hinglish narration

**Every slot is Hinglish now** — research, paper, explainer, enterprise. Not pure Hindi,
not pure English. Every video in this preset is narrated this way.

The target is how an Indian engineer actually explains something to another engineer over
chai: the sentence runs in Hindi, the technical nouns stay in English, and nobody
translates "embedding" into Hindi because nobody says that out loud.

## The structural rule

**Hindi carries the sentence. English carries the subject matter.**

| Hindi carries | English carries |
|---|---|
| verbs, postpositions, sentence frame | technical nouns and product names |
| connectives — क्योंकि, इसलिए, लेकिन, और इसका मतलब | metrics, units, benchmark names |
| the stakes, the reaction, the emotion | company, lab and model names |
| the second person — तुम, तुम्हारा | the numbers (spoken as English words) |

Correct:
> "पूरा AI benchmark चलाना महंगा है. Random subset लगभग काफ़ी है. दिक्कत उसी 'लगभग' में है."

Wrong — English sentence with Hindi decoration:
> "Full AI benchmark चलाना is expensive, but random subset is लगभग enough."

Wrong — translated tech terms:
> "पूरा कृत्रिम बुद्धिमत्ता मानदंड चलाना महंगा है."

Wrong — Latin-script Hindi (the voice will mispronounce it):
> "Poora AI benchmark chalana mehenga hai."

## Script rules, because the voice reads literally

The narration string goes straight to text-to-speech. Spelling *is* pronunciation.

1. **Hindi words in Devanagari.** Latin-script Hindi gets read as English phonetics and
   comes out wrong. This is the single most common defect.
2. **English technical terms in Latin script.** `embedding`, `attention`, `RAG`,
   `benchmark`, `fine-tune`, `token`, `agent`, `Hugging Face`, `Carnegie Mellon`.
3. **Numbers spoken out, in English words.** `thirty-one percent`, not `31%` and not
   `इकतीस प्रतिशत`. Never leave a digit or a `%` in a `voice` line.
4. **Acronyms that read badly get a phonetic respelling** in the voice line only, with the
   real spelling kept on screen: `SWE-Bench` → `swee-bench`, `MoE` → `em-o-ee`,
   `LLM` → `el-el-em`, `RSA` → `aar-es-ay`.
5. **No symbols at all in a voice line:** no `%`, `→`, `≠`, `&`, `/`, `+`, brackets, or
   bullet characters. Write the word.
6. **Decimals and ranges in words:** `eighteen to thirty-one percent`,
   `one point two billion`.

## The register

- **"तुम", not "आप".** Peer to peer, not a lecture. Consistent across the whole reel.
- Short sentences. Hindi runs long when written formally; speak it instead.
- No Sanskritised formal Hindi (अत्यंत, तत्पश्चात, निष्कर्ष). No Urdu-heavy flourish either.
- One reaction line per reel, in Hindi, where the narrator is a person rather than a
  narrator: "यही हिस्सा मुझे पसंद आया." / "और यहीं मज़ा है."
- Mix ratio lands around 60% Hindi words / 40% English by count, but **never count** —
  write it as speech and it comes out right. If it reads like a translation, it is wrong.

## On-screen text stays English

Deliberate, not an oversight:
- It keeps the reel legible to the non-Hindi half of the audience, so one video serves
  both. The picture carries the meaning, the voice carries the warmth.
- Devanagari at headline weight needs a font the engine does not bundle, and the bundled
  Inter has no Devanagari glyphs — Hindi on screen would render as boxes.
- The voice adds to the on-screen text rather than reading it, which is a story rule
  anyway (see `01-story-engine.md`).

So: **screen in English, voice in Hinglish.** If a reel needs a Hindi word on screen,
flag it instead of rendering it.

## Captions

Captions are per platform (see `03-platform-metadata.md`). Both are **Roman-script
Hinglish** — Devanagari in a caption hurts reach, because search and the keyboard most of
this audience uses are Latin.

> "Poora AI benchmark chalana mehenga hai. Random subset lagbhag kaafi hai — dikkat usi
> 'lagbhag' mein hai."

Roman Hinglish is right for captions and wrong for narration. The caption is read; the
narration is spoken. Do not reuse one as the other.

## Known failure modes

- **Devanagari numbers.** `३१` is never correct anywhere.
- **Hindi plural on an English noun.** "agents के" not "agentों के".
- **Over-Hindi-ing a term with no real Hindi form.** "मॉडल" is fine; "प्रतिचित्रण" is not.
- **An English clause with one Hindi word dropped in.** That reads as a translation, not
  as speech.
- **Formal Hindi in the CTA.** "फॉलो करो" not "अनुसरण कीजिए".
- **A voice line that is 100% English.** Every line should have Hindi sentence structure;
  a fully English line breaks the register mid-reel. English-only is acceptable only for
  a direct quote.

## Voice selection

Pacing is a hard constraint, not taste: scenes stretch to fit narration and the gate
rejects over 45s. Measured on a 159-character Hinglish passage:

| Voice | voice_id | Pace | Effect |
|---|---|---|---|
| Tisha K — E-Learning Professor | `wmIQDUiVl74MwMw9Rpag` | 158.8 wpm | 7.5% shorter than Sarah |
| Sarah (stock, American) | `EXAVITQu4vr4xnSDxMaL` | 146.9 wpm | baseline — wrong accent for Hinglish |
| Riya Rao — E-Learning Narration | `ZI6I4a3UGgs1DXxqWjBV` | 125.7 wpm | 16.9% longer |
| Ananya — Deep, Calm and Steady | `fG9s0SXJb213f4UxVHyG` | 117.5 wpm | 25.1% longer — **overruns the 45s gate** |

A voice 25% slower than baseline needs the word budget cut from ~90 to ~70, not a longer
reel. Set the chosen voice as `tts.voice_id` in `creatoros.yaml`.
