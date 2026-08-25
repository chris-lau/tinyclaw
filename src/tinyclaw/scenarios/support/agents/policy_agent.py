"""Support policy agent: refund governance decision point."""

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
ACTION = "refund.issue"


class SupportPolicyExecutor(TinyclawExecutor):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            AgentSpec(
                name="support-policy",
                description="Evaluates refund policies and risk routes",
                url=POLICY_URL,
                skills=["refund-policy-evaluation"],
            ),
            settings,
        )
        self.pack_file = PACK / "policies" / "support.yaml"
        self.engine = PolicyEngine.from_yaml(self.pack_file)  # fallback; live set comes from the gateway
        self.risk = RiskRouter.from_yaml(PACK / "policies" / "risk.yaml")

    async def _engine(self) -> PolicyEngine:
        """Hot-reload: editable set lives in the gateway DB; recompile per
        evaluation, fall back to the pack file if the gateway is unreachable."""
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.gateway_url,
                headers={"authorization": f"Bearer {self.settings.internal_token}"},
                timeout=3.0,
            ) as http:
                r = await http.get("/api/policy-sets/support")
                r.raise_for_status()
                return PolicyEngine.from_text(r.json()["yaml"])
        except Exception:
            return self.engine

    async def handle(self, request: AgentRequest, updater: TaskUpdater) -> None:
        payload = self._evaluation_payload(request)
        posture = (request.data or {}).get("posture", "balanced")
        engine = await self._engine()
        decision = engine.evaluate(payload, posture=posture)
        tier = decision.tier or 1
        ar = self.risk.classify(ACTION)
        if decision.is_denied:
            route = RiskDecision(Route.DENY, ar.risk_class, tier, decision.summary())
        elif decision.effect is Effect.ALLOW and ar.risk_class in (RiskClass.AUTO, RiskClass.THRESHOLD):
            route = RiskDecision(Route.AUTO, ar.risk_class, tier, f"{decision.summary()} · posture={posture}")
        else:
            route = RiskDecision(Route.HUMAN, ar.risk_class, tier, decision.summary())

        async with httpx.AsyncClient(
            base_url=self.settings.gateway_url,
            headers={"authorization": f"Bearer {self.settings.internal_token}"},
            timeout=5.0,
        ) as http:
            await http.post(
                "/internal/audit",
                json={
                    "actor": "agent:support-policy",
                    "action": "policy.evaluate",
                    "subject": request.task_id,
                    "decision": decision.effect.value,
                    "details": {
                        "hits": [f"{h.rule.id} ({h.detail})" for h in decision.hits],
                        "tier": tier,
                        "route": route.route.value,
                        "action": ACTION,
                        "posture": posture,
                        "payload_summary": {k: payload.get(k) for k in ("refund_amount", "abuse_flag", "legal_flag")},
                    },
                },
            )
        await self.events.report(
            "policy.decision",
            "support-policy",
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
        data = request.data or {}
        extracted = data.get("extracted", data)
        enrichment = data.get("profile", {})
        return {
            "refund_amount": float(extracted.get("refund_amount", 0) or 0),
            "abuse_flag": bool(extracted.get("abuse_flag", False)),
            "legal_flag": bool(extracted.get("legal_flag", False)),
            "churn_risk": bool(enrichment.get("churn_risk", False)),
            "injection_flags": int(extracted.get("injection_flags", 0) or 0),
        }


def main() -> None:
    from ....core.agent import serve

    executor = SupportPolicyExecutor(Settings(service_name="support-policy"))
    serve(executor.spec, executor)


if __name__ == "__main__":
    main()
