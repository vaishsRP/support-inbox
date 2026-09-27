"""Language model access through any OpenAI-compatible endpoint.

Groq's free tier by default, a second free model when the first is rate-limited,
and a local Ollama model if one is running. The provider is a base URL and a
model name in .env, not code. If every provider fails, callers get
ModelUnavailable and must queue the mail, never drop it.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from .config import env


class ModelUnavailable(RuntimeError):
    pass


class BadOutput(RuntimeError):
    pass


class ChatModel(Protocol):
    def chat(self, system: str, user: str, *, json_mode: bool = False, max_tokens: int = 700) -> str: ...


@dataclass
class Provider:
    name: str
    base_url: str
    model: str
    api_key: str


def providers_from_env() -> list[Provider]:
    out = []
    key = env("GROQ_API_KEY")
    base = env("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    if key:
        out.append(Provider("primary", base, env("LLM_MODEL", "openai/gpt-oss-120b"), key))
        second = env("LLM_SECOND_MODEL", "openai/gpt-oss-20b")
        if second:
            out.append(Provider("second", base, second, key))
    fb = env("LLM_FALLBACK_BASE_URL")
    if fb:
        out.append(Provider("local", fb, env("LLM_FALLBACK_MODEL", "llama3.1:8b"), "ollama"))
    return out


class OpenAICompatible:
    """Tries each provider in turn, with short backoff on rate limits."""

    def __init__(self, providers: list[Provider] | None = None, retries: int = 2, log=print):
        self.providers = providers if providers is not None else providers_from_env()
        self.retries = retries
        self.log = log
        self._clients = {}

    def _client(self, p: Provider):
        if p.name not in self._clients:
            from openai import OpenAI

            self._clients[p.name] = OpenAI(base_url=p.base_url, api_key=p.api_key, timeout=60, max_retries=0)
        return self._clients[p.name]

    def chat(self, system: str, user: str, *, json_mode: bool = False, max_tokens: int = 700) -> str:
        from openai import APIConnectionError, APIStatusError, RateLimitError

        errors = []
        for p in self.providers:
            for attempt in range(self.retries + 1):
                try:
                    kwargs = dict(
                        model=p.model,
                        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                        temperature=0.2,
                        max_tokens=max_tokens,
                    )
                    if json_mode:
                        kwargs["response_format"] = {"type": "json_object"}
                    if "gpt-oss" in p.model:
                        kwargs["reasoning_effort"] = "low"
                    resp = self._client(p).chat.completions.create(**kwargs)
                    return (resp.choices[0].message.content or "").strip()
                except RateLimitError as e:
                    errors.append(f"{p.name}: rate limited")
                    wait = min(20, 2 ** (attempt + 1))
                    self.log(f"[llm] {p.model} rate limited, waiting {wait}s")
                    time.sleep(wait)
                except APIConnectionError as e:
                    errors.append(f"{p.name}: unreachable ({e.__class__.__name__})")
                    break
                except APIStatusError as e:
                    errors.append(f"{p.name}: HTTP {e.status_code}")
                    if e.status_code < 500:
                        break
                    time.sleep(2)
        raise ModelUnavailable("; ".join(errors) or "no model configured")


def chat_json(model: ChatModel, system: str, user: str, max_tokens: int = 700) -> dict:
    """Ask for JSON. One retry on malformed output, then BadOutput."""
    for attempt in range(2):
        raw = model.chat(system, user, json_mode=True, max_tokens=max_tokens)
        try:
            start, end = raw.index("{"), raw.rindex("}") + 1
            return json.loads(raw[start:end])
        except ValueError:
            if attempt == 1:
                raise BadOutput(f"not JSON: {raw[:200]!r}")
    raise BadOutput("unreachable")


class StubModel:
    """Deterministic stand-in for tests and CI. Never calls the network."""

    def __init__(self, reply: str | Callable[[str, str], str]):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def chat(self, system: str, user: str, *, json_mode: bool = False, max_tokens: int = 700) -> str:
        self.calls.append((system, user))
        return self.reply(system, user) if callable(self.reply) else self.reply
