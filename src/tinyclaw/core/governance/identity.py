"""Agent identities and scopes.

Each agent in a deployment has an identity with:

* ``agent`` name — matches its Agent Card name,
* ``scopes`` — what it may do (checked by the policy agent / executor),
* ``tier`` — its risk envelope, mirroring the action risk classes.

Identities are declared per scenario pack (``identity.yaml``) and surfaced in
the Governance view. This is the accountability primitive: every audit entry
names the acting identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class AgentIdentity:
    agent: str
    scopes: list[str] = field(default_factory=list)
    tier: int = 1
    description: str = ""

    def allows(self, scope: str) -> bool:
        return scope in self.scopes or "*" in self.scopes


class IdentityRegistry:
    def __init__(self, identities: dict[str, AgentIdentity]) -> None:
        self.identities = identities

    @classmethod
    def from_yaml(cls, path: str | Path) -> IdentityRegistry:
        raw = yaml.safe_load(Path(path).read_text()) or {}
        identities = {
            name: AgentIdentity(
                agent=name,
                scopes=spec.get("scopes", []),
                tier=spec.get("tier", 1),
                description=spec.get("description", ""),
            )
            for name, spec in raw.get("agents", {}).items()
        }
        return cls(identities)

    def get(self, agent: str) -> AgentIdentity:
        # Unknown agents get an empty identity: no scopes granted implicitly.
        return self.identities.get(agent, AgentIdentity(agent=agent))

    def all(self) -> list[AgentIdentity]:
        return sorted(self.identities.values(), key=lambda i: i.agent)
