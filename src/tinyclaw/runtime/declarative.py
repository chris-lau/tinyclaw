"""Declarative agents: Agent Studio definitions turned into live A2A servers.

A definition is pure data (JSON) — name, description, system prompt, model,
bound tools, risk class, policy bindings. The runtime turns it into a real
A2A agent with its own Agent Card, so studio-built agents are protocol peers
of the coded scenario agents, discoverable exactly the same way.

Bound tools EXECUTE: the registry (gateway DB) defines mock tools (configured
response samples) and http tools (real calls behind an SSRF guard). Tool defs
are fetched per request, so registry edits apply immediately; every execution
lands in the audit chain and the live event feed.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
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
        self._last_user_text = ""

    async def handle(self, request: AgentRequest, updater: TaskUpdater) -> None:
        user_text = request.text or json.dumps(request.data)
        self._last_user_text = user_text
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

        # Tool phase: bound tools actually execute (registry defs fetched from
        # the gateway per request, so edits apply immediately). Results join
        # the reply and every execution lands in the audit chain.
        tool_results: list[dict[str, Any]] = []
        bound = [t if isinstance(t, str) else t.get("name") for t in self.definition.get("tools", [])]
        if bound:
            tool_results = await self._run_tools(bound, request.task_id)
        tool_note = ""
        if tool_results:
            lines = [f"[{r['tool']}] {r['output']}" for r in tool_results]
            tool_note = "\n\nTool results:\n" + "\n".join(lines)
            await self.artifact(
                updater,
                "tool.results",
                {"results": tool_results},
                text="; ".join(f"{r['tool']}={'ok' if r['ok'] else 'failed'}" for r in tool_results),
            )

        await self.artifact(updater, "reply", {"reply": resp.text}, text=resp.text[:300])
        await updater.complete(updater.new_agent_message(parts=[TextPart(text=resp.text + tool_note)]))

    async def _run_tools(self, bound: list[str], task_id: str | None) -> list[dict[str, Any]]:
        from .tools import execute_tool

        registry: dict[str, dict[str, Any]] = {}
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.gateway_url,
                headers={"authorization": f"Bearer {self.settings.internal_token}"},
                timeout=3.0,
            ) as http:
                r = await http.get("/api/studio/tools")
                r.raise_for_status()
                registry = {t["name"]: t for t in r.json()}
        except Exception:
            return []

        results: list[dict[str, Any]] = []
        for name in bound:
            tool = registry.get(name)
            if not tool:
                results.append({"tool": name, "ok": False, "output": "not in registry"})
                continue
            outcome = execute_tool(tool, self._last_user_text)
            results.append({"tool": name, "ok": outcome.ok, "output": outcome.output, "ms": outcome.ms})
            await self.events.report(
                "tool.executed",
                self.spec.name,
                {"tool": name, "ok": outcome.ok, "ms": outcome.ms, "high_risk": tool.get("high_risk", False)},
                task_id=task_id,
            )
            try:
                async with httpx.AsyncClient(
                    base_url=self.settings.gateway_url,
                    headers={"authorization": f"Bearer {self.settings.internal_token}"},
                    timeout=3.0,
                ) as http:
                    await http.post(
                        "/internal/audit",
                        json={
                            "actor": f"agent:{self.spec.name}",
                            "action": "tool.executed",
                            "subject": task_id or name,
                            "decision": "high_risk" if tool.get("high_risk") else "ok",
                            "details": {"tool": name, "ok": outcome.ok, "ms": outcome.ms},
                        },
                    )
            except Exception:
                pass
        return results
