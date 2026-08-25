"""Editable policy sets (DB-backed, hot-reload) + audit filtering."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tinyclaw.core.config import Settings
from tinyclaw.core.governance.policy import PolicyEngine
from tinyclaw.gateway.app import create_app

PROD = Path(__file__).parent.parent / "src" / "tinyclaw" / "scenarios" / "procurement" / "policies"


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(database_path=tmp_path / "policy.sqlite", service_name="test-gateway")
    return TestClient(create_app(settings))


def test_pack_seeds_db_set_on_boot(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        r = c.get("/api/policy-sets/procurement")
        assert r.status_code == 200
        body = r.json()
        assert body["version"] == 1 and "procurement.amount.tier2" in body["yaml"]


def test_edit_validates_versions_and_audits(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        # invalid YAML rejected
        bad = c.put("/api/policy-sets/procurement", json={"yaml": "policies: [oops"})
        assert bad.status_code == 422
        # empty rules rejected
        bad2 = c.put("/api/policy-sets/procurement", json={"yaml": "policies: []\n"})
        assert bad2.status_code == 422

        original = c.get("/api/policy-sets/procurement").json()["yaml"]
        edited = original.replace("value: 5000", "value: 9000")  # tier-2 threshold moves
        r = c.put("/api/policy-sets/procurement", json={"yaml": edited, "updated_by": "pm"})
        assert r.status_code == 200
        assert r.json()["version"] == 2 and r.json()["rules"] >= 5

        # the saved set evaluates with the new threshold
        t = c.post(
            "/api/policy-sets/procurement/test",
            json={"payload": {"amount": 6000, "vendor": {"sanctioned": False}, "injection_flags": 0}},
        ).json()
        assert t["effect"] == "allow", "$6k is now under the moved tier-2 threshold"

        # audited
        audit = c.get("/api/audit", params={"action": "policy.update"}).json()
        assert audit and audit[0]["actor"] == "human:pm"


def test_dry_run_on_draft_without_saving(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        draft = (PROD / "procurement.yaml").read_text().replace("value: 5000", "value: 100")
        r = c.post(
            "/api/policy-sets/procurement/test",
            json={"yaml": draft, "payload": {"amount": 500, "vendor": {"sanctioned": False}, "injection_flags": 0}},
        )
        assert r.json()["effect"] == "require_approval"
        # saved set untouched
        assert c.get("/api/policy-sets/procurement").json()["version"] == 1


def test_from_text_engine(tmp_path: Path) -> None:
    text = """
policies:
  - id: test.rule
    when: {path: amount, op: ">=", value: 100}
    effect: deny
"""
    engine = PolicyEngine.from_text(text)
    assert engine.evaluate({"amount": 200}).is_denied
    assert not engine.evaluate({"amount": 10}).is_denied
    try:
        PolicyEngine.from_text("policies: []")
        raise AssertionError("empty set must raise")
    except ValueError:
        pass


def test_audit_filters(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        token = {"authorization": "Bearer dev-internal-token"}
        c.post(
            "/internal/audit",
            json={"actor": "agent:policy", "action": "policy.evaluate", "subject": "t1", "decision": "deny"},
            headers=token,
        )
        c.post(
            "/internal/audit",
            json={"actor": "human:pm", "action": "policy.update", "subject": "procurement", "decision": "v2"},
            headers=token,
        )

        assert all(a["actor"] == "human:pm" for a in c.get("/api/audit", params={"actor": "human"}).json())
        assert len(c.get("/api/audit", params={"action": "policy.update"}).json()) == 1
        assert len(c.get("/api/audit", params={"decision": "deny"}).json()) >= 1
        assert len(c.get("/api/audit", params={"q": "UPDATE"}).json()) == 1
        assert c.get("/api/audit", params={"q": "zzz-nothing"}).json() == []
