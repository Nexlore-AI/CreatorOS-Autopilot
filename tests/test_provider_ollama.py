# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Ollama backend, against a fake server rather than a real one.

Every defect these tests pin down was invisible without one. A truncated generation, a
200 carrying an error, a non-HTTP service on the port and a slow-but-alive server all
look fine from a unit test that stubs `_call`, and none of them can be provoked against a
healthy `ollama serve`. So the fakes here are real HTTP (and in one case deliberately
not-HTTP) sockets on a loopback port.

Nothing here needs Ollama installed, and no test touches 11434.
"""
import contextlib
import json
import socketserver
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from creatoros_autopilot.providers.llm import ollama as mod
from creatoros_autopilot.providers.llm.ollama import LOOPBACK, Ollama, OllamaError

from .conftest import PACKAGE

ONE_MODEL = {"models": [{"name": "llama3.1:latest"}]}
GOOD = {"response": "a spec", "done": True, "done_reason": "stop"}


@contextlib.contextmanager
def fake_ollama(*, tags=None, generate=None, delay=0.0):
    """A server that speaks Ollama's two endpoints and whatever we tell it to say."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _reply(self, payload):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._reply(ONE_MODEL if tags is None else tags)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if delay:
                time.sleep(delay)
            self._reply(GOOD if generate is None else generate)

    with _running(ThreadingHTTPServer(("127.0.0.1", 0), Handler)) as host:
        yield host


@contextlib.contextmanager
def not_http():
    """Something else on the port: the case that used to raise BadStatusLine."""
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            with contextlib.suppress(OSError):
                self.request.recv(4096)
                self.request.sendall(b"SSH-2.0-OpenSSH_9.8\r\n")

    with _running(socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)) as host:
        yield host


@contextlib.contextmanager
def html_page():
    """A 200 that is not JSON at all: a proxy login page, usually."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _reply(self):
            body = b"<html><body>sign in to continue</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _reply

    with _running(ThreadingHTTPServer(("127.0.0.1", 0), Handler)) as host:
        yield host


@contextlib.contextmanager
def cut_off():
    """A body shorter than its Content-Length: the IncompleteRead case."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "400")
            self.end_headers()
            self.wfile.write(b'{"models": [')
            self.wfile.flush()
            self.close_connection = True

    with _running(ThreadingHTTPServer(("127.0.0.1", 0), Handler)) as host:
        yield host


