# SPDX-License-Identifier: AGPL-3.0-or-later
"""Single-shot generation from a model running on this machine, through Ollama.

Half of the zero-cost path: with this and Piper a whole run needs no account, no key and
nothing leaving the laptop. The other half of that bargain is that it cannot do the daily
agent pass. `agentic` is False and `run_agent` raises, rather than wrapping an 8B model in
a loop that imitates Claude Code — `interfaces/llm.py` explains why at length.

What it does *not* do, because this docstring used to claim it did: there is no scripted
"topic in, spec out" flow anywhere in the pipeline. `core/pipeline.py` has one agent step
and `_run_agent` raises for any backend with `agentic = False`, so `agent.provider: ollama`
plus `creatoros run` renders nothing at all. Promising the fallback here, and in
`run_agent`'s refusal, sent people to configure a path that does not exist and to wonder
what they had got wrong. The refusal now names only what works today. `complete()` stays
because the `LLM` protocol requires it and a script of your own can call it — nothing in
the package does yet.

Ollama speaks plain HTTP on localhost, so there is no SDK here and no dependency: one
JSON body and `urllib`.

`available()` is most of the first-run experience for this backend, so it separates the
failures that look identical from outside — the server is not running, the server is
running but the model was never pulled, something else is on the port. They need
different commands, and every one of them must arrive as a sentence rather than a
traceback.
"""
from __future__ import annotations

import http.client
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from ...interfaces import Availability

DEFAULT_PORT = "11434"
DEFAULT_HOST = f"http://localhost:{DEFAULT_PORT}"
DEFAULT_MODEL = "llama3.1"
LOOPBACK = {"localhost", "127.0.0.1", "::1"}

# What a port means when the user wrote the scheme out: this is how Ollama's own
# `OLLAMA_HOST` parser reads it, and matching the CLI is the only way `ollama list` and
# this backend can agree about where the server is.
SCHEME_PORTS = {"http": "80", "https": "443"}

# `urlopen` honours `http_proxy`, so on a managed machine a request to localhost:11434 is
# handed to a corporate proxy that cannot possibly answer it — the user then reads
# "connection refused" about a server that is running fine. Loopback skips the proxy; a
# genuinely remote OLLAMA_HOST still goes through it.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# Everything that means "no usable HTTP reply came back". `http.client`'s own exceptions
# are not in the OSError tree, so an except clause naming only OSError let three of them
# out as a traceback: BadStatusLine from a non-HTTP service on the port, IncompleteRead
# from a body shorter than its Content-Length, InvalidURL from a host urllib cannot parse.
# `available()` exists to answer in one sentence and `pipeline._run_agent` calls it
# without a try, so each of those reached the user as a stack instead.
TRANSPORT = (urllib.error.URLError, OSError, http.client.HTTPException)

# Generation is minutes, not seconds: an 8B model on CPU runs at a few tokens a second,
# and the first request after `ollama serve` also pays for loading the weights off disk.
# A timeout short enough to feel responsive would kill exactly the runs this backend
# exists to serve, so it is deliberately generous — and `OLLAMA_TIMEOUT` moves it, because
# an entry-point plugin is built with no arguments and a 70B model on a laptop needs more.
GENERATE_TIMEOUT = 600.0

# Asking what is installed is a local directory listing. If it has not answered by now,
# nothing is listening.
TAGS_TIMEOUT = 5.0

CREDENTIALS_DETAIL = (
    "OLLAMA_HOST carries credentials before the @, which urllib cannot send; they have "
    "been dropped rather than tried unauthenticated"
)
CREDENTIALS_FIX = (
    "set OLLAMA_HOST to the plain host:port and reach the authenticated endpoint through "
    "an SSH tunnel, or through a local proxy that adds the header"
)


class OllamaError(RuntimeError):
    pass


