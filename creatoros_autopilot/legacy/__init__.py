# SPDX-License-Identifier: AGPL-3.0-or-later
"""Code that still works but is not on any live path, and is not portable.

Nothing here is imported by `core`, `render`, `providers`, `quality`, `cli` or `agents` —
`tests/test_portability.py` asserts that. It is kept so the work it represents can be
reproduced, and it is quarantined so the "runs anywhere" rule can be enforced everywhere
that matters.

* `reel_engine.py` — the v1 Instagram-reels renderer, retired on 2026-11-16 when one
  pipeline took over both platforms. It carries its own macOS `say` narration, which is
  the design the current engine moved away from: TTS does not belong inside a renderer.
* `codeswitch_bakeoff.py` — a one-off benchmark comparing TTS backends on Hindi/English
  code-switching. It is the only place several of those backends were ever called, which
  is why the README's claim of seven TTS backends did not match the live path.

Phase 2 decides their fate: either `say` becomes a registered TTS provider with an
`available()` check and these are ported behind the interfaces, or they are deleted.
Deleting a renderer is a product decision, so it is Aman's call, not a refactor.
"""
