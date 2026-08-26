"""Generalized config sets: risk registries, hooks, and identities are
editable + hot-applied like policy sets; coded-agent prompts override live."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tinyclaw.core.config import Settings
from tinyclaw.core.governance.hooks import HookEngine
from tinyclaw.core.governance.risk import RiskRouter
from tinyclaw.gateway.app import create_app

PACKS = Path(__file__).parent.parent / "src" / "tinyclaw" / "scenarios"


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(database_path=tmp_path / "sets.sqlite", service_name="test-gateway")
    return TestClient(create_app(settings))


def test_all_kinds_seeded_for_every_scenario(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        seen = {(p["scenario"], p["kind"]) for p in c.get("/api/policies").json()}
        for scen in ("procurement", "support"):
            for kind in ("policy", "risk", "hooks", "identity"):
                assert (scen, kind) in seen, f"{scen}/{kind} missing"


def test_risk_registry_edit_hot_applies(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        original = c.get("/api/sets/procurement/risk").json()["yaml"]
        edited = original.replace("risk_class: threshold", "risk_class: always_human", 1)
        r = c.put("/api/sets/procurement/risk", json={"yaml": edited, "updated_by": "pm"})
        assert r.status_code == 200 and r.json()["version"] == 2
        # the saved registry compiles with the new class
        router = RiskRouter.from_text(c.get("/api/sets/procurement/risk").json()["yaml"])
        assert router.classify("po.issue").risk_class.value == "always_human"
        # empty registry rejected
        assert c.put("/api/sets/procurement/risk", json={"yaml": "actions: {}\n"}).status_code == 422


def test_hooks_edit_hot_applies(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        edited = """
hooks:
  - id: test.block.all
    when: {path: text, op: matches, value: "secret"}
    effect: block
"""
        r = c.put("/api/sets/procurement/hooks", json={"yaml": edited})
        assert r.status_code == 200
        # the gateway's live hook engine now serves the DB set (no restart):
        token = {"authorization": "Bearer dev-internal-token"}
        decision = c.post("/internal/hooks/eval", json={"text": "here is a secret", "data": {}}, headers=token).json()
        assert decision["action"] == "block", "edited hook must fire without a gateway restart"


def test_identity_edit_shape_validated(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        assert c.put("/api/sets/procurement/identity", json={"yaml": "agents: []"}).status_code == 422
        ok = c.put("/api/sets/procurement/identity", json={"yaml": "agents:\n  intake:\n    scopes: [request:read]\n"})
        assert ok.status_code == 200


def test_agent_prompt_override(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        # default: no override
        assert c.get("/api/agent-prompts/intake").json()["system_prompt"] is None
        r = c.put("/api/agent-prompts/intake", json={"system_prompt": "Extract invoices. Be terse."})
        assert r.status_code == 200 and r.json()["version"] == 1
        assert c.get("/api/agent-prompts/intake").json()["system_prompt"].startswith("Extract invoices")
        # unknown agent rejected
        assert c.put("/api/agent-prompts/orchestrator", json={"system_prompt": "x"}).status_code == 404
        # audited
        audit = c.get("/api/audit", params={"action": "prompt.update"}).json()
        assert audit and audit[0]["subject"] == "intake"


def test_hook_engine_from_text(tmp_path: Path) -> None:
    engine = HookEngine.from_text("hooks:\n  - id: x\n    when: {path: text, op: exists}\n    effect: annotate\n")
    d = engine.evaluate("anything", {})
    assert d.action == "allow" and d.annotations and d.annotations[0]["hook"] == "x"