def _split_authority(authority: str) -> tuple[str, str]:
    """Host and port out of `host`, `host:port`, `[::1]:port` or a bare `::1`."""
    if authority.startswith("["):
        hostname, _, port = authority.partition("]")
        return f"{hostname}]", port.lstrip(":")
    if authority.count(":") > 1:
        # A bare IPv6 literal: no port can be told apart from the address, and urllib
        # needs the brackets back before it will parse a hostname out of it at all.
        return f"[{authority}]", ""
    hostname, _, port = authority.partition(":")
    return hostname, port


def _has_credentials(host: str) -> bool:
    """Whether the configured host embeds `user:password@`."""
    text = host.strip()
    authority = text.partition("://")[2] or text
    return "@" in authority.partition("/")[0]


def _normalise_host(host: str) -> str:
    """A base URL with an explicit host and port, from whatever the user set.

    `OLLAMA_HOST` is Ollama's own variable and its CLI takes a bare `127.0.0.1:11434`, so
    that is what people have in their shell profile. Pasting it here without a scheme
    would make `urllib` treat the whole thing as a path.

    The port is filled in the way `ollama serve` fills it — a value with no scheme gets
    11434 — because leaving it out meant the documented `OLLAMA_HOST=0.0.0.0` for exposing
    the server became `http://0.0.0.0`, i.e. port 80. `available()` then reported
    "nothing answering, run ollama serve" against a server that was up on 11434, which is
    both wrong and unfixable by doing what it says. A missing hostname (`:11434`, which
    the server reads as "every interface") is filled with loopback for the same class of
    reason: `urlsplit` leaves it as None, so the request missed LOOPBACK below and was
    handed to `http_proxy`, which cannot route to this machine.

    Credentials in the URL are dropped: urllib reads the token as a port and raises
    InvalidURL *with the token in the exception text*, so carrying it would put a secret
    into error messages. `available()` and `complete()` refuse outright instead of
    silently retrying the bare address.
    """
    text = host.strip().rstrip("/")
    if not text:
        return DEFAULT_HOST
    scheme, written, rest = text.partition("://")
    if not written:
        scheme, rest = "http", text
    authority, slash, path = rest.partition("/")
    authority = authority.rpartition("@")[2]
    hostname, port = _split_authority(authority)
    if not port:
        port = SCHEME_PORTS.get(scheme.lower(), DEFAULT_PORT) if written else DEFAULT_PORT
    # A port that is not a number stays as written: substituting the default would hide
    # the typo, and it now surfaces by name as an InvalidURL rather than a traceback.
    return f"{scheme}://{hostname or '127.0.0.1'}:{port}{slash}{path}"


