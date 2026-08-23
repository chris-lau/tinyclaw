"""Provider-agnostic LLM layer.

``MockLLM`` is the default: deterministic, keyless, and sufficient to run the
entire platform end-to-end (CI runs on it). ``OpenAIChat`` and
``AnthropicChat`` are thin adapters that keep the same interface and record
GenAI spans for every call.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Protocol

from ..observability import tracing

log = logging.getLogger("tinyclaw.llm")


@dataclass(frozen=True)
class LLMResponse:
    text: str
    provider: str
    model: str


class LLMClient(Protocol):
    provider: str
    model: str

    async def complete(self, system: str, user: str, agent: str = "") -> LLMResponse: ...


# ---------------------------------------------------------------------------
# Mock: deterministic, keyword-driven. Good enough to demo every scenario
# path without any API key, which is exactly why CI uses it.
# ---------------------------------------------------------------------------

_AMOUNT_RE = re.compile(r"\$?\s*([\d,]+(?:\.\d{2})?)")
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class MockLLM:
    provider = "mock"
    model = "mock-1"

    async def complete(self, system: str, user: str, agent: str = "") -> LLMResponse:
        text = self._respond(system, user, agent)
        tracing.record_llm_span(self.provider, self.model, f"{system}\n\n{user}", text, agent)
        return LLMResponse(text=text, provider=self.provider, model=self.model)

    def _respond(self, system: str, user: str, agent: str) -> str:
        # Structured-extraction requests: mirror back any JSON found in input.
        if "json" in system.lower() or "extract" in system.lower():
            m = _JSON_RE.search(user)
            if m:
                try:
                    json.loads(m.group(0))
                    return m.group(0)
                except json.JSONDecodeError:
                    pass
            amounts = _AMOUNT_RE.findall(user.replace(",", ""))
            amount = float(amounts[0]) if amounts else 0.0
            return json.dumps({"amount": amount, "raw": user[:400]})

        # Conversational requests: short deterministic reply naming the agent.
        first = user.strip().splitlines()[0][:120] if user.strip() else "(empty)"
        return f"[{agent or 'agent'}] Understood: {first}. (mock LLM — deterministic response)"


# ---------------------------------------------------------------------------
# Real adapters (activated via TINYCLAW_LLM_PROVIDER + API key envs)
# ---------------------------------------------------------------------------


class OpenAIChat:
    provider = "openai"

    def __init__(self, model: str = "gpt-4o") -> None:
        from openai import AsyncOpenAI  # lazy import: optional dependency

        self._client = AsyncOpenAI()
        self.model = model

    async def complete(self, system: str, user: str, agent: str = "") -> LLMResponse:
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        text = resp.choices[0].message.content or ""
        tracing.record_llm_span(self.provider, self.model, f"{system}\n\n{user}", text, agent)
        return LLMResponse(text=text, provider=self.provider, model=self.model)


class AnthropicChat:
    provider = "anthropic"

    def __init__(self, model: str = "claude-sonnet-4-5") -> None:
        import anthropic  # lazy import: optional dependency

        self._client = anthropic.AsyncAnthropic()
        self.model = model

    async def complete(self, system: str, user: str, agent: str = "") -> LLMResponse:
        resp = await self._client.messages.create(
            model=self.model, max_tokens=1024, system=system, messages=[{"role": "user", "content": user}]
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        tracing.record_llm_span(self.provider, self.model, f"{system}\n\n{user}", text, agent)
        return LLMResponse(text=text, provider=self.provider, model=self.model)


def build_llm(provider: str, model: str) -> LLMClient:
    if provider == "openai":
        return OpenAIChat(model)
    if provider == "anthropic":
        return AnthropicChat(model)
    if provider != "mock":
        log.warning("Unknown LLM provider %r — falling back to mock", provider)
    return MockLLM()
