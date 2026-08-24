"""Support research agent: order & customer enrichment (mock systems)."""

from __future__ import annotations

from a2a.server.tasks import TaskUpdater
from a2a.types import TextPart

from ....core.agent import AgentRequest, AgentSpec, TinyclawExecutor
from ....core.config import Settings
from ....core.observability import tracing
from ..tools.orders import lookup_order
from ..urls import RESEARCH_URL


class SupportResearchExecutor(TinyclawExecutor):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            AgentSpec(
                name="support-research",
                description="Order & customer enrichment for refund decisions",
                url=RESEARCH_URL,
                skills=["order-research"],
            ),
            settings,
        )

    async def handle(self, request: AgentRequest, updater: TaskUpdater) -> None:
        extracted = request.data or {}
        order_id = extracted.get("order_id", "")
        with tracing.span("tool.order.lookup", order=order_id):
            order = lookup_order(order_id)
        profile = order.get("customer_profile") or {}
        churn = bool(profile.get("churn_risk", False))

        enrichment = {
            "order": order,
            "churn_risk": churn,
            "customer_tier": profile.get("tier", "unknown"),
            "lifetime_value": profile.get("lifetime_value", 0),
        }
        await self.events.report(
            "a2a.artifact", "support-research", {"artifact": "order.profile", "found": order.get("found")}, task_id=None
        )
        await self.artifact(
            updater,
            "order.profile",
            enrichment,
            text=f"order {order_id} {'found' if order.get('found') else 'NOT FOUND'} · "
            f"tier {enrichment['customer_tier']} · churn_risk={churn}",
        )
        await updater.complete(
            updater.new_agent_message(parts=[TextPart(text=f"order research complete — churn_risk={churn}")])
        )


def main() -> None:
    from ....core.agent import serve

    executor = SupportResearchExecutor(Settings(service_name="support-research"))
    serve(executor.spec, executor)


if __name__ == "__main__":
    main()
