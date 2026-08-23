"""Research agent: enriches the request with vendor + budget facts.

Deterministic (tool calls, no LLM): vendor registry lookup, sanctions check,
budget check. Stands in for enterprise system integrations.
"""

from __future__ import annotations

import time

from a2a.server.tasks import TaskUpdater
from a2a.types import TextPart

from ....core.agent import AgentRequest, AgentSpec, TinyclawExecutor
from ....core.config import Settings
from ....core.observability import tracing
from ..tools.vendors import budget_for, lookup_vendor
from ..urls import RESEARCH_URL


class ResearchExecutor(TinyclawExecutor):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            AgentSpec(
                name="research",
                description="Vendor enrichment: registry, sanctions, budget",
                url=RESEARCH_URL,
                skills=["vendor-research"],
            ),
            settings,
        )

    async def handle(self, request: AgentRequest, updater: TaskUpdater) -> None:
        extracted = request.data or {}
        vendor_name = extracted.get("vendor", "unknown")
        cost_center = extracted.get("cost_center")

        t0 = time.perf_counter()
        with tracing.span("tool.vendor.lookup", vendor=vendor_name):
            vendor = lookup_vendor(vendor_name)
        with tracing.span("tool.sanctions.check", vendor=vendor_name):
            sanctioned = vendor["sanctioned"]
        with tracing.span("tool.budget.check"):
            budget = budget_for(cost_center)
        dur_ms = round((time.perf_counter() - t0) * 1000)

        profile = {
            "vendor": vendor,
            "budget": budget,
            "sanctioned": sanctioned,
            "tools_ms": dur_ms,
        }
        await self.events.report(
            "a2a.artifact", "research", {"artifact": "vendor.profile", "sanctioned": sanctioned}, task_id=None
        )
        await self.artifact(
            updater,
            "vendor.profile",
            profile,
            text=f"vendor {vendor.get('vendor_id')} tier {vendor.get('tier')} · "
            f"sanctions {'HIT' if sanctioned else 'clear'} · budget ${budget.get('remaining', 0):,.0f}",
        )
        await updater.complete(
            updater.new_agent_message(parts=[TextPart(text=f"vendor research complete — sanctioned={sanctioned}")])
        )


def main() -> None:
    from ....core.agent import serve

    executor = ResearchExecutor(Settings(service_name="research"))
    serve(executor.spec, executor)


if __name__ == "__main__":
    main()
