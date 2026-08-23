"""Gateway control plane.

One FastAPI service that:

* observes every agent (agents POST events here; the UI consumes them over SSE),
* is the **single writer** of the hash-chained audit log,
* issues signed **execution permits** (auto after policy pass; human only after
  a recorded human decision) — the executor refuses tokens it cannot verify,
* hosts the human **approval queue** and resumes parked A2A tasks,
* computes governance KPIs, serves scenario/policy metadata, and submits
  playground requests to scenario orchestrators.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from ..core.a2a_client import A2ACaller
from ..core.config import Settings
from ..core.hitl.tokens import Permit, issue_permit
from ..core.observability import tracing
from .db import Database

GENESIS = "0" * 64
SCENARIOS_DIR = Path(__file__).parent.parent / "scenarios"


def load_scenarios() -> dict[str, dict[str, Any]]:
    packs: dict[str, dict[str, Any]] = {}
    docker = bool(os.environ.get("TINYCLAW_DOCKER"))
    for manifest in sorted(SCENARIOS_DIR.glob("*/scenario.yaml")):
        data = yaml.safe_load(manifest.read_text()) or {}
        if not data.get("name"):
            continue
        if docker:
            # Inside compose, agents live on separate containers: swap the
            # local-dev 127.0.0.1 hosts for service names (same ports).
            def _dockerize(u: str, name: str) -> str:
                return u.replace("127.0.0.1", name) if isinstance(u, str) else u

            data["orchestrator"]["url"] = _dockerize(data["orchestrator"]["url"], "orchestrator")
            for a in data.get("agents", []):
                a["url"] = _dockerize(a["url"], a["name"])
        packs[data["name"]] = {**data, "dir": manifest.parent}
    return packs


class GatewayState:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = Database(settings.database_path)
        self.subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self.scenarios = load_scenarios()
        self.callers: dict[str, A2ACaller] = {}

    def caller(self, url: str) -> A2ACaller:
        if url not in self.callers:
            self.callers[url] = A2ACaller(url)
        return self.callers[url]

    async def broadcast(self, event: dict[str, Any]) -> None:
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def audit(self, actor: str, action: str, subject: str, decision: str = "", **details: Any) -> dict[str, Any]:
        entry = self.db.append_audit(
            {
                "id": uuid.uuid4().hex[:12],
                "ts": time.time(),
                "actor": actor,
                "action": action,
                "subject": subject,
                "decision": decision,
                "details": details,
            }
        )
        await self.broadcast(
            {
                "id": uuid.uuid4().hex[:8],
                "ts": time.time(),
                "type": "audit.append",
                "agent": "gateway",
                "data": {
                    "actor": actor,
                    "action": action,
                    "subject": subject,
                    "decision": decision,
                    "hash": entry["hash"][:12],
                },
            }
        )
        return entry


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    state: dict[str, GatewayState] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        tracing.setup_tracing("tinyclaw-gateway", settings.otlp_endpoint)
        gw = GatewayState(settings)
        state["gw"] = gw
        await gw.audit("gateway", "gateway.start", "tinyclaw", "ok", scenarios=list(gw.scenarios))
        yield
        for caller in gw.callers.values():
            await caller.aclose()

    app = FastAPI(title="tinyclaw gateway", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def gw() -> GatewayState:
        return state["gw"]

    def authorized(request: Request) -> bool:
        header = request.headers.get("authorization", "")
        return header == f"Bearer {settings.internal_token}"  # demo-grade static token

    # ------------------------------------------------------------------ internal

    @app.post("/internal/events")
    async def internal_events(request: Request) -> dict[str, str]:
        if not authorized(request):
            raise HTTPException(401, "bad internal token")
        ev = await request.json()
        gw().db.insert_event(ev)
        await gw().broadcast(ev)
        # keep the task mirror fresh off agent-reported states
        if ev.get("type") == "task.state" and ev.get("task_id"):
            t = gw().db.get_task(ev["task_id"])
            if t:
                gw().db.upsert_task(
                    {
                        **t,
                        "state": ev["data"].get("state", t["state"]),
                        "current_agent": ev.get("agent", t["current_agent"]),
                    }
                )
        return {"ok": "1"}

    @app.post("/internal/audit")
    async def internal_audit(request: Request) -> dict[str, str]:
        if not authorized(request):
            raise HTTPException(401, "bad internal token")
        body = await request.json()
        entry = gw().db.append_audit(body)
        await gw().broadcast(
            {
                "id": uuid.uuid4().hex[:8],
                "ts": time.time(),
                "type": "audit.append",
                "agent": body.get("actor", "agent"),
                "data": {
                    "actor": body.get("actor"),
                    "action": body.get("action"),
                    "subject": body.get("subject"),
                    "decision": body.get("decision", ""),
                    "hash": entry["hash"][:12],
                },
            }
        )
        return {"hash": entry["hash"]}

    @app.post("/internal/tasks")
    async def internal_tasks(request: Request) -> dict[str, str]:
        if not authorized(request):
            raise HTTPException(401, "bad internal token")
        gw().db.upsert_task(await request.json())
        return {"ok": "1"}

    @app.post("/internal/permits")
    async def internal_permits(request: Request) -> dict[str, Any]:
        """Orchestrator asks for execution authorization after policy evaluation.

        ``route=auto``  → audit + signed permit returned immediately.
        ``route=human`` → approval request created, parked in the human queue.
        """
        if not authorized(request):
            raise HTTPException(401, "bad internal token")
        b = await request.json()
        task_id, action, route = b["task_id"], b["action"], b.get("route", "human")
        await gw().audit(
            "agent:policy",
            "policy.route",
            task_id,
            route,
            target_action=action,
            tier=b.get("tier"),
            policy_decision=b.get("policy_decision"),
            hits=b.get("hits"),
        )
        if route == "deny":
            await gw().audit("gateway", "execution.denied", task_id, "deny", target_action=action)
            return {"route": "deny", "reason": b.get("policy_decision", {}).get("summary", "denied by policy")}
        if route == "auto":
            permit = issue_permit(
                settings.approval_secret, Permit(task_id=task_id, action=action, route="auto", tier=b.get("tier", 1))
            )
            await gw().audit("gateway", "permit.issue", task_id, "auto", target_action=action)
            return {"route": "auto", "token": permit}
        approval_id = f"apr_{uuid.uuid4().hex[:10]}"
        gw().db.create_approval(
            {
                "id": approval_id,
                "ts": time.time(),
                "task_id": task_id,
                "context_id": b.get("context_id"),
                "orchestrator_url": b.get("orchestrator_url"),
                "scenario": b.get("scenario"),
                "subject": b.get("subject"),
                "amount": b.get("amount"),
                "tier": b.get("tier"),
                "action": action,
                "context_packet": b.get("context_packet") or {},
            }
        )
        await gw().audit(
            "gateway", "approval.requested", task_id, "pending", approval_id=approval_id, target_action=action
        )
        await gw().broadcast(
            {
                "id": uuid.uuid4().hex[:8],
                "ts": time.time(),
                "type": "approval.created",
                "agent": "gateway",
                "task_id": task_id,
                "data": {"approval_id": approval_id, "subject": b.get("subject"), "amount": b.get("amount")},
            }
        )
        return {"route": "human", "approval_id": approval_id}

    # ------------------------------------------------------------------- public

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "scenarios": list(gw().scenarios),
            "llm": settings.llm_provider,
            "otlp": bool(settings.otlp_endpoint),
        }

    @app.get("/api/scenarios")
    async def scenarios() -> list[dict[str, Any]]:
        out = []
        for name, s in gw().scenarios.items():
            out.append(
                {
                    "name": name,
                    "description": s.get("description", ""),
                    "orchestrator": s.get("orchestrator", {}),
                    "agents": s.get("agents", []),
                }
            )
        return out

    @app.get("/api/agents")
    async def agents() -> list[dict[str, Any]]:
        """Live Agent Cards, fetched from each registered agent (discovery)."""
        out: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=5.0) as http:
            for scen in gw().scenarios.values():
                for a in scen.get("agents", []):
                    try:
                        r = await http.get(f"{a['url']}/.well-known/agent-card.json")
                        out.append({"live": True, "card": r.json(), "scenario": scen["name"]})
                    except Exception:
                        out.append(
                            {"live": False, "card": {"name": a["name"], "url": a["url"]}, "scenario": scen["name"]}
                        )
        for d in gw().db.list_agent_defs():
            out.append(
                {
                    "live": d["status"] == "live",
                    "card": {
                        "name": d["name"],
                        "version": f"v{d['version']}",
                        "description": d["definition"].get("description", ""),
                        "studio": True,
                    },
                    "scenario": "studio",
                    "status": d["status"],
                }
            )
        return out

    @app.get("/api/tasks")
    async def tasks() -> list[dict[str, Any]]:
        return gw().db.list_tasks()

    @app.get("/api/tasks/{task_id}")
    async def task_detail(task_id: str) -> dict[str, Any]:
        t = gw().db.get_task(task_id)
        if not t:
            raise HTTPException(404, "unknown task")
        return {"task": t, "events": gw().db.recent_events(500, task_id=task_id)}

    @app.get("/api/approvals")
    async def approvals(status: str | None = None) -> list[dict[str, Any]]:
        return gw().db.list_approvals(status)

    @app.post("/api/approvals/{approval_id}/decision")
    async def decide(approval_id: str, body: dict[str, Any]) -> dict[str, Any]:
        approval = gw().db.get_approval(approval_id)
        if not approval:
            raise HTTPException(404, "unknown approval")
        if approval["status"] != "pending":
            raise HTTPException(409, f"already {approval['status']}")
        decision = body.get("decision")
        if decision not in ("approve", "reject"):
            raise HTTPException(422, "decision must be approve|reject")
        approver = body.get("approver", "local-user")
        comment = body.get("comment", "")
        gw().db.decide_approval(approval_id, decision, approver, comment)
        await gw().audit(
            "human:" + approver,
            "approval.decide",
            approval["task_id"],
            decision,
            approval_id=approval_id,
            comment=comment,
            amount=approval.get("amount"),
        )

        token = None
        if decision == "approve":
            token = issue_permit(
                settings.approval_secret,
                Permit(
                    task_id=approval["task_id"],
                    action=approval["action"],
                    route="human",
                    tier=approval.get("tier") or 1,
                    approval_id=approval_id,
                    approver=approver,
                ),
            )
            await gw().audit(
                "gateway", "permit.issue", approval["task_id"], "human", approval_id=approval_id, approver=approver
            )

        result: dict[str, Any] = {"decision": decision}

        # Studio deployments: the approved action is hosting an agent definition.
        if approval.get("action") == "agent.deploy":
            if decision == "approve":
                agent_name = str(approval["task_id"]).removeprefix("deploy:")
                result["deploy"] = await _deploy_agent_def(agent_name, approver=approver)
            await gw().broadcast(
                {
                    "id": uuid.uuid4().hex[:8],
                    "ts": time.time(),
                    "type": "approval.decided",
                    "agent": "gateway",
                    "task_id": approval["task_id"],
                    "data": {"approval_id": approval_id, "decision": decision, "approver": approver},
                }
            )
            return result

        # Resume the parked A2A task (input-required) with the human decision.
        if approval.get("orchestrator_url"):
            try:
                r = (
                    await gw()
                    .caller(approval["orchestrator_url"])
                    .send_text(
                        f"human decision: {decision}" + (f" — {comment}" if comment else ""),
                        task_id=approval["task_id"],
                        context_id=approval.get("context_id"),
                        metadata={
                            "tinyclaw.decision": decision,
                            "tinyclaw.approver": approver,
                            "tinyclaw.permit": token or "",
                            "tinyclaw.approval_id": approval_id,
                            "tinyclaw.context": approval["context_packet"],
                        },
                    )
                )
                result["task_state"] = r.state
                result["reply"] = r.reply_text[:500]
            except Exception as exc:
                result["resume_error"] = str(exc)
        await gw().broadcast(
            {
                "id": uuid.uuid4().hex[:8],
                "ts": time.time(),
                "type": "approval.decided",
                "agent": "gateway",
                "task_id": approval["task_id"],
                "data": {"approval_id": approval_id, "decision": decision, "approver": approver},
            }
        )
        return result

    @app.get("/api/audit")
    async def audit(limit: int = 200) -> list[dict[str, Any]]:
        return gw().db.audit_entries(limit)

    @app.get("/api/audit/verify")
    async def audit_verify() -> dict[str, Any]:
        return gw().db.audit_verify()

    @app.get("/api/events")
    async def recent_events(limit: int = 100) -> list[dict[str, Any]]:
        return gw().db.recent_events(limit)

    @app.get("/api/events/stream")
    async def event_stream() -> StreamingResponse:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        gw().subscribers.append(queue)

        async def gen() -> AsyncIterator[str]:
            for ev in reversed(gw().db.recent_events(30)):
                yield f"data: {json.dumps(ev)}\n\n"
            yield f"data: {json.dumps({'type': 'hello', 'data': {'subscribers': len(gw().subscribers)}})}\n\n"
            try:
                while True:
                    ev = await queue.get()
                    yield f"data: {json.dumps(ev)}\n\n"
            finally:
                gw().subscribers.remove(queue)

        return StreamingResponse(
            gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    @app.get("/api/kpis")
    async def kpis() -> dict[str, Any]:
        audit = gw().db.audit_entries(1000)
        executed = [a for a in audit if a["action"] == "execution.executed"]
        auto_n = sum(1 for a in executed if (a["details"] or {}).get("route") == "auto")
        human_n = sum(1 for a in executed if (a["details"] or {}).get("route") == "human")
        approvals_all = gw().db.list_approvals()
        decided = [a for a in approvals_all if a.get("decided_at")]
        latencies = [a["decided_at"] - a["ts"] for a in decided if a.get("decided_at")]
        denials = [a for a in audit if a["decision"] == "deny"]
        guardrail_hits = gw().db.recent_events(1000)
        g_hits = [e for e in guardrail_hits if e["type"] == "guardrail.hit"]
        total = auto_n + human_n
        return {
            "executions": {"total": total, "auto": auto_n, "human": human_n},
            "autonomy_rate": round(auto_n / total, 3) if total else None,
            "escalation_rate": round(human_n / total, 3) if total else None,
            "mean_approval_latency_s": round(sum(latencies) / len(latencies), 2) if latencies else None,
            "policy_denials": len(denials),
            "guardrail_hits": len(g_hits),
            "pending_approvals": sum(1 for a in approvals_all if a["status"] == "pending"),
        }

    @app.get("/api/policies")
    async def policies() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for scen in gw().scenarios.values():
            for rel in scen.get("policies", []):
                path: Path = scen["dir"] / rel
                if path.exists():
                    out.append({"scenario": scen["name"], "file": rel, "yaml": yaml.safe_load(path.read_text())})
        return out

    @app.post("/api/playground/submit")
    async def playground_submit(body: dict[str, Any]) -> list[dict[str, Any]]:
        """Submit one or more free-form requests to a scenario orchestrator."""
        scenario_name = body.get("scenario")
        scen = gw().scenarios.get(scenario_name)
        if not scen:
            raise HTTPException(404, f"unknown scenario {scenario_name!r}")
        orch = scen["orchestrator"]["url"]

        async def one(payload: dict[str, Any]) -> dict[str, Any]:
            with tracing.span("playground.submit", scenario=scenario_name):
                r = (
                    await gw()
                    .caller(orch)
                    .send_text(
                        payload.get("title", "playground request"),
                        data=payload,
                        metadata={"tinyclaw.requester": payload.get("requester", "playground@local")},
                    )
                )
            return {
                "title": payload.get("title"),
                "state": r.state,
                "task_id": r.task.id if r.task else None,
                "context_id": r.context_id if hasattr(r, "context_id") else None,
                "reply": r.reply_text[:400],
                "data": r.data,
            }

        results = await asyncio.gather(*(one(p) for p in body.get("requests", [])), return_exceptions=True)
        return [r if not isinstance(r, Exception) else {"error": str(r)} for r in results]

    # ------------------------------------------------------- Agent Studio

    HIGH_RISK_TOOLS = {"email.send", "payments.refund", "http.request", "slack.post"}
    TOOL_CATALOG = [
        {"name": "flights.search", "high_risk": False},
        {"name": "calendar.read", "high_risk": False},
        {"name": "policy.lookup", "high_risk": False},
        {"name": "http.request", "high_risk": True},
        {"name": "email.send", "high_risk": True},
        {"name": "payments.refund", "high_risk": True},
    ]

    @app.get("/api/studio/tools")
    async def studio_tools() -> list[dict[str, Any]]:
        return TOOL_CATALOG

    @app.get("/api/studio/agents")
    async def studio_agents() -> list[dict[str, Any]]:
        return gw().db.list_agent_defs()

    @app.post("/api/studio/agents")
    async def studio_create(body: dict[str, Any]) -> dict[str, Any]:
        name = (body.get("name") or "").strip().lower().replace(" ", "-")
        if not name or not name.replace("-", "").isalnum():
            raise HTTPException(422, "name must be a slug (letters, digits, dashes)")
        existing = gw().db.get_agent_def(name)
        version = (existing["version"] + 1) if existing else 1
        gw().db.upsert_agent_def(
            {"id": f"def_{name}", "name": name, "version": version, "status": "draft", "definition": body}
        )
        await gw().audit("human:studio", "agent.define", name, "draft", version=version)
        return {"name": name, "version": version, "status": "draft"}

    def _definition_is_high_risk(definition: dict[str, Any]) -> bool:
        tools = {t if isinstance(t, str) else t.get("name", "") for t in definition.get("tools", [])}
        return bool(tools & HIGH_RISK_TOOLS) or definition.get("risk_class") in ("tier2", "tier3", "always_human")

    async def _deploy_agent_def(name: str, approver: str | None) -> dict[str, Any]:
        """Mark live and ask the runtime supervisor to host a real process."""
        d = gw().db.get_agent_def(name)
        if not d:
            raise HTTPException(404, "unknown agent definition")
        runtime_url = os.environ.get("TINYCLAW_RUNTIME_URL", "http://127.0.0.1:9111")
        try:
            async with httpx.AsyncClient(timeout=15.0) as http:
                r = await http.post(
                    f"{runtime_url}/host", json={"definition": d["definition"] | {"version": d["version"]}}
                )
                r.raise_for_status()
                hosted = r.json()
        except Exception as exc:
            raise HTTPException(
                503,
                f"runtime supervisor unreachable ({exc}); start it with `python -m tinyclaw.runtime`",
            ) from exc
        gw().db.upsert_agent_def({**d, "status": "live"})
        await gw().audit("gateway", "agent.deploy", name, "live", url=hosted["url"], approver=approver)
        await gw().broadcast(
            {
                "id": uuid.uuid4().hex[:8],
                "ts": time.time(),
                "type": "agent.deployed",
                "agent": "gateway",
                "data": {"name": name, "url": hosted["url"]},
            }
        )
        return hosted

    @app.post("/api/studio/agents/{name}/deploy")
    async def studio_deploy(name: str) -> dict[str, Any]:
        d = gw().db.get_agent_def(name)
        if not d:
            raise HTTPException(404, "unknown agent definition")
        # The platform governs its own expansion: high-risk deployments route
        # through the very same human approval queue used for executions.
        if _definition_is_high_risk(d["definition"]):
            approval_id = f"apr_{uuid.uuid4().hex[:10]}"
            gw().db.create_approval(
                {
                    "id": approval_id,
                    "ts": time.time(),
                    "task_id": f"deploy:{name}",
                    "context_id": None,
                    "orchestrator_url": None,
                    "scenario": "studio",
                    "subject": f"Deploy agent “{name}”",
                    "amount": None,
                    "tier": 2,
                    "action": "agent.deploy",
                    "context_packet": {
                        "definition": d["definition"],
                        "version": d["version"],
                        "reason": "high-risk tool binding or elevated risk class",
                    },
                }
            )
            await gw().audit(
                "gateway",
                "approval.requested",
                f"deploy:{name}",
                "pending",
                approval_id=approval_id,
                target_action="agent.deploy",
            )
            return {
                "route": "human",
                "approval_id": approval_id,
                "reason": "high-risk definition — deployment requires human approval",
            }
        return {"route": "auto", **await _deploy_agent_def(name, approver=None)}

    @app.post("/api/studio/agents/{name}/test")
    async def studio_test(name: str, body: dict[str, Any]) -> dict[str, Any]:
        """Dry-run the agent's brain: system prompt + message → reply (mock LLM by default)."""
        d = gw().db.get_agent_def(name)
        if not d:
            raise HTTPException(404, "unknown agent definition")
        from ..core.llm import build_llm

        llm = build_llm(settings.llm_provider, d["definition"].get("model", settings.llm_model))
        message = body.get("message", "")
        with tracing.span("studio.test", agent=name):
            resp = await llm.complete(
                d["definition"].get("system_prompt", "You are a helpful agent."), message, agent=name
            )
        return {"reply": resp.text, "provider": resp.provider, "model": resp.model}

    @app.post("/api/studio/agents/{name}/dry-run")
    async def studio_dry_run(name: str, body: dict[str, Any]) -> dict[str, Any]:
        """Policy dry-run: evaluate a sample payload against the bound policy sets."""
        d = gw().db.get_agent_def(name)
        if not d:
            raise HTTPException(404, "unknown agent definition")
        from ..core.governance.policy import PolicyEngine

        results = []
        for scen in gw().scenarios.values():
            for rel in scen.get("policies", []):
                path: Path = scen["dir"] / rel
                if path.name == "risk.yaml" or not path.exists():
                    continue
                decision = PolicyEngine.from_yaml(path).evaluate(body.get("payload", {}))
                results.append(
                    {
                        "policy_set": rel,
                        "effect": decision.effect.value,
                        "tier": decision.tier,
                        "hits": [h.rule.id for h in decision.hits],
                    }
                )
        return {"results": results}

    # Serve the built dashboard from the same origin as the API (one-port demo).
    ui_dist = Path(__file__).resolve().parent.parent.parent.parent / "ui" / "dist"
    if ui_dist.exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=str(ui_dist), html=True), name="ui")

    return app


def main() -> None:
    settings = Settings(service_name="tinyclaw-gateway")
    app = create_app(settings)
    uvicorn.run(app, host="127.0.0.1", port=9100, log_level="info")


if __name__ == "__main__":
    main()
