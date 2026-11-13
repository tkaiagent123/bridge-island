"""Model providers. ``replay`` is the default and needs no network, no SDK and no key.

Live providers (``anthropic``, ``openai``, ``ollama``) are wrapped by :class:`RecordingProvider`,
which writes every response to a cassette so a later replay gives identical numbers. API keys are
read from environment variables only. The SDKs are imported inside ``complete`` so the core
package installs and runs without the optional ``ai`` extra.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from bridge_island.aimap import cassette

PARAMS: dict[str, Any] = {"temperature": 0, "max_tokens": 8000}
LIVE = ("anthropic", "openai", "ollama")


class ProviderError(RuntimeError):
    """A provider cannot be used (missing key, missing SDK, unreachable server)."""


@dataclass(frozen=True)
class Request:
    task_id: str
    trial: int
    round: int  # 0 for the first call, then 1.. for each repair
    prompt: str


@dataclass(frozen=True)
class Completion:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_s: float = 0.0


class Provider(Protocol):
    provider: str
    model: str

    def complete(self, request: Request) -> Completion: ...


def prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def request_key(provider: str, model: str, request: Request) -> str:
    """What identifies a recorded response: who answered, how, which sample, the exact prompt."""
    payload = {
        "provider": provider,
        "model": model,
        "params": PARAMS,
        "trial": request.trial,
        "prompt": prompt_sha256(request.prompt),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def cassette_name(provider: str, model: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in model)
    return f"{provider}__{safe}.jsonl"


class ReplayProvider:
    """Answers from a cassette. A request with no recorded answer is an error, never a guess."""

    def __init__(self, provider: str, model: str, path: Path) -> None:
        self.provider, self.model, self.path = provider, model, path
        self._records = cassette.load(path)

    def complete(self, request: Request) -> Completion:
        rec = self._records.get(request_key(self.provider, self.model, request))
        if rec is None:
            raise cassette.CassetteMiss(
                f"{self.path.name}: no recorded response for {request.task_id} trial "
                f"{request.trial} round {request.round}. The prompt or the settings changed since "
                "it was recorded; record it again with a live provider."
            )
        return Completion(
            rec["response"],
            rec.get("tokens_in", 0),
            rec.get("tokens_out", 0),
            rec.get("latency_s", 0.0),
        )


class RecordingProvider:
    """Wraps a live provider: replays what is already recorded, records what is new."""

    def __init__(self, inner: Provider, path: Path) -> None:
        self.inner, self.path = inner, path
        self.provider, self.model = inner.provider, inner.model
        self._records = cassette.load(path)

    def complete(self, request: Request) -> Completion:
        key = request_key(self.provider, self.model, request)
        rec = self._records.get(key)
        if rec is not None:
            return Completion(
                rec["response"],
                rec.get("tokens_in", 0),
                rec.get("tokens_out", 0),
                rec.get("latency_s", 0.0),
            )
        done = self.inner.complete(request)
        rec = {
            "key": key,
            "provider": self.provider,
            "model": self.model,
            "params": PARAMS,
            "task": request.task_id,
            "trial": request.trial,
            "round": request.round,
            "prompt_sha256": prompt_sha256(request.prompt),
            "response": done.text,
            "tokens_in": done.tokens_in,
            "tokens_out": done.tokens_out,
            "latency_s": round(done.latency_s, 3),
        }
        cassette.append(self.path, rec)
        self._records[key] = rec
        return done


def _key(env: str) -> str:
    value = os.environ.get(env)
    if not value:
        raise ProviderError(f"set the {env} environment variable (it is never written to disk)")
    return value


def _sdk(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ImportError:
        raise ProviderError(
            f"the {name} package is not installed; run: uv sync --extra ai"
        ) from None


def _mentions_temperature(exc: Exception) -> bool:
    return "temperature" in str(exc).lower()


class AnthropicProvider:
    provider = "anthropic"

    def __init__(self, model: str) -> None:
        self.model = model

    def complete(self, request: Request) -> Completion:
        client = _sdk("anthropic").Anthropic(api_key=_key("ANTHROPIC_API_KEY"))
        args: dict[str, Any] = {
            "model": self.model,
            "max_tokens": PARAMS["max_tokens"],
            "messages": [{"role": "user", "content": request.prompt}],
        }
        t0 = time.monotonic()
        try:
            resp = client.messages.create(temperature=PARAMS["temperature"], **args)
        except Exception as exc:  # some models do not accept a temperature
            if not _mentions_temperature(exc):
                raise
            resp = client.messages.create(**args)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return Completion(
            text, resp.usage.input_tokens, resp.usage.output_tokens, time.monotonic() - t0
        )


class OpenAIProvider:
    provider = "openai"

    def __init__(self, model: str) -> None:
        self.model = model

    def complete(self, request: Request) -> Completion:
        client = _sdk("openai").OpenAI(api_key=_key("OPENAI_API_KEY"))
        args: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        t0 = time.monotonic()
        try:
            resp = client.chat.completions.create(temperature=PARAMS["temperature"], **args)
        except Exception as exc:
            if not _mentions_temperature(exc):
                raise
            resp = client.chat.completions.create(**args)
        return Completion(
            resp.choices[0].message.content or "",
            resp.usage.prompt_tokens,
            resp.usage.completion_tokens,
            time.monotonic() - t0,
        )


class OllamaProvider:
    """A local model over HTTP (standard library only). Nothing leaves your machine."""

    provider = "ollama"

    def __init__(self, model: str, host: str | None = None) -> None:
        self.model = model
        self.host = (host or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")

    def complete(self, request: Request) -> Completion:
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": request.prompt}],
                "stream": False,
                "options": {"temperature": PARAMS["temperature"]},
            }
        ).encode()
        req = urllib.request.Request(  # noqa: S310  (the configured ollama host)
            f"{self.host}/api/chat", body, {"Content-Type": "application/json"}
        )
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:  # noqa: S310  (configured host)
                data = json.loads(resp.read())
        except OSError as exc:
            raise ProviderError(f"cannot reach ollama at {self.host}: {exc}") from exc
        return Completion(
            data["message"]["content"],
            data.get("prompt_eval_count", 0),
            data.get("eval_count", 0),
            time.monotonic() - t0,
        )


def live_provider(name: str, model: str | None) -> Provider:
    if not model:
        raise ProviderError(f"--model is required with --provider {name}")
    classes: dict[str, Any] = {
        "anthropic": AnthropicProvider,
        "openai": OpenAIProvider,
        "ollama": OllamaProvider,
    }
    return classes[name](model)  # type: ignore[no-any-return]
