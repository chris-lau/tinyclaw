"""Declarative agents: Agent Studio definitions turned into live A2A servers.

A definition is pure data (JSON) — name, description, system prompt, model,
declared tools, risk class, policy bindings. The runtime turns it into a real
A2A agent with its own Agent Card, so studio-built agents are protocol peers
of the coded scenario agents, discoverable exactly the same way.

Tools are *declared* for governance (the card + deploy gate reason about
them); execution wiring is mock in this demo and documented as the roadmap.
"""

from __future__ import annotations

import json
from typing import Any

from a2a.server.tasks import TaskUpdater
from a2a.types import TextPart

from ..core.agent import AgentRequest, AgentSpec, TinyclawExecutor
from ..core.config import Settings
from ..core.governance.guardrails import redact_pii, scan_injection
from ..core.llm import build_llm


class DeclarativeExecutor(TinyclawExecutor):
    def __init__(self, definition: dict[str, Any], settings: Settings, port: int) -> None:
        super().__init__(
            AgentSpec(
                name=definition["name"],
                description=definition.get("description", ""),
                url=f"http://127.0.0.1:{port}",
                version=f"{definition.get('version', 1)}.0.0",
                skills=definition.get("skills") or [definition["name"]],
            ),
            settings,
        )
        self.definition = definition
        self.llm = build_llm(settings.llm_provider, definition.get("model", settings.llm_model))

    async def handle(self, request: AgentRequest, updater: TaskUpdater) -> None:
        user_text = request.text or json.dumps(request.data)
        clean, report = redact_pii(user_text)
        flags = scan_injection(user_text)
        if isinstance(clean, str) is False:  # redact_pii on a plain str returns str
            clean = str(clean)
        if report.redactions or flags:
            await self.events.report(
                "guardrail.hit",
                self.spec.name,
                {"redactions": report.redactions, "injection_patterns": flags},
                task_id=request.task_id,
            )

        resp = await self.llm.complete(
            self.definition.get("system_prompt", "You are a helpful agent."), clean, agent=self.spec.name
        )
        await self.artifact(updater, "reply", {"reply": resp.text}, text=resp.text[:300])
        await updater.complete(updater.new_agent_message(parts=[TextPart(text=resp.text)]))
