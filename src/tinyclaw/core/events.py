"""Event reporting from agents to the gateway control plane.

Agents are independent A2A servers; the gateway observes them through these
events (POSTed to ``/internal/events``) and fans them out to the dashboard
over SSE, records governance KPIs, and persists an event history.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from .config import Settings


class EventReporter:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: httpx.AsyncClient | None = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._settings.gateway_url,
                headers={"authorization": f"Bearer {self._settings.internal_token}"},
                timeout=5.0,
            )
        return self._client

    async def report(
        self, type: str, agent: str, data: dict[str, Any] | None = None, task_id: str | None = None
    ) -> None:
        """Fire a structured event. Failures are logged and swallowed: the
        observation plane must never take down the agent."""
        from opentelemetry.trace import get_current_span

        sc = get_current_span().get_span_context()
        payload = {
            "id": uuid.uuid4().hex[:12],
            "ts": time.time(),
            "type": type,  # e.g. "a2a.message", "policy.decision", "guardrail.hit", "task.state"
            "agent": agent,
            "task_id": task_id,
            "trace_id": f"{sc.trace_id:032x}" if sc.is_valid else None,
            "data": data or {},
        }
        try:
            client = await self._http()
            await client.post("/internal/events", json=payload)
        except Exception:  # pragma: no cover - observation must be non-fatal
            import logging

            logging.getLogger("tinyclaw.events").debug("event report failed", exc_info=True)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
