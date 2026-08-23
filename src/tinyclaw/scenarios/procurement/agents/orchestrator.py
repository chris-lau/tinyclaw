"""Orchestrator agent: coordinates the procurement workflow over A2A.

Deterministic by design — the *control plane* is code, not an LLM (an
enterprise-grade property worth showcasing: predictable supervision around
LLM-powered workers). Every handoff is a real A2A protocol call with Agent
Card discovery and tracecontext propagation, so the whole workflow appears as
ONE distributed trace.

Task lifecycle:
    submitted → working → (input-required ⇄ human decision) → completed | rejected
"""

from __future__ import annotations

import json

import httpx
from a2a.server.tasks import TaskUpdater
from a2a.types import TextPart

from ....core.a2a_client import A2ACaller, HookBlockedError
from ....core.agent import AgentRequest, AgentSpec, TinyclawExecutor
from ....core.config import Settings
from ..urls import EXECUTOR_URL, INTAKE_URL, POLICY_URL, RESEARCH_URL, SELF_URL

ACTION = "po.issue"


class GatewayClient:
    """Tiny internal API client (tasks mirror + permits)."""

    def __init__(self, settings: Settings) -> None:
        self._http = httpx.AsyncClient(
            base_url=settings.gateway_url,
            headers={"authorization": f"Bearer {settings.internal_token}"},
            timeout=10.0,
        )

    async def upsert_task(self, **fields: object) -> None:
        try:
            await self._http.post("/internal/tasks", json=fields)
        except Exception:
            pass  # observation/mirror must never block the workflow

    async def request_permit(self, body: dict) -> dict:
        r = await self._http.post("/internal/permits", json=body)
        r.raise_for_status()
        return r.json()

    async def posture(self) -> str:
        """Current autonomy-dial posture (public endpoint; fail → balanced)."""
        try:
            r = await self._http.get("/api/posture")
            r.raise_for_status()
            return r.json().get("posture", "balanced")
        except Exception:
            return "balanced"

    async def aclose(self) -> None:
        await self._http.aclose()


