---
title: Add an example preset for a language that is not yet represented
labels: good first issue, docs
---

Three example channels ship today — `examples/nexlore-ai` (Hinglish), `examples/ember-fitness`
(English) and `examples/claro-es` (Spanish). Add a fourth for a language you actually speak:
one folder with a `creatoros.yaml`, a `brand.yaml` and at least one pillar. No Python.

## Why this matters

Someone deciding whether this tool can run their channel looks for their own language first,
and the honest answer is "yes, if you can work out which settings matter". A preset is that
answer in a form they can copy. It is also the only thing that keeps the brand and language
work from quietly becoming "Hinglish with the names changed": every preset that is not
English or Hindi exercises a path that nothing else does.

## Where to start

Copy `examples/claro-es/`. It is the smallest of the three — a 30-line config, a brand with
only a name, tagline, CTA, palette and `language.primary`, and one pillar.

- `examples/claro-es/creatoros.yaml`, `brand.yaml`, `pillars/concepto.yaml` — your template.
- `examples/ember-fitness/creatoros.yaml` — read this one if you will publish to Instagram:
  it carries `offset_days: 0` and the comment saying why.
- `creatoros_autopilot/core/brand.py` — `load()` accepts exactly the keys
  `{name, tagline, logo, cta, palette, fonts, language}` and errors on anything else.
  `DEFAULT_PALETTE` is the complete list of colour names you may override; `_language()`
  is the whole of the `language:` block.
- `creatoros_autopilot/interfaces/brand.py` — the `LanguageMix` fields: `primary`,
  `secondary`, `ratio`, `tolerance`, `script`, `lexicon`.
- `creatoros_autopilot/core/pillars.py` — `_load_one()` holds the closed set of pillar keys
  and `CADENCES` the five valid cadences (`daily`, `weekdays`, `weekends`, `weekly`,
  `monthly`). An unknown key is a `PillarError`, not a warning.
- `creatoros_autopilot/core/settings.py` — `Settings.brand_path` and `Settings.pillars_dir`.
  Relative paths resolve against the config file, not the working directory, which is why
  `brand: brand.yaml` means the file beside it and `brand: claro` would mean a kit by name.
- `tests/test_brand.py` — `TestPresets.PRESETS` (line 147) and the two hard-coded
  `@pytest.mark.parametrize` lists (lines 26 and 172) are where your preset's name goes.
- `creatoros_autopilot/providers/tts/piper.py` — the module docstring explains what a voice
  model is (two files) and where voices come from.

## What done looks like

- [ ] `examples/<name>/creatoros.yaml`, `examples/<name>/brand.yaml`, and
      `examples/<name>/pillars/<at least one>.yaml`.
- [ ] A comment at the top of `creatoros.yaml` saying what this preset shows that the other
      three do not — the existing three each say this, and it is the reason they are kept.
- [ ] The preset name added to `tests/test_brand.py`: `TestPresets.PRESETS`, the parametrize
      at line 26, and the draft-mode parametrize at line 172. `pytest tests/test_brand.py -q`
      passes.
- [ ] `creatoros --config examples/<name>/creatoros.yaml doctor` loads the config and names
      your brand on the `brand` line. The only `x` lines are binaries or credentials you do
      not have locally — that is a pass, and doctor still exits 0.
- [ ] `publish.mode: draft`.
- [ ] `gates: []` unless the channel genuinely mixes two languages.
- [ ] No key-shaped value in any of the YAML. The pre-commit hook and CI both run
      `scripts/check_no_secrets.py` over `examples/**/*.yaml`.
- [ ] Brief and tagline written in the language, not translated from the Spanish ones.

An afternoon, most of it writing rather than configuring.

## Gotchas

**Do not add `gates: [langmix]` for a single-language channel.** The gate reads the brand's
`language` block: `LanguageMix.from_brand()` in
`creatoros_autopilot/providers/gate/langmix.py` returns `None` when there is no `secondary`
or no `ratio`, and `core/gates.py::run()` skips a gate that returns `None` rather than
failing it. That is the correct behaviour and
`test_a_single_language_preset_has_no_mix_to_gate` asserts it. The trap is a half-filled
language block — a `secondary` with no `ratio`, or the reverse — which reads like a
configured gate and silently measures nothing.

**If your channel does mix, the gate probably cannot measure it.** `LanguageMix.analyse()`
counts a word as secondary when it is Latin script *or* in the brand's lexicon. For two
Latin-script languages — Spanish and English, German and English — every word scores as
secondary and the ratio sits near 1.0 whatever the narration says. The gate was built for a
Devanagari/Latin pair, and its docstring says the ratio is a lower bound for exactly this
reason. Leave `gates: []` and say why in a comment rather than shipping a check that cannot
fail.

**`tts.voice_id` is not validated when the config loads.** Check whether a Piper voice
exists for your language at https://huggingface.co/rhasspy/piper-voices before you declare
one; each voice is two files, `<name>.onnx` and `<name>.onnx.json`. A name with nothing
matching on disk raises at `creatoros audio` time and lists what is installed
(`piper.py::_resolve`) — loud, and fine. The quiet failure is a model whose phonemiser has
no coverage for the script you write in: Piper exits 0 and writes a valid WAV with no
samples in it. That shipped a silent scene once — the manifest recorded the clip, the
renderer fell back to the scene's minimum duration, and every later run said "unchanged".
`piper.py`'s `_wav_is_empty()` now refuses it, but the way to not meet it is to match the
model's language to the script.

**`publish.mode: draft`, always.** `nexlore-ai` is the only preset that ships `auto`,
because it is a live channel. A copy that keeps `auto` turns a stranger's first trial run
into real posts. `test_every_preset_but_the_live_one_publishes_nothing_by_default` enforces
this — add your preset to its list.

**`offset_days` is not a free choice if you list `instagram`.** `claro-es` can use `1`
because it only posts to YouTube. The Instagram Graph API has no scheduled publish for
Reels, so an Instagram preset wants `offset_days: 0` — see the comment in
`examples/ember-fitness/creatoros.yaml`.

**A preset no test loads will rot.** Phase 2 found three modules that had been broken since
Phase 1 because nothing imported them; 665 tests and six green CI jobs never noticed. Adding
your name to the three lists in `tests/test_brand.py` is what makes the preset real.

**A typo in a palette colour name is an error, not a default.** `Palette.build()` rejects
an unknown colour and lists the valid names. Override only the colours you want to change;
the rest fall back to `DEFAULT_PALETTE`.

**Leave `workspace: ""`.** Empty means the platform's own data directory. It does not mean
the preset folder, and a preset that points `workspace` anywhere specific breaks for
everyone who is not you.