def _timeout(given: float | None) -> float:
    """The generation deadline: the argument, else `OLLAMA_TIMEOUT`, else the default.

    A nonsense value is an error rather than a fallback. Quietly reverting to 600 s would
    be this project's recurring bug: the user asked for a 60 s deadline, a stalled model
    would hold the lock for ten minutes, and nothing would say why.
    """
    source, raw = (("timeout", given) if given is not None
                   else ("OLLAMA_TIMEOUT", os.environ.get("OLLAMA_TIMEOUT", "").strip()))
    if raw == "":
        return GENERATE_TIMEOUT
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        seconds = 0.0
    if seconds <= 0:
        raise ValueError(f"{source} must be a positive number of seconds, not {raw!r}")
    return seconds


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
                 timeout: float | None = None) -> None:
        """`model` defaults to `llama3.1`, `host` to `http://localhost:11434`.

        All three also read the environment, because an entry-point plugin is built with
        no arguments (see `plugins.load`) and `complete()` takes no model parameter.
        Without an env fallback a user loading this by name in `creatoros.yaml` could
        never say which model to run, or how long to let it think.
        """
        self.model = (model or os.environ.get("OLLAMA_MODEL") or DEFAULT_MODEL).strip()
        configured = host or os.environ.get("OLLAMA_HOST", "")
        self.host = _normalise_host(configured)
        self._credentialled = _has_credentials(configured)
        self.timeout = _timeout(timeout)

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
        if self._credentialled:
            return Availability.no(CREDENTIALS_DETAIL, CREDENTIALS_FIX)
        try:
            payload = self._call("/api/tags", timeout=TAGS_TIMEOUT)
        except urllib.error.HTTPError as exc:
            return Availability.no(
                f"{self.host} answered HTTP {exc.code} for /api/tags",
                f"something other than Ollama is serving {self.host}. Point OLLAMA_HOST "
                "at the right address, or free that port.",
            )
        except http.client.HTTPException as exc:
            # Something replied, but not with an HTTP response this client can finish
            # reading, so `ollama serve` is the wrong advice: the port is the problem.
            return Availability.no(
                f"{self.host} did not return a readable HTTP reply "
                f"({type(exc).__name__}: {exc})",
                "check OLLAMA_HOST — something other than Ollama is probably on that "
                "port.",
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
        except (*TRANSPORT, ValueError):
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
        if self._credentialled:
            raise OllamaError(f"{CREDENTIALS_DETAIL} — {CREDENTIALS_FIX}")
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
        except TimeoutError as exc:
            # The socket went quiet after connecting, so the server is there and thinking.
            # This used to fall through to the branch below and tell the user to start a
            # server that was already running, while the real fix — a longer deadline or
            # a smaller model — went unmentioned and the next run stalled identically.
            raise OllamaError(
                f"{self.model} at {self.host} sent nothing for {self.timeout:g}s. The "
                f"server is up; generation is slower than that. Raise the deadline with "
                f"OLLAMA_TIMEOUT (seconds), or run a smaller model."
            ) from exc
        except http.client.HTTPException as exc:
            raise OllamaError(
                f"{self.host} did not return a readable HTTP reply "
                f"({type(exc).__name__}: {exc}). Check OLLAMA_HOST — something other "
                f"than Ollama may be on that port."
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise OllamaError(
                f"could not reach Ollama at {self.host} ({getattr(exc, 'reason', exc)}). "
                "Start it with: ollama serve"
            ) from exc
        except ValueError as exc:
            raise OllamaError(f"Ollama sent a reply this backend cannot read: {exc}") from exc

        error = payload.get("error")
        if isinstance(error, str) and error.strip():
            # A 200 can still carry an error — "model requires more system memory" is the
            # common one — beside a `response` holding nothing or half a sentence. Reading
            # only `response` turned an out-of-memory refusal into a plausible script.
            raise OllamaError(f"{self.model} could not generate: {error.strip()}")

        text = payload.get("response")
        reason = payload.get("done_reason")
        unfinished = isinstance(reason, str) and reason not in ("", "stop")
        if payload.get("done") is False or unfinished:
            # `done_reason: "length"` means the answer stops wherever num_predict ran out.
            # Returning it read as success and handed the caller half a JSON object: the
            # same silent-wrong-value shape as the rest of this project's worst bugs, and
            # invisible until a spec fails to parse several steps later. Old servers send
            # neither field, so only an explicit "not finished" fails here.
            stopped = f"done_reason={reason!r}" if reason else "done was false"
            hint = (f"Raise max_tokens above {max_tokens}" if reason == "length"
                    else "See what `ollama serve` logged")
            got = len(text) if isinstance(text, str) else 0
            raise OllamaError(
                f"{self.model} stopped before finishing ({stopped}) after {got} "
                f"characters, so the answer is cut off. {hint}."
            )
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
            "There is no scripted flow behind it either — `creatoros run` has one agent "
            "step, so with agent.provider: ollama it writes no spec and renders nothing. "
            "Two things do work: set agent.provider: claude_code for the daily run, or "
            "write the specs into the job directory yourself and run `creatoros run "
            "--skip-agent`, which never calls a model. This backend's complete() is "
            "there for your own scripts in the meantime."
        )
