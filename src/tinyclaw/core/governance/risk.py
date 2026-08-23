"""Risk classification and routing.

A *risk registry* (YAML, per scenario) declares every consequential action the
scenario can perform and how risky it is:

    actions:
      po.issue:            { risk_class: threshold }   # tier decides auto vs human
      payment.execute:     { risk_class: always_human }
      data.export.bulk:    { risk_class: blocked }

Combined with the tier produced by the policy engine, ``route()`` decides the
routing outcome: auto-execute, human approval, or deny.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml


class RiskClass(str, Enum):
    AUTO = "auto"  # tier 1 — execute autonomously, still audited
    THRESHOLD = "threshold"  # tier 2+ — human approval above tier-1 payloads
    ALWAYS_HUMAN = "always_human"  # every execution needs a signed human decision
    BLOCKED = "blocked"  # never executable in this deployment


class Route(str, Enum):
    AUTO = "auto"
    HUMAN = "human"
    DENY = "deny"


@dataclass(frozen=True)
class RiskDecision:
    route: Route
    risk_class: RiskClass
    tier: int
    reason: str

    @property
    def needs_approval(self) -> bool:
        return self.route == Route.HUMAN


@dataclass(frozen=True)
class ActionRisk:
    action: str
    risk_class: RiskClass
    description: str = ""


class RiskRouter:
    def __init__(self, registry: dict[str, ActionRisk]) -> None:
        self.registry = registry

    @classmethod
    def from_yaml(cls, path: str | Path) -> RiskRouter:
        raw = yaml.safe_load(Path(path).read_text()) or {}
        registry = {
            action: ActionRisk(
                action=action, risk_class=RiskClass(spec["risk_class"]), description=spec.get("description", "")
            )
            for action, spec in raw.get("actions", {}).items()
        }
        return cls(registry)

    def classify(self, action: str) -> ActionRisk:
        if action not in self.registry:
            # Unknown actions are treated as always-human: fail safe, never silent.
            return ActionRisk(
                action=action, risk_class=RiskClass.ALWAYS_HUMAN, description="(unregistered — fail-safe default)"
            )
        return self.registry[action]

    def route(self, action: str, tier: int, reason: str = "") -> RiskDecision:
        ar = self.classify(action)
        if ar.risk_class == RiskClass.BLOCKED:
            return RiskDecision(Route.DENY, ar.risk_class, tier, f"{action} is blocked in this deployment")
        if ar.risk_class == RiskClass.ALWAYS_HUMAN:
            return RiskDecision(Route.HUMAN, ar.risk_class, tier, f"{action} always requires human approval")
        if ar.risk_class == RiskClass.THRESHOLD and tier <= 1:
            return RiskDecision(Route.AUTO, ar.risk_class, tier, f"{action} tier {tier} ≤ 1 — autonomous execution")
        # threshold with tier ≥ 2, and AUTO
        return RiskDecision(
            Route.AUTO if ar.risk_class == RiskClass.AUTO else Route.HUMAN,
            ar.risk_class,
            tier,
            reason or f"{action} tier {tier}",
        )
