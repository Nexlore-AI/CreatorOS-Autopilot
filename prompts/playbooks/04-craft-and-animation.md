# Craft: what makes it look made rather than generated

Adapted from the AdamX video house rules (`video-agent-example1.md` in this folder) and
from watching the two finished Chronus films frame by frame.

## The rules the renderer already enforces

- **Frame 0 is a finished frame.** Scene 1 is fully drawn at t=0; no fade from black.
- **Text never moves while it holds.** Sub-pixel drift makes type shimmer frame to frame
  and it is instantly visible. Elements animate *in* — word by word, with a slight
  overshoot — and then hold perfectly still.
- **Life comes from non-text layers.** Three parallax bands of particles, two drifting
  glows, and a vignette move continuously so a held scene is never frozen. Never add life
  by scaling or drifting the text.
- **Scenes cross-fade.** The outgoing scene fades out *while* the incoming fades in on top
  over 0.32s. (Before 2026-11-07 they did not overlap: each scene faded out before the
  cut and the next faded in after it, leaving about half a second of near-empty screen at
  every transition — roughly 2.5s of dead air in a six-scene reel.)
- **Depth.** Cards and list rows sit on a soft spread shadow with a hairline top
  highlight; the payoff number sits in its own radial glow.
- **Audio is mastered** to −14 LUFS / −1.5 dBTP at 48 kHz, which is what Instagram and
  YouTube normalise to. Unmastered audio reads as quiet and amateur next to other reels.

## The rules you have to hold yourself

**Few big things.** A scene is a few large elements on one grid, readable on a phone held
at arm's length. Many small cards lose to three big ones every time.

**On-screen text is labels, never the spoken sentence.** If the voice and the screen say
the same words, cut one. The picture carries the structure; the voice carries the story.

**The picture follows the voice, in the same order.** What is on screen is what is being
said, as it is said.

**A colour keeps one meaning.** Cyan is the key term. Green is a good number. Red is a bad
number. Amber is caution. Never use green merely because a bar looks nice in green.

**Each scene does something a slide could not.** If a scene would work as a PowerPoint
slide, it is not earning its seconds.

**No numbered scene labels.** "Step 1 of 5" in a corner reads as a deck. The `label` field
is for a story beat ("The problem", "The result"), not a position counter.

**On-screen numbers match the spoken ones.** If the voice says "thirty-one percent", the
screen says 31%, not 30.9%.

## Spec constraints that come from real layout failures

| Field | Limit | Why |
|---|---|---|
| `card.meta` label | **one short word, ≤8 chars** | longer labels collide with the value column — this broke three scenes on 2026-11-07 |
| `card.subtitle` | ≤12 words | runs into the caption-safe area |
| `list.items` | ≤8 words each, 3 items | 4 items push past `SAFE_BOTTOM` |
| `flow.nodes` | ≤3 words each | node pills are sized to their text |
| `hook.lines` | ≤4 lines | more and the hook cannot land in 3 seconds |

The renderer prints `WARNING:` for overflow and the daily run must open the preview PNG
and look at it. A clean ffprobe says nothing about whether it is readable.

## The two things still missing, in priority order

**1. A persistent stage instead of scene replacement.** This is the biggest gap between
our reels and the reference films. The Chronus comparison video holds *one* two-panel
layout for most of its length and progressively populates it — the generic answer greys
out, the other panel fills with the person's context, then the conversation appears. One
stage, built up.

Our renderer throws away the entire screen six times in 38 seconds. Six unrelated layouts
in sequence is the definition of a narrated slide deck, and no amount of easing fixes it.
The fix is a scene type that persists across beats and accumulates — a `stage` that holds
two columns and reveals rows into them on cue.

**2. Silence as punctuation.** Measured on the reference film: the median gap between
narration lines is 0.71s, but the two gaps before the two major turns are **1.82s and
2.42s**. The pause is what makes a turn feel like a turn.

Our renderer uses a flat `VOICE_LEAD = 0.15` / `VOICE_TAIL = 0.45` everywhere, so every
beat arrives with identical spacing and nothing feels emphasised. The fix is a per-scene
`lead` field, defaulting to 0.15 and set to 1.2–1.8 on the scene that carries the turn
(usually `flow`) and on the payoff (`stat`).

Until both are built, a reel can be well written and still feel evenly paced and
deck-like. Write to that limitation rather than pretending it is not there.

## Technical targets

1080×1920 · 30 fps · h264 yuv420p · AAC 48 kHz · −14 LUFS, −1.5 dBTP · 25–45s.
Anything over 45s fails the gate; at ~146 wpm that means 85–90 narration words.
