"""A2A client helper with W3C tracecontext propagation + boundary enforcement.

Every tinyclaw agent-to-agent call goes through this class so that:

* the remote Agent Card is discovered properly (``/.well-known/agent-card.json``),
* the current span's ``traceparent`` rides in message metadata → one
  distributed trace across the whole agent chain,
* the final task state and artifacts are collected into a simple result,
* when wired to the gateway (Phase 2), the outbound message passes the
  boundary-hook policy decision point first: ``block`` raises, ``redact``
  masks PII in text and every payload field before anything is sent.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import AgentCard, DataPart, Message, Part, Role, Task, TextPart

from .observability import tracing

log = logging.getLogger("tinyclaw.a2a")


class HookBlockedError(Exception):
    """A boundary hook refused this outbound message."""

    def __init__(self, hook: str, detail: str = "") -> None:
        self.hook = hook
        self.detail = detail
        super().__init__(f"blocked at boundary by hook {hook!r}: {detail}")


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

    def __init__(
        self,
        base_url: str,
        httpx_client: httpx.AsyncClient | None = None,
        settings: Any = None,
        caller_name: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx_client or httpx.AsyncClient(timeout=30.0)
        self._card: AgentCard | None = None
        self._factory = ClientFactory(ClientConfig(streaming=False, httpx_client=self._http))
        self._settings = settings
        self._caller_name = caller_name
        self._hook_http: httpx.AsyncClient | None = None
        if settings is not None:
            self._hook_http = httpx.AsyncClient(
                base_url=settings.gateway_url,
                headers={"authorization": f"Bearer {settings.internal_token}"},
                timeout=5.0,
            )

    async def card(self) -> AgentCard:
        if self._card is None:
            resolver = A2ACardResolver(httpx_client=self._http, base_url=self.base_url)
            self._card = await resolver.get_agent_card()
        return self._card

    async def _enforce_hooks(
        self, text: str, data: dict[str, Any] | None, task_id: str | None
    ) -> tuple[str, dict[str, Any] | None]:
        """Consult the gateway's hook policy before anything leaves."""
        if self._hook_http is None:
            return text, data
        to_agent = ""
        try:
            to_agent = (await self.card()).name
        except Exception:
            pass
        try:
            r = await self._hook_http.post(
                "/internal/hooks/eval",
                json={
                    "from_agent": self._caller_name,
                    "to_agent": to_agent,
                    "text": text,
                    "data": data or {},
                    "task_id": task_id,
                },
            )
            r.raise_for_status()
            decision = r.json()
        except Exception:
            # Fail-open is deliberate and documented: the in-agent guardrails
            # (PII redaction pre-LLM) remain the inner defense line.
            log.debug("hook evaluation unavailable; sending unfiltered (fail-open)", exc_info=True)
            return text, data
        if decision.get("action") == "block":
            hook = (decision.get("annotations") or [{}])[0].get("hook", "unknown")
            raise HookBlockedError(hook, f"message to {to_agent or 'agent'} refused")
        if decision.get("action") == "redact":
            return decision.get("text", text), decision.get("data", data)
        return text, data

    async def send_text(
        self,
        text: str,
        *,
        data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
        hook_task_id: str | None = None,
    ) -> SendResult:
        """Send one message (non-streaming) and drain the event iterator.

        ``task_id`` continues an existing task on the receiving agent (resume).
        ``hook_task_id`` is business-task attribution for boundary-hook events
        only — it never enters the protocol message (specialists don't know
        the orchestrator's task id).
        """
        text, data = await self._enforce_hooks(text, data, hook_task_id or task_id)
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
