"""Policy agent: the governance decision point.

Evaluates the policy set + risk registry for the enriched request and returns
a routing decision:

* ``deny`` — hard policy violation (sanctions, injection)
* ``human`` — risk-tiered human approval required
* ``auto`` — autonomous execution permitted (still audited)
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from a2a.server.tasks import TaskUpdater
from a2a.types import TextPart

from ....core.agent import AgentRequest, AgentSpec, TinyclawExecutor
from ....core.config import Settings
from ....core.governance.policy import Effect, PolicyEngine
from ....core.governance.risk import RiskClass, RiskDecision, RiskRouter, Route
from ..urls import POLICY_URL

PACK = Path(__file__).parent.parent
ACTION = "po.issue"


class PolicyExecutor(TinyclawExecutor):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            AgentSpec(
                name="policy",
                description="Evaluates policy rules and risk routes for requests",
                url=POLICY_URL,
                skills=["policy-evaluation"],
            ),
            settings,
        )
        self.engine = PolicyEngine.from_yaml(PACK / "policies" / "procurement.yaml")
        self.risk = RiskRouter.from_yaml(PACK / "policies" / "risk.yaml")

    async def handle(self, request: AgentRequest, updater: TaskUpdater) -> None:
        payload = self._evaluation_payload(request)
        posture = (request.data or {}).get("posture", "balanced")
        decision = self.engine.evaluate(payload, posture=posture)
        tier = decision.tier or 1
        ar = self.risk.classify(ACTION)
        if decision.is_denied:
            # A hard policy deny overrides any risk-class routing.
            route = RiskDecision(Route.DENY, ar.risk_class, tier, decision.summary())
        elif decision.effect is Effect.ALLOW and ar.risk_class in (RiskClass.AUTO, RiskClass.THRESHOLD):
            # The posture-adjusted ALLOW is authoritative: under "full", a
            # tier-2 allowance executes autonomously. (An ALWAYS_HUMAN action
            # class still forces a human regardless of posture.)
            route = RiskDecision(Route.AUTO, ar.risk_class, tier, f"{decision.summary()} · posture={posture}")
        else:
            route = RiskDecision(Route.HUMAN, ar.risk_class, tier, decision.summary())

        # The policy decision itself is audit-worthy: full reasoning, all hits.
        async with httpx.AsyncClient(
            base_url=self.settings.gateway_url,
            headers={"authorization": f"Bearer {self.settings.internal_token}"},
            timeout=5.0,
        ) as http:
            await http.post(
                "/internal/audit",
                json={
                    "actor": "agent:policy",
                    "action": "policy.evaluate",
                    "subject": request.task_id,
                    "decision": decision.effect.value,
                    "details": {
                        "hits": [f"{h.rule.id} ({h.detail})" for h in decision.hits],
                        "tier": tier,
                        "route": route.route.value,
                        "action": ACTION,
                        "posture": posture,
                        "payload_summary": {k: payload.get(k) for k in ("amount", "injection_flags")},
                    },
                },
            )
        await self.events.report(
            "policy.decision",
            "policy",
            {
                "effect": decision.effect.value,
                "route": route.route.value,
                "tier": tier,
                "hits": [h.rule.id for h in decision.hits],
                "summary": decision.summary(),
                "posture": posture,
            },
            task_id=request.task_id,
        )

        out = {
            "effect": decision.effect.value,
            "route": route.route.value,
            "tier": tier,
            "action": ACTION,
            "reason": route.reason,
            "hits": [h.rule.id for h in decision.hits],
            "summary": decision.summary(),
        }
        await self.artifact(updater, "policy.eval", out, text=decision.summary())
        await updater.complete(updater.new_agent_message(parts=[TextPart(text=json.dumps(out))]))

    def _evaluation_payload(self, request: AgentRequest) -> dict:
        """Merge intake extraction + research profile into one policy payload."""
        data = request.data or {}
        # The orchestrator forwards both artifacts: extracted + vendor profile.
        extracted = data.get("extracted", data)
        profile = data.get("profile", {})
        vendor = profile.get("vendor", extracted.get("vendor", {}))
        return {
            "amount": float(extracted.get("amount", 0) or 0),
            "vendor": {"sanctioned": bool(profile.get("sanctioned", False)), "tier": vendor.get("tier", "unrated")},
            "requester": extracted.get("requester", "unknown"),
            "cost_center": extracted.get("cost_center"),
            "injection_flags": int(extracted.get("injection_flags", 0) or 0),
        }


def main() -> None:
    from ....core.agent import serve

    executor = PolicyExecutor(Settings(service_name="policy"))
    serve(executor.spec, executor)


if __name__ == "__main__":
    main()
