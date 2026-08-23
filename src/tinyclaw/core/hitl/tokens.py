"""Signed execution permits.

Nothing consequential executes without a permit issued by the gateway after
governance checks:

* ``AUTO`` route — the gateway audits the policy decision and signs
  immediately (autonomous execution is still authorized + audited).
* ``HUMAN`` route — the gateway signs only after a recorded human decision;
  the approver's identity is embedded in the permit.

Permit format: ``base64url(json_payload).hex_hmac_sha256`` — readable,
verifiable offline by the executor via the shared secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

DEFAULT_TTL_SECONDS = 15 * 60


@dataclass(frozen=True)
class Permit:
    task_id: str
    action: str
    route: str  # "auto" | "human"
    tier: int = 1
    approval_id: str | None = None
    approver: str | None = None
    issued_at: float = 0.0
    ttl: int = DEFAULT_TTL_SECONDS

    def to_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "action": self.action,
            "route": self.route,
            "tier": self.tier,
            "approval_id": self.approval_id,
            "approver": self.approver,
            "issued_at": self.issued_at or time.time(),
            "ttl": self.ttl,
        }


def _sign(secret: str, body: str) -> str:
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


def issue_permit(secret: str, permit: Permit) -> str:
    body = base64.urlsafe_b64encode(json.dumps(permit.to_payload(), sort_keys=True).encode()).decode().rstrip("=")
    return f"{body}.{_sign(secret, body)}"


def verify_permit(secret: str, token: str, *, task_id: str, action: str) -> Permit | None:
    """Verify signature, binding (task + action) and freshness. None = rejected."""
    try:
        body, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, _sign(secret, body)):
            return None
        pad = "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body + pad))
        permit = Permit(**payload)
    except Exception:
        return None
    if permit.task_id != task_id or permit.action != action:
        return None
    if time.time() - permit.issued_at > permit.ttl:
        return None
    return permit
