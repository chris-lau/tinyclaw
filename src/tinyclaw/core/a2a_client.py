"""A2A client helper with W3C tracecontext propagation.

Every tinyclaw agent-to-agent call goes through this class so that:

* the remote Agent Card is discovered properly (``/.well-known/agent-card.json``),
* the current span's ``traceparent`` rides in message metadata → one
  distributed trace across the whole agent chain,
* the final task state and artifacts are collected into a simple result.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import AgentCard, DataPart, Message, Part, Role, Task, TextPart

from .observability import tracing


@dataclass
class SendResult:
    task: Task | None = None
    reply_text: str = ""
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    state: str = "unknown"

    @property
    def data(self) -> dict[str, Any]:
        """Last data artifact (agents publish plan first, final result last)."""
        for a in reversed(self.artifacts):
            if isinstance(a.get("data"), dict):
                return a["data"]
        return {}


class A2ACaller:
    """Thin, cached wrapper over the a2a-sdk client for agent-to-agent calls."""

    def __init__(self, base_url: str, httpx_client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx_client or httpx.AsyncClient(timeout=30.0)
        self._card: AgentCard | None = None
        self._factory = ClientFactory(ClientConfig(streaming=False, httpx_client=self._http))

    async def card(self) -> AgentCard:
        if self._card is None:
            resolver = A2ACardResolver(httpx_client=self._http, base_url=self.base_url)
            self._card = await resolver.get_agent_card()
        return self._card

    async def send_text(
        self,
        text: str,
        *,
        data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
    ) -> SendResult:
        """Send one message (non-streaming) and drain the event iterator."""
        parts: list[Part] = [Part(root=TextPart(text=text))]
        if data is not None:
            parts.append(Part(root=DataPart(data=data)))
        message = Message(
            message_id=uuid.uuid4().hex,
            role=Role.agent,
            parts=parts,
            task_id=task_id,
            context_id=context_id,
            metadata=tracing.inject_trace_metadata(metadata),
        )
        card = await self.card()
        client = self._factory.create(card)
        result = SendResult()
        async for item in client.send_message(message):
            item_task, item_event = item if isinstance(item, tuple) else (None, item)
            if isinstance(item, Message):
                result.reply_text = "".join(p.root.text for p in item.parts if isinstance(p.root, TextPart))
                continue
            if item_task is not None:
                result.task = item_task
                result.state = item_task.status.state.value if item_task.status else "unknown"
            if item_event is not None:
                ev = getattr(item_event, "artifact", None)
                if ev is not None:
                    result.artifacts.append(_artifact_to_dict(ev))
        # Non-streaming message/send returns artifacts embedded in the final Task.
        for artifact in getattr(result.task, "artifacts", None) or []:
            result.artifacts.append(_artifact_to_dict(artifact))
        return result

    async def aclose(self) -> None:
        await self._http.aclose()


def _artifact_to_dict(artifact: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"name": getattr(artifact, "name", "") or ""}
    for part in getattr(artifact, "parts", []) or []:
        root = getattr(part, "root", None)
        if isinstance(root, TextPart):
            out["text"] = out.get("text", "") + root.text
        elif isinstance(root, DataPart):
            out["data"] = root.data
    return out
