"""Support executor agent: issues refunds/credits with a verified permit."""

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


class SupportExecutorExecutor(TinyclawExecutor):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            AgentSpec(
                name="support-executor",
                description="Issues refunds/credits — only with a verifiable execution permit",
                url=EXECUTOR_URL,
                skills=["refund-execution"],
            ),
            settings,
        )

    async def handle(self, request: AgentRequest, updater: TaskUpdater) -> None:
        data = request.data or {}
        action = data.get("action", "refund.issue")
        token = (request.metadata or {}).get("tinyclaw.permit", "")
        bound_task_id = data.get("task_id") or request.task_id

        permit = verify_permit(self.settings.approval_secret, token, task_id=bound_task_id, action=action)
        if not permit:
            await self.events.report(
                "permit.rejected",
                "support-executor",
                {"reason": "missing or invalid permit", "action": action},
                task_id=bound_task_id,
            )
            await updater.reject(
                updater.new_agent_message(parts=[TextPart(text="REFUSED: no valid execution permit.")])
            )
            return

        refund_id = f"RF-{uuid.uuid4().hex[:8].upper()}"
        result = {
            "refund_id": refund_id,
            "action": action,
            "route": permit.route,
            "approver": permit.approver or "policy(auto)",
            "issued_at": time.time(),
            "amount": data.get("amount"),
            "customer": data.get("customer"),
        }
        async with httpx.AsyncClient(
            base_url=self.settings.gateway_url,
            headers={"authorization": f"Bearer {self.settings.internal_token}"},
            timeout=5.0,
        ) as http:
            await http.post(
                "/internal/audit",
                json={
                    "actor": "agent:support-executor",
                    "action": "execution.executed",
                    "subject": bound_task_id,
                    "decision": permit.route,
                    "details": {
                        "route": permit.route,
                        "action": action,
                        "refund_id": refund_id,
                        "approver": permit.approver,
                    },
                },
            )
        await self.artifact(
            updater, "refund.issued", result, text=f"{refund_id} · ${result['amount'] or 0:,.0f} · route {permit.route}"
        )
        await updater.complete(updater.new_agent_message(parts=[TextPart(text=json.dumps(result))]))


def main() -> None:
    from ....core.agent import serve

    executor = SupportExecutorExecutor(Settings(service_name="support-executor"))
    serve(executor.spec, executor)


if __name__ == "__main__":
    main()
