# Add a video style

A style turns a spec into pixels. The one that ships, `nexlore-v3`, draws every frame itself
with Pillow and NumPy at 2× and downsamples — no browser, no templating engine, no GPU.

A style is a plugin like any other, but it is the most involved kind, so this is about what
you are taking on rather than a copy-paste example.

## The contract

```python
class MyStyle:
    name = "my-style"
    scene_types = ("hook", "body", "outro")      # your vocabulary
    visual_types = ()
    width, height = 1080, 1920

    def available(self) -> Availability: ...
    def render(self, spec, spec_path, out=None, brand=None) -> Path: ...
    def preview(self, spec, spec_path, out=None, brand=None) -> Path: ...
```

`scene_types` and `visual_types` are what `core.spec` validates a spec against, so a style
with its own vocabulary gets its specs validated correctly rather than against someone
else's.

## Four things the shipped engine does that yours should

**Time scenes to the narration.** A scene lasts `max(min_seconds, lead + voice + tail)`. A
fixed duration either clips the voice or leaves dead air, and both are obvious to a viewer.

**Write artifacts beside the output, never beside the spec.** A spec may be read-only — in a
container, in site-packages, in a shared examples folder. Writing next to it is how
`render /app/examples/specs/kv-cache.json -o /workspace/out.mp4` died on a permission error
for a path the user never named.

**Take a brand rather than hard-coding one.** The name, logo, palette and fonts come from the
kit. A missing logo renders a wordmark and a missing font falls back — a day's run is not
lost because an image moved.

**Offer a cheap preview.** `preview()` writes one still per scene as a contact sheet. It
takes under a second where a render takes minutes, and it is where layout problems — text
off the frame, a card overlapping a visual, anything in the bottom sixth where the platform
UI sits — are caught.

## If you are starting from the existing engine

`creatoros_autopilot/render/engine.py` is about 800 lines and dense on purpose: one statement
per visual element. It is excluded from some lint rules for that reason.

The parts you would keep: the background and grid generation, `apply_brand()`, the per-scene
`Pool` render and ffmpeg concat, the audio mix with the music ducked under the voice. The
parts you would replace: the `SCENES` and `VISUALS` tables and the `sc_*` drawing functions.

A warning from experience. If you cache anything derived from the brand — the engine caches
the background, grid, logo and gradient bar — clear it **only when the brand actually
changes**. Clearing it unconditionally rebuilt a 2160×3840 float array once per scene instead
of once per worker and took the test suite from two minutes to over nine. And if a cached
object defines `__getattr__`, make it read `__dict__` directly: a render worker unpickles it
with an empty `__dict__`, so a lookup through `self` calls itself forever, and the only
symptom is a job that never finishes.

## Proving you did not change the existing look

If you touch the shipped engine, render the examples before and after and compare:

```python
from creatoros_autopilot.render import engine
from creatoros_autopilot.core import brand
import numpy as np
from PIL import Image

kit = brand.load("nexlore-ai")
out = engine.run("preview", "examples/specs/kv-cache.json", "/tmp/after.png", kit=kit)
a = np.asarray(Image.open("/tmp/before.png").convert("RGB"), dtype=np.int16)
b = np.asarray(Image.open(out).convert("RGB"), dtype=np.int16)
assert (a == b).all(), f"{(np.abs(a - b) > 0).sum()} pixels changed"
```

That is how the brand refactor was shown to be neutral for a channel that was already
running. It is worth doing even when you are sure.
