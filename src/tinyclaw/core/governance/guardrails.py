"""Input/output guardrails: PII redaction and prompt-injection heuristics.

Every untrusted payload passes through ``redact_pii`` before it reaches an
LLM or the audit log; ``scan_injection`` flags classic prompt-injection
attempts in untrusted text so agents can refuse or annotate them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)(\+?\d[\d\-() ]{8,}\d)(?!\d)")
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")

_INJECTION_PATTERNS = [
    r"ignore [a-z ]{0,30}(instructions|prompts|rules)",
    r"disregard [a-z ]{0,30}(instructions|prompts|rules|above)",
    r"you are now",
    r"system prompt",
    r"reveal (your|the) (instructions|prompt)",
    r"act as (if you are )?an? (unrestricted|unfiltered|dan)",
]


@dataclass
class GuardrailReport:
    redactions: int = 0
    redacted_fields: list[str] = field(default_factory=list)
    injection_flags: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.redactions and not self.injection_flags


def redact_text(text: str) -> tuple[str, int]:
    """Mask emails/phones/card-numbers; returns (redacted_text, count)."""
    count = 0

    def _sub(m: re.Match) -> str:
        nonlocal count
        count += 1
        s = m.group(0)
        return s[:2] + "••••" + s[-2:] if len(s) > 6 else "••••"

    text = _CARD_RE.sub(_sub, text)
    text = _EMAIL_RE.sub(_sub, text)
    text = _PHONE_RE.sub(_sub, text)
    return text, count


def redact_pii(payload: Any, path: str = "") -> tuple[Any, GuardrailReport]:
    """Deep-walk a payload, redacting PII inside strings. Returns (payload, report)."""
    report = GuardrailReport()

    def walk(node: Any, p: str) -> Any:
        if isinstance(node, dict):
            return {k: walk(v, f"{p}.{k}" if p else k) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, p) for v in node]
        if isinstance(node, str):
            red, n = redact_text(node)
            if n:
                report.redactions += n
                report.redacted_fields.append(p)
            return red
        return node

    return walk(payload, path), report


def scan_injection(text: str) -> list[str]:
    """Return the list of injection heuristics triggered by *text* (empty = clean)."""
    lowered = text.lower()
    return [pat for pat in _INJECTION_PATTERNS if re.search(pat, lowered)]
