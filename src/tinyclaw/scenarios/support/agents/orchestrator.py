"""Support orchestrator: coordinates the refund workflow over A2A.

Same skeleton as the procurement orchestrator — that's the framework story:
a new scenario is a new pack, not a core change. Differences are the plan
steps, the artifact names, and the domain fields.
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

ACTION = "refund.issue"


class GatewayClient:
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
            pass

    async def request_permit(self, body: dict) -> dict:
        r = await self._http.post("/internal/permits", json=body)
        r.raise_for_status()
        return r.json()

    async def posture(self) -> str:
        try:
            r = await self._http.get("/api/posture")
            r.raise_for_status()
            return r.json().get("posture", "balanced")
        except Exception:
            return "balanced"

    async def aclose(self) -> None:
        await self._http.aclose()


class SupportOrchestratorExecutor(TinyclawExecutor):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            AgentSpec(
                name="support-orchestrator",
                description="Coordinates support refund requests across specialist agents",
                url=SELF_URL,
                skills=["support-coordination"],
            ),
            settings,
        )
        self.gateway = GatewayClient(settings)
        self.intake = A2ACaller(INTAKE_URL, settings=settings, caller_name="support-orchestrator")
        self.research = A2ACaller(RESEARCH_URL, settings=settings, caller_name="support-orchestrator")
        self.policy = A2ACaller(POLICY_URL, settings=settings, caller_name="support-orchestrator")
        self.executor = A2ACaller(EXECUTOR_URL, settings=settings, caller_name="support-orchestrator")

    async def handle(self, request: AgentRequest, updater: TaskUpdater) -> None:
        if request.is_resume and request.metadata.get("tinyclaw.decision"):
            await self._resume(request, updater)
            return
        await self._run(request, updater)

    async def _run(self, request: AgentRequest, updater: TaskUpdater) -> None:
        task_id, ctx = request.task_id, request.context_id
        payload = request.data or {}
        title = payload.get("title") or payload.get("subject", "support ticket")
        requester = payload.get("requester", request.metadata.get("tinyclaw.requester", "unknown"))

        await self._task(task_id, ctx, title, None, "intake", "working", requester)

        plan = {
            "steps": [
                {"id": "intake", "label": "Extract structured ticket", "agent": "support-intake"},
                {"id": "research", "label": "Order & customer lookup", "agent": "support-research"},
                {"id": "policy", "label": "Evaluate refund policy & route", "agent": "support-policy"},
                {"id": "route", "label": "Execution permit (auto / human / deny)", "agent": "gateway"},
                {"id": "execute", "label": "Issue refund with signed permit", "agent": "support-executor"},
            ]
        }
        await self.artifact(updater, "task.plan", plan, text="plan: intake → research → policy → route → execute")
        await self._hop("intake", task_id)

        posture = await self.gateway.posture()

        try:
            extracted = (
                await self.intake.send_text(
                    f"extract support ticket: {title}",
                    data=payload,
                    metadata={"tinyclaw.requester": requester},
                    hook_task_id=task_id,
                )
            ).data
            amount = float(extracted.get("refund_amount", 0) or 0)
            await self._task(task_id, ctx, title, amount, "research", "working", requester)

            profile = (
                await self.research.send_text(
                    f"look up order {extracted.get('order_id')} — ticket: {extracted.get('body_summary', title)}",
                    data=extracted,
                    hook_task_id=task_id,
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
            await self.events.report(
                "hook.blocked",
                "support-orchestrator",
                {"hook": blocked.hook, "detail": blocked.detail},
                task_id=task_id,
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

        permit_resp = await self.gateway.request_permit(
            {
                "task_id": task_id,
                "context_id": ctx,
                "action": ACTION,
                "route": policy_out.get("route", "human"),
                "tier": policy_out.get("tier", 1),
                "scenario": "support",
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

        await self._task(task_id, ctx, title, amount, "approval", "input_required", requester)
        await updater.requires_input(
            updater.new_agent_message(
                parts=[
                    TextPart(
                        text=f"Human approval required (tier {policy_out.get('tier')}): "
                        f"{policy_out.get('summary')}. Approval id {permit_resp.get('approval_id')}."
                    )
                ]
            )
        )

    async def _resume(self, request: AgentRequest, updater: TaskUpdater) -> None:
        task_id = request.task_id
        decision = request.metadata.get("tinyclaw.decision")
        t = {"task_id": task_id, "context_id": request.context_id, "scenario": "support"}
        details = request.metadata.get("tinyclaw.context") or {}
        subject = details.get("subject", "support ticket")
        amount = details.get("amount") or (details.get("request") or {}).get("refund_amount") or 0
        if decision != "approve" or not request.metadata.get("tinyclaw.permit"):
            await self.gateway.upsert_task(
                **t,
                state="rejected",
                stage="rejected",
                title=subject,
                amount=amount,
                current_agent="support-orchestrator",
            )
            await updater.reject(
                updater.new_agent_message(
                    parts=[TextPart(text=f"Rejected by human: {request.metadata.get('tinyclaw.approver', 'human')}")]
                )
            )
            return
        extracted = details.get("request") or {}
        await self._execute(request, updater, request.metadata["tinyclaw.permit"], extracted, subject, amount)

    async def _execute(
        self, request: AgentRequest, updater: TaskUpdater, token: str, extracted: dict, title: str, amount: float | None
    ) -> None:
        task_id, ctx = request.task_id, request.context_id
        await self._hop("executor", task_id)
        await self._task(task_id, ctx, title, amount, "executed", "working", extracted.get("requester", "unknown"))
        try:
            result = (
                await self.executor.send_text(
                    f"issue refund for {title}",
                    data={
                        "task_id": task_id,
                        "action": ACTION,
                        "amount": amount,
                        "customer": extracted.get("customer"),
                        "order_id": extracted.get("order_id"),
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
        state = "completed" if result.get("refund_id") else "failed"
        await self._task(
            task_id,
            ctx,
            title,
            amount,
            "executed" if state == "completed" else "failed",
            state,
            extracted.get("requester", "unknown"),
        )
        if result.get("refund_id"):
            await self.artifact(
                updater, "task.result", result, text=f"{result['refund_id']} · route {result.get('route')}"
            )
        await updater.complete(
            updater.new_agent_message(parts=[TextPart(text=json.dumps(result or {"result": "executed"}))])
        )

    async def _task(
        self, task_id: str, ctx: str, title: str, amount: float | None, stage: str, state: str, requester: str
    ) -> None:
        from opentelemetry.trace import get_current_span

        sc = get_current_span().get_span_context()
        await self.gateway.upsert_task(
            task_id=task_id,
            context_id=ctx,
            scenario="support",
            title=title,
            amount=amount,
            state=state,
            stage=stage,
            current_agent="support-orchestrator",
            trace_id=f"{sc.trace_id:032x}" if sc.is_valid else None,
            requester=requester,
        )

    async def _hop(self, to: str, task_id: str | None) -> None:
        await self.events.report("a2a.hop", "support-orchestrator", {"to": to}, task_id=task_id)


def main() -> None:
    from ....core.agent import serve

    executor = SupportOrchestratorExecutor(Settings(service_name="support-orchestrator"))
    serve(executor.spec, executor)


if __name__ == "__main__":
    main()
