"""Intake agent: turns a free-form purchase request into structured data.

Runs the untrusted text through the PII guardrail *before* any LLM call,
scans for prompt-injection patterns, and publishes the extracted request as
an A2A artifact.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from a2a.server.tasks import TaskUpdater
from a2a.types import TextPart

from ....core.agent import AgentRequest, AgentSpec, TinyclawExecutor
from ....core.config import Settings
from ....core.governance.guardrails import redact_pii, scan_injection
from ....core.llm import build_llm
from ..urls import INTAKE_URL

PACK = Path(__file__).parent.parent

SYSTEM = (
    "You extract structured purchase requests. Reply with ONLY a JSON object "
    "with keys: amount (number, USD), vendor (string), description, requester, "
    "items (array of {name, qty, unit_price}), cost_center."
)


class IntakeExecutor(TinyclawExecutor):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            AgentSpec(
                name="intake",
                description="Extracts structured purchase requests from untrusted input",
                url=INTAKE_URL,
                skills=["request-extraction"],
            ),
            settings,
        )
        self.llm = build_llm(settings.llm_provider, settings.llm_model)

    async def handle(self, request: AgentRequest, updater: TaskUpdater) -> None:
        payload = dict(request.data or {})
        raw_text = request.text or payload.get("description", "") or json.dumps(payload)
        requester = payload.get("requester", request.metadata.get("tinyclaw.requester", "unknown"))

        # 1) Guardrails BEFORE the model: redact PII, flag injection attempts.
        #    Scan the whole surface — message text AND every payload field
        #    (untrusted data hides in vendor names, descriptions, line items…).
        clean, report = redact_pii({"text": raw_text, "payload": payload})
        clean_text = clean["text"] if isinstance(clean, dict) else str(clean)
        flags = scan_injection(f"{raw_text} {json.dumps(payload, default=str)}")
        if report.redactions or flags:
            await self.events.report(
                "guardrail.hit",
                "intake",
                {
                    "redactions": report.redactions,
                    "redacted_fields": report.redacted_fields,
                    "injection_patterns": flags,
                },
                task_id=request.task_id,
            )

        # 2) LLM extraction (mock in default mode; same interface, same spans).
        resp = await self.llm.complete(
            SYSTEM, f"{clean_text}\n\npayload: {json.dumps(clean.get('payload', payload))}", agent="intake"
        )
        extracted = self._parse(resp.text, payload)
        extracted["requester"] = requester
        extracted["injection_flags"] = len(flags)

        await self.artifact(
            updater,
            "request.extracted",
            extracted,
            text=f"amount ${extracted.get('amount', 0):,.0f} · vendor {extracted.get('vendor', '?')}",
        )
        await updater.complete(updater.new_agent_message(parts=[TextPart(text=json.dumps(extracted))]))

    def _parse(self, text: str, fallback: dict) -> dict:
        try:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            data = json.loads(m.group(0)) if m else {}
        except json.JSONDecodeError:
            data = {}
        data.setdefault("amount", float(fallback.get("amount", 0) or 0))
        data.setdefault("vendor", fallback.get("vendor", "unknown"))
        data.setdefault("description", fallback.get("title", "") or fallback.get("description", ""))
        data.setdefault("items", fallback.get("items", []))
        data.setdefault("cost_center", fallback.get("cost_center", "CC-1180"))
        return data


def main() -> None:
    from ....core.agent import serve

    executor = IntakeExecutor(Settings(service_name="intake"))
    serve(executor.spec, executor)


if __name__ == "__main__":
    main()
