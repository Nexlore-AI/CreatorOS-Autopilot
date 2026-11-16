# SPDX-License-Identifier: AGPL-3.0-or-later
"""Single-shot generation from a model running on this machine, through Ollama.

Half of the zero-cost path: with this and Piper a whole run needs no account, no key and
nothing leaving the laptop. The other half of that bargain is that it cannot do the daily
agent pass. `agentic` is False and `run_agent` raises, rather than wrapping an 8B model in
a loop that imitates Claude Code — `interfaces/llm.py` explains why at length, and the
pipeline already has a scripted flow (topic in, spec out through `complete()`) for
backends that answer no.

Ollama speaks plain HTTP on localhost, so there is no SDK here and no dependency: one
JSON body and `urllib`.

`available()` is most of the first-run experience for this backend, so it separates the
two failures that look identical from outside — the server is not running, and the server
is running but the model was never pulled. They need different commands, and a connection
refused must arrive as a sentence rather than a traceback.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from ...interfaces import Availability

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "llama3.1"
LOOPBACK = {"localhost", "127.0.0.1", "::1"}

# `urlopen` honours `http_proxy`, so on a managed machine a request to localhost:11434 is
# handed to a corporate proxy that cannot possibly answer it — the user then reads
# "connection refused" about a server that is running fine. Loopback skips the proxy; a
# genuinely remote OLLAMA_HOST still goes through it.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# Generation is minutes, not seconds: an 8B model on CPU runs at a few tokens a second,
# and the first request after `ollama serve` also pays for loading the weights off disk.
# A timeout short enough to feel responsive would kill exactly the runs this backend
# exists to serve, so it is deliberately generous and the user's own patience is the real
# limit.
GENERATE_TIMEOUT = 600.0

# Asking what is installed is a local directory listing. If it has not answered by now,
# nothing is listening.
TAGS_TIMEOUT = 5.0


class OllamaError(RuntimeError):
    pass


def _normalise_host(host: str) -> str:
    """A base URL, from whatever the user set.

    `OLLAMA_HOST` is Ollama's own variable and its CLI takes a bare `127.0.0.1:11434`, so
    that is what people have in their shell profile. Pasting it here without a scheme
    would make `urllib` treat the whole thing as a path.
    """
    host = host.strip().rstrip("/")
    if not host:
        return DEFAULT_HOST
    return host if "://" in host else f"http://{host}"


def _satisfies(wanted: str, pulled: str) -> bool:
    """Whether a pulled model is the configured one.

    `/api/tags` always reports an explicit tag (`llama3.1:latest`) while people write the
    bare name in config and after `ollama pull`. Comparing the two as strings reports a
    model sitting on disk as missing. Different tags are genuinely different models,
    though — `llama3.1:70b` does not answer a request for `llama3.1` — so only the
    implicit `:latest` is filled in.
    """
    return pulled == wanted or (":" not in wanted and pulled == f"{wanted}:latest")


class Ollama:
    """A local model served by Ollama."""

    name = "ollama"
    agentic = False
    paid = False

    def __init__(self, model: str = "", *, host: str = "",
                 timeout: float = GENERATE_TIMEOUT) -> None:
        """`model` defaults to `llama3.1`, `host` to `http://localhost:11434`.

        Both also read the environment, because an entry-point plugin is built with no
        arguments (see `plugins.load`) and `complete()` takes no model parameter. Without
        an env fallback a user loading this by name in `creatoros.yaml` could never say
        which model to run.
        """
        self.model = (model or os.environ.get("OLLAMA_MODEL") or DEFAULT_MODEL).strip()
        self.host = _normalise_host(host or os.environ.get("OLLAMA_HOST", ""))
        self.timeout = timeout

    def _call(self, path: str, body: dict | None = None, *, timeout: float) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.host + path, data=data, method="GET" if data is None else "POST",
            headers={} if data is None else {"Content-Type": "application/json"},
        )
        host = urllib.parse.urlsplit(self.host).hostname or ""
        send = _DIRECT.open if host in LOOPBACK else urllib.request.urlopen
        with send(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", "replace")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} did not return JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"{path} did not return a JSON object")
        return payload

    def available(self) -> Availability:
        try:
            payload = self._call("/api/tags", timeout=TAGS_TIMEOUT)
        except urllib.error.HTTPError as exc:
            return Availability.no(
                f"{self.host} answered HTTP {exc.code} for /api/tags",
                f"something other than Ollama is serving {self.host}. Point OLLAMA_HOST "
                "at the right address, or free that port.",
            )
        except (urllib.error.URLError, OSError) as exc:
            # Connection refused reaches us as a URLError wrapping the socket error, and
            # a timeout as TimeoutError; both are OSError. Either way the server is not
            # there, and the user wants one line, not a stack.
            reason = getattr(exc, "reason", exc)
            return Availability.no(
                f"nothing answering at {self.host} ({reason})",
                "ollama serve   (install it first from https://ollama.com/download)",
            )
        except ValueError as exc:
            return Availability.no(
                f"{self.host} is not speaking Ollama's API ({exc})",
                "check OLLAMA_HOST — another service is probably on that port.",
            )

        pulled = self._names(payload)
        if not pulled:
            return Availability.no(
                f"Ollama is running at {self.host} with no models pulled",
                f"ollama pull {self.model}",
            )
        if not any(_satisfies(self.model, candidate) for candidate in pulled):
            return Availability.no(
                f"{self.model} is not pulled; {self.host} has {self._listing(pulled)}",
                f"ollama pull {self.model}",
            )
        return Availability.yes(f"{self.model} at {self.host}; pulled: {self._listing(pulled)}")

    def models(self) -> list[str]:
        """Every model pulled on this machine, tags included.

        Empty when the server cannot be reached: `available()` is the one place that
        reports why, with the fix.
        """
        try:
            return self._names(self._call("/api/tags", timeout=TAGS_TIMEOUT))
        except (urllib.error.URLError, OSError, ValueError):
            return []

    @staticmethod
    def _names(payload: dict) -> list[str]:
        models = payload.get("models")
        if not isinstance(models, list):
            return []
        names = [str(entry.get("name") or entry.get("model") or "").strip()
                 for entry in models if isinstance(entry, dict)]
        return sorted(name for name in names if name)

    @staticmethod
    def _listing(pulled: list[str], limit: int = 6) -> str:
        shown = ", ".join(pulled[:limit])
        return shown if len(pulled) <= limit else f"{shown} (+{len(pulled) - limit} more)"

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 4096,
                 temperature: float = 0.7, **options) -> str:
        """One prompt, one answer.

        Extra keyword arguments are passed straight through as Ollama options, so
        `top_p`, `seed` or `num_ctx` work without this module knowing about them.
        """
        body = {
            "model": self.model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            # Streaming would return newline-delimited JSON chunks instead of one object,
            # which nothing upstream of here wants.
            "options": {"temperature": temperature, "num_predict": max_tokens, **options},
        }
        try:
            payload = self._call("/api/generate", body, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            # Ollama puts the real cause in the body — most often that the model is not
            # pulled — and the status alone would hide it.
            detail = exc.read()[:300].decode("utf-8", "replace").strip()
            hint = f" Try: ollama pull {self.model}" if exc.code == 404 else ""
            raise OllamaError(f"Ollama returned HTTP {exc.code}: {detail}.{hint}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise OllamaError(
                f"could not reach Ollama at {self.host} ({getattr(exc, 'reason', exc)}). "
                "Start it with: ollama serve"
            ) from exc
        except ValueError as exc:
            raise OllamaError(f"Ollama sent a reply this backend cannot read: {exc}") from exc

        text = payload.get("response")
        if not isinstance(text, str) or not text.strip():
            # An empty response is usually num_predict set too low or a model that
            # answered with nothing; either way the caller is about to write an empty
            # script, so it fails here instead.
            raise OllamaError(
                f"{self.model} returned no text. Raise max_tokens, or try a larger model."
            )
        return text

    def run_agent(self, prompt: str, context: dict, *, max_turns: int = 240,
                  cwd: str | None = None, timeout: float | None = None) -> int:
        raise NotImplementedError(
            "ollama cannot run the agent pass: it has no tools, so it cannot search the "
            "web, read competitors, or write and revise its own files over many turns. "
            "Because this backend reports agentic = False the pipeline uses the scripted "
            "flow instead — one complete() call, topic in and spec out — so nothing needs "
            "configuring to get a video. For the agentic path set agent.provider to "
            "claude_code."
        )
