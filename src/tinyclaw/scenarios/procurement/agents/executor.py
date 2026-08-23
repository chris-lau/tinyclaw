"""Executor agent: performs the consequential action — issuing the PO.

Nothing happens without a **signed permit** from the gateway:

* AUTO route — permit signed by the gateway after the policy pass,
* HUMAN route — permit signed only after a recorded human approval
  (approver identity embedded in the token).

This is the enforcement point of the whole governance story: even a fully
compromised orchestrator cannot make this agent act without a valid permit.
"""

from __future__ import annotations

import json
import time
import uuid

import httpx
from a2a.server.tasks import TaskUpdater
from a2a.types import TextPart

from ....core.agent import AgentRequest, AgentSpec, TinyclawExecutor
from ....core.config import Settings
from ....core.hitl.tokens import verify_permit
from ..urls import EXECUTOR_URL


class ExecutorExecutor(TinyclawExecutor):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            AgentSpec(
                name="executor",
                description="Issues purchase orders — only with a verifiable execution permit",
                url=EXECUTOR_URL,
                skills=["po-execution"],
            ),
            settings,
        )

    async def handle(self, request: AgentRequest, updater: TaskUpdater) -> None:
        data = request.data or {}
        action = data.get("action", "po.issue")
        token = (request.metadata or {}).get("tinyclaw.permit", "")
        # The permit is bound to the ORCHESTRATOR's task id (the business
        # transaction), not to this agent's own A2A task id.
        bound_task_id = data.get("task_id") or request.task_id

        permit = verify_permit(self.settings.approval_secret, token, task_id=bound_task_id, action=action)
        if not permit:
            await self.events.report(
                "permit.rejected",
                "executor",
                {"reason": "missing or invalid permit", "action": action},
                task_id=bound_task_id,
            )
            await updater.reject(
                updater.new_agent_message(
                    parts=[
                        TextPart(
                            text="REFUSED: no valid execution permit. The gateway must authorize this action "
                            "(auto after policy pass, or after a recorded human approval)."
                        )
                    ]
                )
            )
            return

        po_number = f"PO-{uuid.uuid4().hex[:8].upper()}"
        result = {
            "po_number": po_number,
            "action": action,
            "route": permit.route,
            "approver": permit.approver or "policy(auto)",
            "issued_at": time.time(),
            "amount": data.get("amount"),
            "vendor": data.get("vendor"),
        }
        async with httpx.AsyncClient(
            base_url=self.settings.gateway_url,
            headers={"authorization": f"Bearer {self.settings.internal_token}"},
            timeout=5.0,
        ) as http:
            await http.post(
                "/internal/audit",
                json={
                    "actor": "agent:executor",
                    "action": "execution.executed",
                    "subject": bound_task_id,
                    "decision": permit.route,
                    "details": {
                        "route": permit.route,
                        "action": action,
                        "po_number": po_number,
                        "approver": permit.approver,
                    },
                },
            )
        await self.artifact(
            updater, "po.issued", result, text=f"{po_number} · ${result['amount'] or 0:,.0f} · route {permit.route}"
        )
        await updater.complete(updater.new_agent_message(parts=[TextPart(text=json.dumps(result))]))


def main() -> None:
    from ....core.agent import serve

    executor = ExecutorExecutor(Settings(service_name="executor"))
    serve(executor.spec, executor)


if __name__ == "__main__":
    main()
