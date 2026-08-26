"""Support intake agent: free-form ticket → structured refund request.

Guardrails before the LLM (PII redaction), keyword flags for abuse / legal /
churn risk feed the policy engine downstream.
"""

from __future__ import annotations

import json
import re

import httpx
from a2a.server.tasks import TaskUpdater
from a2a.types import TextPart

from ....core.agent import AgentRequest, AgentSpec, TinyclawExecutor
from ....core.config import Settings
from ....core.governance.guardrails import redact_pii, scan_injection
from ....core.llm import build_llm
from ..urls import INTAKE_URL

SYSTEM = (
    "You extract customer support refund requests. Reply with ONLY a JSON "
    "object with keys: refund_amount (number, USD), order_id, customer, "
    "subject, body_summary, sentiment (angry|neutral|happy)."
)

ABUSE_PATTERNS = ["chargeback fraud", "fake refund", "do a chargeback", "double dipping"]
LEGAL_PATTERNS = ["sue", "lawyer", "legal action", "attorney", "small claims"]
CHURN_PATTERNS = ["cancel my subscription", "switching to", "leaving you", "competitor"]


class SupportIntakeExecutor(TinyclawExecutor):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            AgentSpec(
                name="support-intake",
                description="Extracts structured refund requests from support tickets",
                url=INTAKE_URL,
                skills=["ticket-extraction"],
            ),
            settings,
        )
        self.llm = build_llm(settings.llm_provider, settings.llm_model)

    async def _system_prompt(self) -> str:
        """Hot prompt override: the gateway DB may carry an edited system
        prompt (audited, versioned); the code constant is the default."""
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.gateway_url,
                headers={"authorization": f"Bearer {self.settings.internal_token}"},
                timeout=3.0,
            ) as http:
                r = await http.get("/api/agent-prompts/support-intake")
                r.raise_for_status()
                override = r.json().get("system_prompt")
                return override or SYSTEM
        except Exception:
            return SYSTEM

    async def handle(self, request: AgentRequest, updater: TaskUpdater) -> None:
        payload = dict(request.data or {})
        # Flags must scan the WHOLE ticket surface: title, body, payload fields —
        # customers write "chargeback fraud" and legal threats in the body.
        raw_text = " ".join(
            filter(
                None,
                [
                    request.text,
                    payload.get("title", ""),
                    payload.get("body", ""),
                ],
            )
        ) or json.dumps(payload)
        requester = payload.get("requester", request.metadata.get("tinyclaw.requester", "unknown"))

        clean, report = redact_pii({"text": raw_text, "payload": payload})
        flags = scan_injection(f"{raw_text} {json.dumps(payload, default=str)}")
        if report.redactions or flags:
            await self.events.report(
                "guardrail.hit",
                "support-intake",
                {
                    "redactions": report.redactions,
                    "redacted_fields": report.redacted_fields,
                    "injection_patterns": flags,
                },
                task_id=request.task_id,
            )

        resp = await self.llm.complete(
            SYSTEM, f"{clean['text']}\n\npayload: {json.dumps(clean.get('payload', payload))}", agent="support-intake"
        )
        extracted = self._parse(resp.text, payload)
        extracted["requester"] = requester
        lower = raw_text.lower()
        extracted["abuse_flag"] = any(p in lower for p in ABUSE_PATTERNS)
        extracted["legal_flag"] = any(p in lower for p in LEGAL_PATTERNS)
        extracted["injection_flags"] = len(flags)

        await self.artifact(
            updater,
            "ticket.extracted",
            extracted,
            text=f"refund ${extracted.get('refund_amount', 0):,.0f} · order {extracted.get('order_id', '?')}",
        )
        await updater.complete(updater.new_agent_message(parts=[TextPart(text=json.dumps(extracted))]))

    def _parse(self, text: str, fallback: dict) -> dict:
        try:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            data = json.loads(m.group(0)) if m else {}
        except json.JSONDecodeError:
            data = {}
        data.setdefault("refund_amount", float(fallback.get("refund_amount", fallback.get("amount", 0)) or 0))
        data.setdefault("order_id", fallback.get("order_id", "ord-unknown"))
        data.setdefault("customer", fallback.get("customer", "unknown"))
        data.setdefault("subject", fallback.get("title", "") or fallback.get("subject", ""))
        data.setdefault("body_summary", fallback.get("body", "")[:200])
        return data


def main() -> None:
    from ....core.agent import serve

    executor = SupportIntakeExecutor(Settings(service_name="support-intake"))
    serve(executor.spec, executor)


if __name__ == "__main__":
    main()
