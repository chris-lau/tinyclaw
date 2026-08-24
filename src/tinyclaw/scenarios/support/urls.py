"""Service URLs for the support pack (ports 9201-9205; env-overridable)."""

from __future__ import annotations

from ..procurement.urls import url

INTAKE_URL = url("support_intake", 9201)
RESEARCH_URL = url("support_research", 9202)
POLICY_URL = url("support_policy", 9203)
EXECUTOR_URL = url("support_executor", 9204)
SELF_URL = url("support_orchestrator", 9205)
