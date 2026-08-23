"""Service URLs for the procurement pack.

Defaults are local-dev (each agent on its own 127.0.0.1 port). Every URL is
overridable via env (``TINYCLAW_INTAKE_URL`` etc.), which is how docker
compose points agents at each other by service name.
"""

from __future__ import annotations

import os


def url(name: str, port: int) -> str:
    return os.environ.get(f"TINYCLAW_{name.upper()}_URL", f"http://127.0.0.1:{port}")


INTAKE_URL = url("intake", 9101)
RESEARCH_URL = url("research", 9102)
POLICY_URL = url("policy", 9103)
EXECUTOR_URL = url("executor", 9104)
SELF_URL = url("orchestrator", 9105)