class OrchestratorExecutor(TinyclawExecutor):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            AgentSpec(
                name="orchestrator",
                description="Coordinates purchase requests across specialist agents",
                url=SELF_URL,
                skills=["procurement-coordination"],
            ),
            settings,
        )
        self.gateway = GatewayClient(settings)
        # Settings-aware callers: every outbound message passes the gateway's
        # boundary-hook policy decision point before it leaves this agent.
        self.intake = A2ACaller(INTAKE_URL, settings=settings, caller_name="orchestrator")
        self.research = A2ACaller(RESEARCH_URL, settings=settings, caller_name="orchestrator")
        self.policy = A2ACaller(POLICY_URL, settings=settings, caller_name="orchestrator")
        self.executor = A2ACaller(EXECUTOR_URL, settings=settings, caller_name="orchestrator")

    async def handle(self, request: AgentRequest, updater: TaskUpdater) -> None:
        if request.is_resume and request.metadata.get("tinyclaw.decision"):
            await self._resume(request, updater)
            return
        await self._run(request, updater)

    # ------------------------------------------------------------------ fresh run

    async def _run(self, request: AgentRequest, updater: TaskUpdater) -> None:
        task_id, ctx = request.task_id, request.context_id
        payload = request.data or {}
        title = payload.get("title") or payload.get("description", "purchase request")
        requester = payload.get("requester", request.metadata.get("tinyclaw.requester", "unknown"))

        await self._task(task_id, ctx, title, None, "intake", "working", requester)

        # Plan-first execution (pattern borrowed from production coding-agent
        # harnesses): publish intent as a first-class artifact BEFORE acting,
        # so humans can audit what the agent intends, not just what it did.
        plan = {
            "steps": [
                {"id": "intake", "label": "Extract structured request", "agent": "intake"},
                {"id": "research", "label": "Vendor, sanctions & budget", "agent": "research"},
                {"id": "policy", "label": "Evaluate policy & risk route", "agent": "policy"},
                {"id": "route", "label": "Execution permit (auto / human / deny)", "agent": "gateway"},
                {"id": "execute", "label": "Issue PO with signed permit", "agent": "executor"},
            ]
        }
        await self.artifact(updater, "task.plan", plan, text="plan: intake → research → policy → route → execute")
        await self._hop("intake", task_id)

        # The autonomy dial travels with the task: the policy agent evaluates
        # under the posture that was current when the run started.
        posture = await self.gateway.posture()

        try:
            extracted = (
                await self.intake.send_text(
                    f"extract purchase request: {title}",
                    data=payload,
                    metadata={"tinyclaw.requester": requester},
                    hook_task_id=task_id,
                )
            ).data
            amount = float(extracted.get("amount", 0) or 0)
            await self._task(task_id, ctx, title, amount, "research", "working", requester)

            profile = (
                await self.research.send_text(
                    f"enrich vendor {extracted.get('vendor')}", data=extracted, hook_task_id=task_id
                )
            ).data

            await self._task(task_id, ctx, title, amount, "policy", "working", requester)
            policy_out = (
                await self.policy.send_text(
                    "evaluate policy",
                    data={"extracted": extracted, "profile": profile, "posture": posture},
                    hook_task_id=task_id,
                )
            ).data
        except HookBlockedError as blocked:
            # The boundary refused one of our sends: end the task, loudly.
            await self.events.report(
                "hook.blocked", "orchestrator", {"hook": blocked.hook, "detail": blocked.detail}, task_id=task_id
            )
            await self._task(task_id, ctx, title, None, "blocked", "rejected", requester)
            await updater.reject(
                updater.new_agent_message(
                    parts=[TextPart(text=f"BLOCKED at the A2A boundary by hook “{blocked.hook}”: {blocked.detail}")]
                )
            )
            return

        await self._task(
            task_id,
            ctx,
            title,
            amount,
            "approval" if policy_out.get("route") == "human" else "policy",
            "working",
            requester,
        )

        # Ask the gateway for execution authorization (audit + permit/approval).
        permit_resp = await self.gateway.request_permit(
            {
                "task_id": task_id,
                "context_id": ctx,
                "action": ACTION,
                "route": policy_out.get("route", "human"),
                "tier": policy_out.get("tier", 1),
                "scenario": "procurement",
                "subject": title,
                "amount": amount,
                "orchestrator_url": SELF_URL,
                "policy_decision": policy_out,
                "hits": policy_out.get("hits", []),
                "context_packet": {
                    "subject": title,
                    "amount": amount,
                    "request": extracted,
                    "research": profile,
                    "policy": policy_out,
                },
            }
        )

        route = permit_resp.get("route")
        if route == "deny":
            await self._task(task_id, ctx, title, amount, "denied", "rejected", requester)
            await updater.reject(
                updater.new_agent_message(
                    parts=[TextPart(text=f"DENIED by policy: {policy_out.get('summary', 'policy violation')}")]
                )
            )
            return
        if route == "auto":
            await self._execute(request, updater, permit_resp["token"], extracted, title, amount)
            return

        # Human route: park the A2A task in input-required — the protocol's
        # own pause primitive. The dashboard resumes it with a signed decision.
        approval_id = permit_resp.get("approval_id")
        await self._task(task_id, ctx, title, amount, "approval", "input_required", requester)
        await updater.requires_input(
            updater.new_agent_message(
                parts=[
                    TextPart(
                        text=f"Human approval required (tier {policy_out.get('tier')}): "
                        f"{policy_out.get('summary')}. Approval id {approval_id}."
                    )
                ]
            )
        )

    # ------------------------------------------------------------------ resume

    async def _resume(self, request: AgentRequest, updater: TaskUpdater) -> None:
        task_id = request.task_id
        decision = request.metadata.get("tinyclaw.decision")
        t = {"task_id": task_id, "context_id": request.context_id, "scenario": "procurement"}
        details = request.metadata.get("tinyclaw.context") or {}
        subject = details.get("subject", "purchase request")
        amount = details.get("amount") or (details.get("request") or {}).get("amount") or 0
        if decision != "approve" or not request.metadata.get("tinyclaw.permit"):
            await self.gateway.upsert_task(
                **t, state="rejected", stage="rejected", title=subject, amount=amount, current_agent="orchestrator"
            )
            await updater.reject(
                updater.new_agent_message(
                    parts=[TextPart(text=f"Rejected by human: {request.metadata.get('tinyclaw.approver', 'human')}")]
                )
            )
            return
        extracted = details.get("request") or {}
        await self._execute(
            request,
            updater,
            request.metadata["tinyclaw.permit"],
            extracted,
            subject,
            details.get("amount") or extracted.get("amount") or 0,
        )

    async def _execute(
        self, request: AgentRequest, updater: TaskUpdater, token: str, extracted: dict, title: str, amount: float | None
    ) -> None:
        task_id, ctx = request.task_id, request.context_id
        await self._hop("executor", task_id)
        await self._task(task_id, ctx, title, amount, "executed", "working", extracted.get("requester", "unknown"))
        try:
            result = (
                await self.executor.send_text(
                    f"issue PO for {title}",
                    data={
                        "task_id": task_id,
                        "action": ACTION,
                        "amount": amount,
                        "vendor": extracted.get("vendor"),
                        "description": extracted.get("description", title),
                    },
                    metadata={"tinyclaw.permit": token},
                    hook_task_id=task_id,
                )
            ).data
        except HookBlockedError as blocked:
            await self._task(task_id, ctx, title, amount, "blocked", "rejected", extracted.get("requester", "unknown"))
            await updater.reject(
                updater.new_agent_message(
                    parts=[TextPart(text=f"BLOCKED at the A2A boundary by hook “{blocked.hook}”: {blocked.detail}")]
                )
            )
            return
        state = "completed" if result.get("po_number") else "failed"
        await self._task(
            task_id,
            ctx,
            title,
            amount,
            "executed" if state == "completed" else "failed",
            state,
            extracted.get("requester", "unknown"),
        )
        if result.get("po_number"):
            await self.artifact(
                updater, "task.result", result, text=f"{result['po_number']} · route {result.get('route')}"
            )
        await updater.complete(
            updater.new_agent_message(parts=[TextPart(text=json.dumps(result or {"result": "executed"}))])
        )

    # ------------------------------------------------------------------ helpers

    async def _task(
        self, task_id: str, ctx: str, title: str, amount: float | None, stage: str, state: str, requester: str
    ) -> None:
        from opentelemetry.trace import get_current_span

        sc = get_current_span().get_span_context()
        await self.gateway.upsert_task(
            task_id=task_id,
            context_id=ctx,
            scenario="procurement",
            title=title,
            amount=amount,
            state=state,
            stage=stage,
            current_agent="orchestrator",
            trace_id=f"{sc.trace_id:032x}" if sc.is_valid else None,
            requester=requester,
        )

    async def _hop(self, to: str, task_id: str | None) -> None:
        await self.events.report("a2a.hop", "orchestrator", {"to": to}, task_id=task_id)


def main() -> None:
    from ....core.agent import serve

    executor = OrchestratorExecutor(Settings(service_name="orchestrator"))
    serve(executor.spec, executor)


if __name__ == "__main__":
    main()
