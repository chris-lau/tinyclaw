"""Policy-as-code engine.

Policies are plain YAML: a list of rules, each a condition over a payload
(dotted-path lookup + comparison operator) with an effect of
``allow`` / ``deny`` / ``require_approval``.

Evaluation semantics — deliberately conservative:

* **Every** rule is evaluated and every hit is reported (no short-circuit),
  so the audit log shows the full reasoning, not just the outcome.
* The **most restrictive** effect wins: deny > require_approval > allow.
* No rules hit ⇒ the default effect for the policy set applies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class Effect(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


_SEVERITY = {Effect.ALLOW: 0, Effect.REQUIRE_APPROVAL: 1, Effect.DENY: 2}


@dataclass(frozen=True)
class Condition:
    path: str  # dotted path into the payload, e.g. "vendor.sanctioned"
    op: str  # == != > >= < <= in contains exists
    value: Any = None

    def matches(self, payload: dict[str, Any]) -> bool:
        node: Any = payload
        for part in self.path.split("."):
            if not isinstance(node, dict) or part not in node:
                node = None
                break
            node = node[part]
        if self.op == "exists":
            return node is not None
        if node is None:
            return False
        try:
            if self.op == "==":
                return node == self.value
            if self.op == "!=":
                return node != self.value
            if self.op == ">=":
                return float(node) >= float(self.value)
            if self.op == ">":
                return float(node) > float(self.value)
            if self.op == "<=":
                return float(node) <= float(self.value)
            if self.op == "<":
                return float(node) < float(self.value)
            if self.op == "in":
                return node in self.value
            if self.op == "contains":
                return self.value in node
        except (TypeError, ValueError):
            return False
        return False


@dataclass(frozen=True)
class Rule:
    id: str
    description: str = ""
    priority: int = 100
    when: Condition | None = None
    effect: Effect = Effect.ALLOW
    tier: int | None = None  # optional risk tier carried by require_approval


@dataclass
class RuleHit:
    rule: Rule
    detail: str = ""


@dataclass
class PolicyDecision:
    effect: Effect
    hits: list[RuleHit] = field(default_factory=list)
    tier: int | None = None

    @property
    def is_denied(self) -> bool:
        return self.effect == Effect.DENY

    @property
    def needs_approval(self) -> bool:
        return self.effect == Effect.REQUIRE_APPROVAL

    def summary(self) -> str:
        if not self.hits:
            return f"default → {self.effect.value}"
        return "; ".join(
            f"{h.rule.id} → {h.rule.effect.value}{f' (tier {h.rule.tier})' if h.rule.tier else ''}" for h in self.hits
        )


class PolicyEngine:
    """Loads a policy set from YAML and evaluates payloads against it."""

    def __init__(self, rules: list[Rule], default_effect: Effect = Effect.ALLOW) -> None:
        self.rules = sorted(rules, key=lambda r: r.priority)
        self.default_effect = default_effect

    @classmethod
    def from_yaml(cls, path: str | Path) -> PolicyEngine:
        raw = yaml.safe_load(Path(path).read_text()) or {}
        rules: list[Rule] = []
        for entry in raw.get("policies", []):
            when = entry.get("when") or {}
            cond = Condition(path=when["path"], op=when.get("op", "=="), value=when.get("value")) if when else None
            rules.append(
                Rule(
                    id=entry["id"],
                    description=entry.get("description", ""),
                    priority=entry.get("priority", 100),
                    when=cond,
                    effect=Effect(entry.get("effect", "allow")),
                    tier=entry.get("tier"),
                )
            )
        return cls(rules, default_effect=Effect(raw.get("default_effect", "allow")))

    def evaluate(self, payload: dict[str, Any]) -> PolicyDecision:
        hits: list[RuleHit] = [
            RuleHit(rule=r, detail=f"{r.when.path} {r.when.op} {r.when.value!r}" if r.when else "unconditional")
            for r in self.rules
            if r.when is None or r.when.matches(payload)
        ]
        if not hits:
            return PolicyDecision(effect=self.default_effect)
        worst = max(hits, key=lambda h: _SEVERITY[h.rule.effect])
        tiers = [h.rule.tier for h in hits if h.rule.tier is not None]
        return PolicyDecision(effect=worst.rule.effect, hits=hits, tier=max(tiers) if tiers else None)
