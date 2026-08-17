"""OpenCode Go chat-completions client.

Two gateway quirks are load-bearing and both are worked around here:

1. Sending OpenAI's ``response_format`` hangs the request indefinitely. JSON is
   therefore enforced by prompt only, and parsed defensively (``extract_json``).
2. Models reason by default and emit thousands of hidden CoT tokens before the
   answer, which dominates latency. ``thinking: {"type": "disabled"}`` is always
   sent; models that ignore it (glm-*) simply cost more time.

Errors arrive HTTP-200-shaped as ``{"type": "error", "error": {...}}``, so a
client that only reads ``choices`` silently treats a failure as an empty success.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field

import httpx

BASE_URL = "https://opencode.ai/zen/go/v1"

# Not billed through us (subscription gateway), but we still want a comparable
# unit-economics number for the report. Rates are public list prices for the
# nearest equivalent commercial tier, used only to compute a *notional* cost.
NOTIONAL_RATES = {  # (usd per 1M input, usd per 1M output)
    "deepseek-v4-flash": (0.27, 1.10),
    "deepseek-v4-pro": (0.55, 2.19),
    "kimi-k2.6": (0.60, 2.50),
    "glm-5.1": (0.60, 2.20),
    "qwen3.7-plus": (0.40, 1.20),
}
DEFAULT_RATE = (0.50, 2.00)


class GatewayError(RuntimeError):
    """Base for gateway failures."""


class FatalGatewayError(GatewayError):
    """Not retryable: region lock, quota exhausted, bad auth."""


class RetryableGatewayError(GatewayError):
    """Rate limited or transient upstream failure."""


class EmptyCompletionError(GatewayError):
    """The model returned an empty body. Deterministic for a given input on this
    gateway, so retrying the same model is wasted wall-clock — the caller should
    fall through to a different model instead."""


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    by_model: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, model: str, pin: int, pout: int, secs: float) -> None:
        with self._lock:
            self.calls += 1
            self.input_tokens += pin
            self.output_tokens += pout
            self.seconds += secs
            m = self.by_model.setdefault(model, {"calls": 0, "in": 0, "out": 0, "secs": 0.0})
            m["calls"] += 1
            m["in"] += pin
            m["out"] += pout
            m["secs"] += secs

    def notional_usd(self) -> float:
        total = 0.0
        for model, m in self.by_model.items():
            rin, rout = NOTIONAL_RATES.get(model, DEFAULT_RATE)
            total += m["in"] / 1e6 * rin + m["out"] / 1e6 * rout
        return round(total, 4)

    def snapshot(self) -> dict:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "llm_seconds": round(self.seconds, 1),
            "notional_usd": self.notional_usd(),
            "by_model": {
                k: {**v, "secs": round(v["secs"], 1)} for k, v in self.by_model.items()
            },
        }


USAGE = Usage()


def _api_key() -> str:
    key = os.environ.get("OPENCODE_GO_API_KEY")
    if not key:
        raise FatalGatewayError("OPENCODE_GO_API_KEY is not set (see .env.example)")
    return key


def chat(
    prompt: str,
    *,
    model: str = "deepseek-v4-flash",
    system: str | None = None,
    max_tokens: int = 6000,
    temperature: float = 0.0,
    attempts: int = 4,
    timeout: float = 180.0,
) -> str:
    """Single-turn completion. Returns assistant text, or raises."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "thinking": {"type": "disabled"},
    }
    headers = {"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"}

    last: Exception | None = None
    for attempt in range(attempts):
        started = time.time()
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post(f"{BASE_URL}/chat/completions", json=payload, headers=headers)
            body = r.json()
        except Exception as exc:  # network / decode
            last = RetryableGatewayError(f"transport: {exc}")
            time.sleep(min(2**attempt, 30))
            continue

        # Errors are HTTP-200-shaped on this gateway.
        if isinstance(body, dict) and body.get("type") == "error":
            kind = (body.get("error") or {}).get("type", "")
            msg = (body.get("error") or {}).get("message", str(body))
            if kind in {"RegionError", "GoUsageLimitError"}:
                raise FatalGatewayError(f"{kind}: {msg}")
            last = RetryableGatewayError(f"{kind}: {msg}")
            time.sleep(min(2**attempt, 30))
            continue

        if r.status_code == 429:
            wait = float(r.headers.get("retry-after") or min(2**attempt * 5, 120))
            last = RetryableGatewayError("429 rate limited")
            time.sleep(min(wait, 120))
            continue
        if r.status_code in (401, 403):
            raise FatalGatewayError(f"auth {r.status_code}: {r.text[:200]}")
        if r.status_code >= 400:
            last = RetryableGatewayError(f"http {r.status_code}: {r.text[:200]}")
            time.sleep(min(2**attempt, 30))
            continue

        try:
            text = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            last = RetryableGatewayError(f"unexpected body: {str(body)[:200]}")
            time.sleep(min(2**attempt, 30))
            continue

        u = body.get("usage") or {}
        USAGE.add(
            model,
            int(u.get("prompt_tokens") or 0),
            int(u.get("completion_tokens") or 0),
            time.time() - started,
        )
        if not text.strip():
            raise EmptyCompletionError(f"{model} returned an empty completion")
        return text

    raise last or GatewayError("exhausted attempts")


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict | list:
    """Pull the first JSON object/array out of a model response.

    The gateway cannot be asked for JSON mode, so responses arrive as prose,
    fenced blocks, or bare JSON. Try each in the order they actually occur.
    """
    candidates: list[str] = []
    for m in _FENCE.finditer(text):
        candidates.append(m.group(1))
    candidates.append(text)
    for chunk in candidates:
        chunk = chunk.strip()
        for opener, closer in (("{", "}"), ("[", "]")):
            start = chunk.find(opener)
            end = chunk.rfind(closer)
            if start == -1 or end <= start:
                continue
            blob = chunk[start : end + 1]
            try:
                return json.loads(blob)
            except json.JSONDecodeError:
                # Trailing commas are the most common model slip.
                try:
                    return json.loads(re.sub(r",(\s*[}\]])", r"\1", blob))
                except json.JSONDecodeError:
                    continue
    raise ValueError(f"no JSON found in response: {text[:300]}")


def chat_json(prompt: str, **kw) -> dict | list:
    return extract_json(chat(prompt, **kw))
