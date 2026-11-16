# The story engine

The failure this file exists to fix: **the reels sound like someone reading facts aloud.**
Information arrives in a sensible order, each scene states something true, and nobody
feels anything. A storyline is not a better order of facts. It is a tension that opens
and closes, carried by one concrete thing the viewer can picture.

Read this before writing a single `voice` line.

## The one rule that fixes most of it

**Open a loop in scene 1. Close it in the payoff scene. Carry one concrete example the
whole way.**

A loop is a specific thing the viewer now wants to know and cannot guess:
- "Random subset लगभग काफ़ी है. दिक्कत उसी 'लगभग' में है." — *what is the catch?*
- "वो 30B model तुमने चलाया. उसने अपने आप का सिर्फ 3B इस्तेमाल किया." — *why would it do that?*

A loop is **not** a topic announcement. "आज हम MoE समझेंगे" opens nothing.

The example is one thing, named, followed from hook to payoff: **this** repo, **this**
benchmark run, **this** pile of invoices, **this** 30B model. Not "models", not
"companies", not "developers". When the payoff lands, it lands on the same object the
hook picked up.

## The five spines

Pick exactly one per reel. Name it in the spec as `"spine"`. Do not blend two.

### 1. The Suspicion → research, paper
Someone suspected the expensive way was wasteful. They tested it. Here is the number.
Here is what it changes for you.
> Beats: the suspicion · who tested it · what they did · the number · your move

### 2. The Hidden Bill → enterprise, cost, infra
You are already paying for something you cannot see on any invoice. Here is who charges
it. Here is what removes it.
> Beats: the invisible cost · who pays it · the mechanism that creates it · what kills it · your move

### 3. The Cheap Trick That Won → paper, benchmark, underdog result
Everyone built the elaborate thing. Someone tried the obvious dumb thing. It nearly
matched. Here is why that is possible.
> Beats: the elaborate consensus · the dumb attempt · the near-match · why it works · the catch

### 4. You Have The Wrong Picture → explainer, concept
You picture it one way. It does not work that way. Here is the picture that actually
sticks, and now you cannot unsee it.
> Beats: the wrong picture (stated generously) · the moment it breaks · the real picture · one example · your move

### 5. The Room Where It Was Decided → enterprise, adoption, careers
A team had to choose. Here is what they chose and what it cost them. Here is the part
you can copy.
> Beats: the decision on the table · the constraint · what they chose · what it cost or returned · what you copy

## Mapping beats onto the engine's scene types

The renderer has six scene types. Used in order they already form a story. Used as a
list of facts they form a slide deck. The difference is entirely in what you write.

| Scene type | Story beat | Write it as | Not as |
|---|---|---|---|
| `hook` | The loop opens | a stake the viewer can picture | a topic label |
| `card` | Who / what this is | the actor, named, with one credential | a bibliography entry |
| `list` | What is going wrong | **consequences** the viewer would feel | a feature list |
| `flow` | The turn — the mechanism | the thing that changes, step by step | a system diagram with no stakes |
| `stat` | The payoff — loop closes | the number the hook made you want | a statistic that happens to exist |
| `cta` | Handover | what the viewer does on Monday | "follow for more" alone |

**The `list` scene is where most reels die.** Three bullets of neutral description kill
momentum. Each item must be something that costs the viewer something. Not "re-reads
repo facts" but "वही repo, वही सवाल, हर session में दोबारा — और bill तुम्हारा."

## Audience connection: five devices, use two or three

1. **Second person, singular, specific.** "तुम" / "तुम्हारा", never "users" or "developers".
   And land it on a real situation: a sprint, an interview, a bill, a manager asking why
   the bill went up.
2. **One analogy from ordinary life**, and only one. It must survive being pushed on. A
   wrong analogy costs more trust than no analogy.
3. **The reaction line.** Exactly one line, somewhere in the middle, where the narrator
   reacts rather than reports: "और यही वाला हिस्सा मुझे पसंद आया." Earns the next ten seconds.
4. **Name the stake before the mechanism.** Nobody cares how attention works until they
   care what it costs them.
5. **The cost of not knowing.** End the hook or the problem beat on what it costs to keep
   doing it the current way.

## Banned: the register that makes it sound read-aloud

Never write these, in either language:
- **Topic announcements.** "आज हम समझेंगे", "चलो समझते हैं", "In this video", "इस reel में".
- **Definition before stakes.** Any reel whose second sentence defines a term has lost.
- **Reading the screen.** The `voice` line must not restate the on-screen text. The picture
  is labels; the voice is the story. If the voice and the text say the same thing, cut one.
- **Fragment stacks.** "Fast. Cheap. Simple." / "सस्ता. तेज़. आसान."
- **"It's not X, it's Y"** and its Hinglish twin "ये X नहीं है, ये Y है."
- **Reflex triplets.** Three of anything because three sounds complete.
- **Rhetorical question stacks.** One real question is fine. Two in a row is a podcast intro.
- **Hype words:** game-changer, revolutionary, insane, crazy, mind-blowing, कमाल, धमाका,
  गजब, "बदल देगा सब कुछ".
- **A poetic closing line.** The last line is an instruction, not a sentiment.
- **More than one number per `voice` line.** Extra numbers go on screen only.
- **Unearned certainty.** If the source says the authors report it, the narration says
  "उनका दावा है" / "they report". This is not optional; it is the factual rule.

## Length and pace

- Narration budget: **85–90 words per reel.** Scenes stretch to fit the voice, and the
  ffprobe gate rejects anything over 45s. At ~90 words the reel lands at 38–39s with a
  146 wpm voice. A slower voice needs fewer words, not a longer reel.
- One idea per line, built on the line before it. Use real connectives — "क्योंकि",
  "इसलिए", "और इसका मतलब" — because they are what make a sequence feel like a story
  instead of a list.
- Read every line in your head as speech. If it reads like a paper abstract, rewrite it.

## The test before you render

Answer these in `out/DATE/STATUS.md`. If any answer is weak, rewrite the script.

1. What loop does scene 1 open, in one question?
2. Which scene closes it, and does it close *that* question?
3. What is the one concrete example, and does it appear in both the hook and the payoff?
4. Which spine is this, and does every beat serve it?
5. Where does the viewer appear as "तुम", and is the stake a real situation?
6. If you cut the narration and kept only the picture, would it be incomprehensible?
   (It should be. If the picture alone tells the story, the voice is redundant.)
7. If you cut the picture and kept only the narration, would it still be a story?
   (It should be.)
