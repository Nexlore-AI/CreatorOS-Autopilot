# SPDX-License-Identifier: AGPL-3.0-or-later
"""The contracts a plugin implements.

Every external service sits behind one of these, and `core/` imports none of their
implementations. That is the whole point: adding ElevenLabs, Piper, Zernio or Ollama must
never mean editing core code, and a provider nobody at Nexlore has heard of must be able
to work exactly as well as one we wrote.

Each interface is a `Protocol`, so a plugin does not import or subclass anything from here
to satisfy it — it just has the right methods. That keeps a third-party plugin from
depending on our class hierarchy, and it keeps our own test fakes honest.

The pieces every interface shares:

* `name` — the string a user writes in `creatoros.yaml`.
* `available()` — can this run *right now*, on this machine, with these keys? It returns
  an `Availability`, never a bool, because "no" is only useful with a reason and a fix.
  `creatoros doctor` renders them; nothing else decides what a missing key looks like.
"""
from .availability import Availability
from .brand import Brand
from .gate import Gate, GateResult
from .llm import LLM, Message
from .publish import Publisher, PublishRequest, PublishResult, PlatformOutcome
from .research import Item, ResearchSource
from .style import Style
from .tts import TTS, Voice

__all__ = [
    "Availability",
    "Brand",
    "Gate", "GateResult",
    "LLM", "Message",
    "Publisher", "PublishRequest", "PublishResult", "PlatformOutcome",
    "Item", "ResearchSource",
    "Style",
    "TTS", "Voice",
]
