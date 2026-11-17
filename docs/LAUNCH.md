# Launch checklist

Drafts below. **Read every one before posting** — they are written in first person as
Aman, and anything that is not true of you should be cut rather than softened.

Two rules they all follow, because launch posts that break them get ignored or mocked:

- **Every number must be checkable.** The figures in these drafts — 49 videos, 10 days,
  19 seconds, three dependencies — were measured, not estimated. If you change a claim,
  re-measure it. The fastest way to lose a Show HN thread is one number someone can
  disprove.
- **Lead with the specific thing, not the category.** "I shipped a tool that automates
  content" is a post nobody finishes. "My pipeline would have posted four videos at once on
  Linux and never errored" is one people read.
- **No hype words.** Not "revolutionary", "seamless", "effortless", "game-changing",
  "unleash", "empower", "supercharge". If a sentence would survive being written about any
  other project, it is not worth posting.

## Before you post anything

- [ ] PRs #2–#5 merged, `main` green on all six CI jobs
- [ ] **Decide on `legacy/`** — delete or keep (still open from PR #2)
- [ ] **Claim the PyPI name** and publish `0.1.0`, or the first link in every post 404s
- [ ] **Run `youtube` or `instagram` against the live API once.** Posting "it publishes to
      YouTube" while that path has never run is the one claim that could blow up in a
      comment thread
- [ ] Repo description set, topics added (below), social preview uploaded
- [ ] Create the 18 issues from `docs/seed-issues/` — a repo with no open issues reads as
      abandoned, and "good first issue" is the first filter a drive-by contributor applies
- [ ] Pin one issue asking for live-API reports
- [ ] Decide whether you want the attention this week: every post below generates questions,
      and an unanswered thread is worse than no thread

## GitHub setup

**Description** (350 char limit):
> Research, write, voice, render and publish short videos on autopilot — on your own machine, with your own keys. Python + ffmpeg, no browser, no GPU. Runs my own channel daily.

**Topics:**
```
video-generation  content-automation  youtube-shorts  instagram-reels  text-to-speech
ffmpeg  python  cli  self-hosted  plugin-architecture  tts  piper  ollama  video-editing
social-media-automation  creator-tools
```

**Social preview** (Settings → General → Social preview). 1280×640 PNG, and GitHub crops
the edges on some surfaces so keep anything important inside the middle 1100×520:

- Left: the wordmark, the one-line pitch, and `pip install creatoros-autopilot` in mono.
- Right: one rendered frame at an angle — `docs/assets/example-nexlore.jpg` scene 1 works.
- Dark background (`#0B0A1C`), one accent (`#0ED1D3`). No stock photography, no gradients
  behind text, nothing below 24px.

---

## Show HN

The highest-value post and the harshest audience. Post Tue–Thu, 08:00–10:00 ET. Be at a
keyboard for the next six hours; an unanswered Show HN dies.

**Title** (80 chars max — this is 74):
> Show HN: CreatorOS – self-hosted pipeline that writes, voices and posts short videos

**Body:**

> I run two small channels that post explainer videos. I wrote the pipeline that makes them
> a couple of weeks ago — research a topic, write a script as JSON, narrate it, render
> 1080×1920 with Pillow and ffmpeg, publish to YouTube and Instagram. It has put out 49
> videos over 10 days, unattended.
>
> It is young, and it only ever ran on my Mac. I spent the last stretch making it run
> anywhere, and the most interesting part was what that surfaced.
>
> The scheduler computed publish times with BSD `date -j -v+1d`, with `|| echo 0` as a
> fallback. On Linux that command does not exist, so the fallback fired, every slot compared
> as already past, and all four videos took the "publish now" branch. It did not error. It
> would have posted the entire day at once, every day, and the logs would have looked fine.
>
> That turned out to be the shape of nearly every real bug in it. A TTS backend handed a
> script its voice cannot pronounce exits 0 and writes a valid, near-silent file — measured,
> a Hindi clause is 3.05s in a voice that speaks it and 0.0116s in one that does not — so the
> video ships with the line missing and every later run reports "unchanged". A publisher that
> wrote files and posted nothing recorded the job as published, so the real publisher skipped
> it forever. None of these failed loudly. All of them were found by writing the test that
> asserts the property rather than the example.
>
> What it is now: a `pip install`, a CLI, and six plugin interfaces (LLM, TTS, research,
> publisher, style, gate). Every provider it ships registers through the same public entry
> point a third-party plugin would use — there is no built-in table consulted first, and a
> test reads the AST of every file in `core/` and fails if it names a provider. Three runtime
> dependencies: Pillow, NumPy, PyYAML.
>
> There is a free path that is actually free: Piper for voices, Ollama for writing, and a
> publisher that renders to a folder with the metadata you would have posted. `creatoros init`
> to a finished video takes 19 seconds on a clean machine with no key.
>
> AGPL-3.0, because a hosted version is the plan and I would rather not compete with a fork
> of my own core.
>
> Two things I will say before anyone finds them: the direct YouTube and Instagram publishers
> are written and unit-tested but have never run against the live APIs — I have no OAuth app
> and no Business account — and the test suite takes two minutes because it renders real
> video. Reports from anyone who runs those publishers are the most useful thing right now.
>
> https://github.com/Nexlore-AI/CreatorOS-Autopilot

**Expect, and have an answer ready:**
- *"Isn't this just AI slop at scale?"* — Do not get defensive. Say what the gates are for,
  that every factual video carries its sources, and that `containsSyntheticMedia` is set on
  every upload whose API has the field. Link the actual channel and let it be judged.
- *"Why AGPL?"* — Answer plainly: a hosted version is planned, and AGPL is the licence that
  matches that. Do not apologise for it.
- *"Why not ffmpeg filters / Remotion / After Effects?"* — Pillow and NumPy mean no browser,
  no GPU, no Node, and a frame is a function of the spec. It renders on a CI runner.
- *"Does it work?"* — The channel links are in the README. That is the answer.

---

## Product Hunt

Different audience: makers, not engineers. Lead with the outcome.

**Tagline** (60 chars): `Your channel posts daily. You do not touch it.`

**Description:**
> CreatorOS runs the whole loop on your own machine: it researches what is new in your
> niche, writes each video as a structured script, narrates it, renders a 1080×1920 video,
> and posts it to YouTube and Instagram on a schedule.
>
> Your keys, your machine, your brand — a brand kit is a YAML file with your name, colours
> and fonts. There is a free path that needs no subscription at all: local voices, a local
> model, and a mode that renders to a folder with the caption and tags ready to paste.
>
> It has put out 49 videos across my own two channels so far, unattended. It is a few weeks
> old — I am open-sourcing the thing I actually use, rather than a demo of it.

**First comment:** the honest-engineering angle from the Show HN post, shortened. PH
rewards a founder comment that sounds like a person.

---

## Reddit

**Read each subreddit's self-promotion rule first.** r/selfhosted and r/Python will remove a
post that reads as marketing, and a removal costs you that audience permanently.

**r/selfhosted** — *"I open-sourced the pipeline that runs my two video channels"*
> Everything runs locally. Three Python dependencies, ffmpeg, and optionally Ollama and
> Piper if you want it to cost nothing at all. It renders to a folder by default and only
> posts when you configure it to. Docker compose file included. AGPL.
>
> The part this crowd may care about: nothing phones home, there is no account, and the
> only state is a JSONL ledger in a directory you choose.

**r/Python** — lead with the engineering, not the product
> Six `Protocol` interfaces and `importlib.metadata` entry points for the plugin system.
> Every provider I ship registers through the same public entry point a third-party would
> use, and there is a test that reads the AST of every file in `core/` and fails if it names
> a provider — I wanted the plugin system to be load-bearing rather than decorative.
>
> Happy to talk about why the TTS protocol is one method, and why caching per-scene audio
> lives in core rather than in each backend.

**r/SideProject**, **r/artificial**, **r/ClaudeAI** — the honest-bug story is the post.
Lead with the `date -j` bug.

---

## X / Twitter

Thread. One idea per post, the bug first.

> 1/ My video pipeline had a bug that would have posted four videos at once, every day, on
> any machine that was not mine. It never would have errored. Here is how that happens. 🧵
>
> 2/ The scheduler computed publish times by shelling out to `date -j -v+1d` — BSD syntax,
> macOS only — with `|| echo 0` as a fallback.
>
> 3/ On Linux the command does not exist. The fallback returns 0. Every slot compares as
> already past. Every video takes the "publish now" branch.
>
> 4/ It does not fail. It posts the whole day at once and the logs look normal.
>
> 5/ I only found it because I wrote the test as a property — *no* future slot, at any
> offset, may ever decide "now" — instead of testing one example.
>
> 6/ That turned out to be the shape of nearly every real bug in the thing. A voice that
> cannot pronounce a script exits 0 and writes a silent file. A publisher that posts nothing
> records the job as done. None of them fail loudly.
>
> 7/ The pipeline is open source now. It writes, voices, renders and posts short videos —
> 49 of them across my own two channels so far. Python + ffmpeg, no browser, no GPU.
> github.com/Nexlore-AI/CreatorOS-Autopilot

---

## LinkedIn

Plainer, no thread formatting, no emoji bullets.

> I open-sourced the pipeline that runs my two video channels.
>
> It researches a topic, writes the script, narrates it, renders a 1080×1920 video and posts
> it to YouTube and Instagram — unattended. 49 videos over its first 10 days.
>
> Making it run on machines other than mine was the interesting part. The scheduler used a
> macOS-only date command with a silent fallback; on Linux it would have published an entire
> day of videos at once without logging an error. Several other bugs had the same shape: a
> voice that could not pronounce a script exited cleanly and wrote a silent file; a publisher
> that posted nothing recorded the job as complete.
>
> I think that is the more useful thing to write about than the feature list. The failures
> that matter in automation are rarely the ones that crash.
>
> AGPL-3.0, three runtime dependencies, and a mode that costs nothing to run.

---

## Dev.to

Long form, and the one with the longest tail. Make the bug the article.

**Title:** *The bug that would have posted four videos at once, and why it never errored*

**Shape:**
1. The pipeline, in one paragraph — enough context, no feature list.
2. The `date -j` line, quoted exactly, with the fallback.
3. What happens on Linux, step by step. Show `date: invalid option -- 'j'`.
4. Why no test caught it: there were none, and the logic was in Bash where it could not be.
5. The property test that would have — `no future slot may ever decide "now"`, swept across
   every offset and slot time, not one example.
6. Three more bugs of the same shape, briefly: the silent audio clip, the practice run that
   consumed a real one, the voice directory that depended on the working directory.
7. The pattern, stated once: an error path that swallows a failure and carries on is more
   dangerous than one that crashes, because nothing is there to notice.
8. Link, one line, at the end.

**Tags:** `python`, `testing`, `opensource`, `automation`

---

## After

- [ ] Answer every comment for 48 hours. A dead thread is worse than no thread.
- [ ] Any bug reported in the first week goes straight into `docs/seed-issues/` or an issue
      with a reproduction. Public, fast triage is what makes people contribute.
- [ ] If the YouTube or Instagram publisher gets its first live run from someone else, say
      so publicly and credit them. That is the gap in the project and closing it in the open
      is worth more than quietly fixing it.
