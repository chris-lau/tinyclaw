"""Boundary hooks: the policy enforcement point on every A2A send.

The gateway is the policy *decision* point (it evaluates hook rules against
the outbound message); the tinyclaw A2A client embedded in every agent is the
*enforcement* point — no message leaves an agent without passing through
``HookEngine.evaluate`` first.

Effects (all matching hooks are evaluated; the most severe wins):

* ``block``   — the send is refused; the task is rejected with the hook id
* ``redact``  — PII is masked in text AND every payload field before sending
* ``annotate``— the message goes out, tagged with the hook that flagged it

Same condition language as the policy engine (including the regex ``matches``
op), evaluated against ``{"from_agent", "to_agent", "text", "data"}``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from .guardrails import redact_pii
from .policy import Condition


class HookEffect(str, Enum):
    BLOCK = "block"
    REDACT = "redact"
    ANNOTATE = "annotate"


_SEVERITY = {HookEffect.ANNOTATE: 0, HookEffect.REDACT: 1, HookEffect.BLOCK: 2}


@dataclass(frozen=True)
class HookRule:
    id: str
    effect: HookEffect
    description: str = ""
    on: str = "a2a.send"
    when: Condition | None = None


@dataclass
class HookDecision:
    action: str = "allow"  # allow | block | redact
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    annotations: list[dict[str, str]] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.action == "block"


class HookEngine:
    def __init__(self, hooks: list[HookRule]) -> None:
        self.hooks = hooks

    @classmethod
    def from_yaml(cls, path: str | Path) -> HookEngine:
        return cls.from_text(Path(path).read_text())

    @classmethod
    def from_text(cls, yaml_text: str) -> HookEngine:
        """Compile boundary hooks from raw YAML — the hot-reload path (the
        gateway rebuilds the engine from its DB config sets per evaluation)."""
        raw = yaml.safe_load(yaml_text) or {}
        hooks: list[HookRule] = []
        for entry in raw.get("hooks", []):
            when = entry.get("when") or {}
            cond = Condition(path=when["path"], op=when.get("op", "=="), value=when.get("value")) if when else None
            hooks.append(
                HookRule(
                    id=entry["id"],
                    effect=HookEffect(entry["effect"]),
                    description=entry.get("description", ""),
                    on=entry.get("on", "a2a.send"),
                    when=cond,
                )
            )
        if not hooks:
            raise ValueError("hook set has no rules")
        return cls(hooks)

    def evaluate(self, text: str, data: dict[str, Any] | None = None) -> HookDecision:
        data = dict(data or {})
        ctx = {"from_agent": "", "to_agent": "", "text": text, "data": data}
        decision = HookDecision(text=text, data=data)

        hits = [h for h in self.hooks if h.when is None or h.when.matches(ctx)]
        for h in hits:
            decision.annotations.append({"hook": h.id, "action": h.effect.value})
        if not hits:
            return decision

        worst = max(hits, key=lambda h: _SEVERITY[h.effect])
        if worst.effect is HookEffect.BLOCK:
            decision.action = "block"
            return decision
        if worst.effect is HookEffect.REDACT:
            red_text, text_report = redact_pii(decision.text)
            red_data, data_report = redact_pii(decision.data)
            decision.text = str(red_text)
            decision.data = dict(red_data)
            decision.action = "redact"
            decision.annotations.append(
                {
                    "hook": "pii.redact",
                    "action": "redact",
                    "detail": f"{text_report.redactions + data_report.redactions} field(s) masked at the boundary",
                }
            )
        return decision
