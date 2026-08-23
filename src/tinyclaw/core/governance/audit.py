"""Tamper-evident, hash-chained audit log.

Every consequential event — policy decisions, guardrail hits, human approvals,
executions — becomes an entry whose hash covers the previous entry's hash:

    entry_n.hash = sha256(entry_n.prev_hash || canonical_json(entry_n))

Any retroactive edit breaks the chain and ``verify()`` pinpoints where.
The gateway is the single writer, which keeps the chain linear.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

GENESIS = "0" * 64


@dataclass
class AuditEntry:
    actor: str  # e.g. "agent:policy", "human:clau", "guardrail", "gateway"
    action: str  # e.g. "policy.evaluate", "approval.decide", "po.execute"
    subject: str  # what it concerns, e.g. task id or agent name
    decision: str = ""  # short outcome, e.g. "require_approval"
    details: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    prev_hash: str = GENESIS
    hash: str = ""  # filled on append

    def canonical(self) -> str:
        d = {k: v for k, v in asdict(self).items() if k not in ("hash",)}
        return json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)


def compute_hash(entry: AuditEntry) -> str:
    return hashlib.sha256((entry.prev_hash + entry.canonical()).encode()).hexdigest()


class AuditLog:
    """In-process chain. The gateway persists it via its sqlite store; tests
    use this class directly."""

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []
        self._lock = threading.Lock()

    def append(self, actor: str, action: str, subject: str, decision: str = "", **details: Any) -> AuditEntry:
        with self._lock:
            prev = self._entries[-1].hash if self._entries else GENESIS
            entry = AuditEntry(
                actor=actor, action=action, subject=subject, decision=decision, details=details, prev_hash=prev
            )
            entry.hash = compute_hash(entry)
            self._entries.append(entry)
            return entry

    def entries(self) -> list[AuditEntry]:
        return list(self._entries)

    def verify(self) -> tuple[bool, int]:
        """Return (ok, first_bad_index)."""
        prev = GENESIS
        for i, e in enumerate(self._entries):
            if e.prev_hash != prev or e.hash != compute_hash(e):
                return False, i
            prev = e.hash
        return True, -1