@contextlib.contextmanager
def _running(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def dead_port() -> str:
    """An address with nothing behind it, chosen by binding and closing."""
    with socketserver.TCPServer(("127.0.0.1", 0), None) as probe:
        return f"http://127.0.0.1:{probe.server_address[1]}"


class TestHost:
    """`OLLAMA_HOST` as people actually write it."""

    @pytest.mark.parametrize("configured,expected", [
        ("", "http://localhost:11434"),
        # Ollama's own documented value for exposing the server. Without the port this
        # became http://0.0.0.0, i.e. port 80, and available() blamed a running server.
        ("0.0.0.0", "http://0.0.0.0:11434"),
        ("localhost", "http://localhost:11434"),
        ("127.0.0.1:11434", "http://127.0.0.1:11434"),
        (":11434", "http://127.0.0.1:11434"),
        ("  box.local:11434/  ", "http://box.local:11434"),
        ("[::1]:11434", "http://[::1]:11434"),
        ("::1", "http://[::1]:11434"),
        # A written-out scheme keeps the port that scheme implies, as `ollama serve`
        # reads it, so a reverse proxy on 443 is not redirected to 11434.
        ("https://ollama.example.com", "https://ollama.example.com:443"),
        ("http://ollama.example.com", "http://ollama.example.com:80"),
        ("http://127.0.0.1:11434/ollama", "http://127.0.0.1:11434/ollama"),
    ])
    def test_a_port_is_always_filled_in(self, configured, expected):
        assert mod._normalise_host(configured) == expected

    def test_a_missing_hostname_still_counts_as_loopback(self):
        """`:11434` left hostname as None, which missed the proxy bypass below."""
        host = Ollama(host=":11434").host
        assert urllib.parse.urlsplit(host).hostname in LOOPBACK

    def test_the_environment_is_read_when_nothing_is_passed(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0")
        monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b")
        backend = Ollama()
        assert backend.host == "http://0.0.0.0:11434"
        assert backend.model == "qwen2.5:7b"

    def test_a_nonnumeric_port_is_not_quietly_replaced(self):
        """Substituting the default would hide the typo instead of reporting it."""
        assert mod._normalise_host("127.0.0.1:eleven") == "http://127.0.0.1:eleven"
        ready = Ollama("m", host="127.0.0.1:eleven").available()
        assert not ready.ok and ready.fix


class TestCredentials:
    """`OLLAMA_HOST=https://user:token@host`, which urllib cannot send."""

    HOST = "https://user:s3cr3t-token@ollama.example.com"

    def test_the_token_never_reaches_the_url_or_a_message(self):
        backend = Ollama("m", host=self.HOST)
        ready = backend.available()
        with pytest.raises(OllamaError) as raised:
            backend.complete("hello")
        for text in (backend.host, ready.detail, ready.fix, str(raised.value)):
            assert "s3cr3t-token" not in text, "a secret must not be echoed"

    def test_it_refuses_instead_of_trying_the_bare_address(self):
        ready = Ollama("m", host=self.HOST).available()
        assert not ready.ok
        assert "credentials" in ready.detail and ready.fix


class TestTransport:
    """`http.client` exceptions are not OSErrors, so they used to escape as tracebacks."""

    def test_a_non_http_service_on_the_port_is_a_sentence(self):
        with not_http() as host:
            ready = Ollama("m", host=host).available()
        assert not ready.ok
        assert "OLLAMA_HOST" in ready.fix, "the port is the problem, not `ollama serve`"

    def test_a_non_http_service_fails_complete_as_an_ollama_error(self):
        with not_http() as host:
            with pytest.raises(OllamaError):
                Ollama("m", host=host).complete("hello")

    def test_a_truncated_body_does_not_escape_models(self):
        """models() documents returning [] when it cannot read the server."""
        with cut_off() as host:
            backend = Ollama("m", host=host)
            assert backend.models() == []
            ready = backend.available()
        assert not ready.ok and ready.fix

    def test_nothing_listening_still_says_ollama_serve(self):
        ready = Ollama("m", host=dead_port()).available()
        assert not ready.ok
        assert "ollama serve" in ready.fix

    def test_a_pulled_model_is_found(self):
        with fake_ollama() as host:
            backend = Ollama("llama3.1", host=host)
            assert backend.available().ok
            assert backend.models() == ["llama3.1:latest"]

    def test_a_missing_model_is_not_a_missing_server(self):
        with fake_ollama() as host:
            ready = Ollama("mistral", host=host).available()
        assert not ready.ok and ready.fix == "ollama pull mistral"


class TestComplete:
    def test_a_finished_answer_comes_back(self):
        with fake_ollama() as host:
            assert Ollama("llama3.1", host=host).complete("topic") == "a spec"

    def test_an_old_server_without_done_fields_still_works(self):
        with fake_ollama(generate={"response": "a spec"}) as host:
            assert Ollama("llama3.1", host=host).complete("topic") == "a spec"

    def test_truncation_at_num_predict_is_an_error_not_a_partial_string(self):
        """The expensive one: half a JSON object looks like success until it is parsed."""
        half = {"response": '{"hook":"a","beat', "done": True, "done_reason": "length"}
        with fake_ollama(generate=half) as host:
            with pytest.raises(OllamaError) as raised:
                Ollama("llama3.1", host=host).complete("topic", max_tokens=64)
        message = str(raised.value)
        assert "length" in message and "max_tokens above 64" in message

    def test_done_false_is_an_error(self):
        with fake_ollama(generate={"response": "half", "done": False}) as host:
            with pytest.raises(OllamaError, match="stopped before finishing"):
                Ollama("llama3.1", host=host).complete("topic")

    def test_an_error_field_beside_a_200_is_not_success(self):
        payload = {"response": "ok", "error": "model requires more system memory"}
        with fake_ollama(generate=payload) as host:
            with pytest.raises(OllamaError, match="more system memory"):
                Ollama("llama3.1", host=host).complete("topic")

    def test_an_empty_answer_is_an_error(self):
        with fake_ollama(generate={"response": "  ", "done_reason": "stop"}) as host:
            with pytest.raises(OllamaError, match="no text"):
                Ollama("llama3.1", host=host).complete("topic")

    def test_a_reply_that_is_not_json_is_an_error(self):
        with html_page() as host:
            with pytest.raises(OllamaError, match="cannot read"):
                Ollama("llama3.1", host=host).complete("topic")
            ready = Ollama("llama3.1", host=host).available()
        assert not ready.ok and "OLLAMA_HOST" in ready.fix


class TestTimeout:
    def test_a_slow_generation_is_not_reported_as_a_dead_server(self):
        with fake_ollama(delay=1.5) as host:
            with pytest.raises(OllamaError) as raised:
                Ollama("llama3.1", host=host, timeout=0.3).complete("topic")
        message = str(raised.value)
        assert "ollama serve" not in message, "the server is up; restarting it fixes nothing"
        assert "OLLAMA_TIMEOUT" in message and "0.3" in message

    def test_the_deadline_is_configurable(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_TIMEOUT", "45")
        assert Ollama("m").timeout == 45.0
        assert Ollama("m", timeout=7).timeout == 7.0, "an argument beats the environment"

    def test_the_default_survives_an_unset_environment(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_TIMEOUT", raising=False)
        assert Ollama("m").timeout == mod.GENERATE_TIMEOUT

    @pytest.mark.parametrize("value", ["soon", "0", "-30"])
    def test_a_nonsense_deadline_is_refused_rather_than_ignored(self, monkeypatch, value):
        """Falling back to 600s would hold the lock for ten minutes without saying why."""
        monkeypatch.setenv("OLLAMA_TIMEOUT", value)
        with pytest.raises(ValueError, match="OLLAMA_TIMEOUT"):
            Ollama("m")


class TestAgentRefusal:
    def test_run_agent_names_what_actually_works(self):
        with pytest.raises(NotImplementedError) as raised:
            Ollama("m").run_agent("prompt", {})
        message = str(raised.value)
        assert "claude_code" in message and "--skip-agent" in message

    def test_it_no_longer_promises_a_scripted_flow(self):
        """It said the pipeline would turn a topic into a spec through complete().

        It does not: `_run_agent` raises for any backend with `agentic = False`, so the
        advertised path produced no video and no explanation.
        """
        with pytest.raises(NotImplementedError) as raised:
            Ollama("m").run_agent("prompt", {})
        message = str(raised.value)
        for promise in ("uses the scripted flow", "nothing needs configuring",
                        "topic in and spec out"):
            assert promise not in message

    def test_nothing_in_the_pipeline_turns_a_topic_into_a_spec(self):
        """A tripwire: if a scripted flow is ever added, rewrite that refusal."""
        source = (PACKAGE / "core" / "pipeline.py").read_text(encoding="utf-8")
        assert ".complete(" not in source
