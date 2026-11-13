"""Providers, cassettes and the rule that tests never reach the network."""

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from bridge_island.aimap import cassette
from bridge_island.aimap import providers as P

REQ = P.Request("aviation/plain", trial=0, round=0, prompt="map it")


def test_tests_cannot_reach_beyond_this_machine() -> None:
    with pytest.raises(RuntimeError, match="blocked in tests"):
        socket.create_connection(("93.184.216.34", 80), timeout=1)


class _Fake:  # a provider that counts its calls
    provider, model = "fake", "m1"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request: P.Request) -> P.Completion:
        self.calls += 1
        return P.Completion("name: x", 11, 7, 0.25)


def test_recording_then_replay_gives_the_same_text(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    inner = _Fake()
    rec = P.RecordingProvider(inner, path)
    assert rec.complete(REQ).text == "name: x"
    assert rec.complete(REQ).text == "name: x" and inner.calls == 1  # second answer from the file
    replay = P.ReplayProvider("fake", "m1", path)
    done = replay.complete(REQ)
    assert (done.text, done.tokens_in, done.tokens_out) == ("name: x", 11, 7)
    line = json.loads(path.read_text(encoding="utf-8"))
    assert "map it" not in json.dumps(line)  # the prompt is stored as a hash only
    assert line["prompt_sha256"] == P.prompt_sha256("map it")


def test_replay_never_guesses(tmp_path: Path) -> None:
    replay = P.ReplayProvider("fake", "m1", tmp_path / "none.jsonl")
    with pytest.raises(cassette.CassetteMiss, match="record it again"):
        replay.complete(REQ)
    other = P.Request("aviation/plain", trial=1, round=0, prompt="map it")
    assert P.request_key("fake", "m1", REQ) != P.request_key("fake", "m1", other)
    assert P.request_key("fake", "m1", REQ) != P.request_key("fake", "m2", REQ)


def test_a_cassette_refuses_anything_that_looks_like_a_key(tmp_path: Path) -> None:
    for text in (
        "sk-" + "a1B2" * 8,
        "Authorization: Bearer " + "x" * 30,
        "api_key = abcdefgh12345",
    ):
        assert cassette.looks_like_secret(text)
        with pytest.raises(ValueError, match="looks like"):
            cassette.append(tmp_path / "c.jsonl", {"key": "k", "response": text})
    assert not cassette.looks_like_secret("downlinks:\n  - {from: A.b, to: C.d}")


def test_the_value_of_a_key_in_the_environment_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "hunter2-hunter2")
    with pytest.raises(ValueError):
        cassette.append(tmp_path / "c.jsonl", {"key": "k", "response": "x hunter2-hunter2 y"})


def test_committed_cassettes_hold_no_keys() -> None:
    root = Path(__file__).resolve().parent.parent / "experiments" / "ai_mapping" / "cassettes"
    for path in root.glob("*.jsonl"):
        assert not cassette.looks_like_secret(path.read_text(encoding="utf-8")), path.name


def test_a_damaged_cassette_names_the_line(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    path.write_text('{"key": "a", "response": "x"}\nnot json\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        cassette.load(path)


def test_live_providers_need_a_key_and_the_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=lambda **kw: None))
    with pytest.raises(P.ProviderError, match="ANTHROPIC_API_KEY"):
        P.AnthropicProvider("m").complete(REQ)
    monkeypatch.setitem(sys.modules, "openai", None)  # None makes the import fail
    monkeypatch.setenv("OPENAI_API_KEY", "k" * 12)
    with pytest.raises(P.ProviderError, match="uv sync --extra ai"):
        P.OpenAIProvider("m").complete(REQ)
    with pytest.raises(P.ProviderError, match="--model is required"):
        P.live_provider("ollama", None)


def test_anthropic_provider_with_a_stub_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, object]] = []

    class Messages:
        def create(self, **kw: object) -> object:
            seen.append(kw)
            if "temperature" in kw:
                raise ValueError("temperature is not supported for this model")
            block = SimpleNamespace(type="text", text="name: y")
            return SimpleNamespace(
                content=[block], usage=SimpleNamespace(input_tokens=5, output_tokens=3)
            )

    stub = SimpleNamespace(Anthropic=lambda api_key: SimpleNamespace(messages=Messages()))
    monkeypatch.setitem(sys.modules, "anthropic", stub)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k" * 12)
    done = P.AnthropicProvider("m").complete(REQ)
    assert (done.text, done.tokens_in, done.tokens_out) == ("name: y", 5, 3)
    assert "temperature" in seen[0] and "temperature" not in seen[1]  # retried without it


def test_ollama_provider_over_loopback_http() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body["options"]["temperature"] == 0 and body["stream"] is False
            out = json.dumps(
                {"message": {"content": "name: z"}, "prompt_eval_count": 4, "eval_count": 2}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        done = P.OllamaProvider("m", f"http://127.0.0.1:{server.server_port}").complete(REQ)
    finally:
        server.shutdown()
    assert (done.text, done.tokens_in, done.tokens_out) == ("name: z", 4, 2)
    with pytest.raises(P.ProviderError, match="cannot reach"):
        P.OllamaProvider("m", "http://127.0.0.1:9").complete(REQ)
