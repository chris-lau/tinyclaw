"""Tool registry (configurable, executable) + SSRF guard for http tools."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tinyclaw.core.config import Settings
from tinyclaw.gateway.app import create_app
from tinyclaw.runtime.tools import execute_tool


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(database_path=tmp_path / "tools.sqlite", service_name="test-gateway")
    return TestClient(create_app(settings))


def test_default_catalog_seeded(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        names = {t["name"] for t in c.get("/api/studio/tools").json()}
        assert {"flights.search", "http.request", "payments.refund"} <= names


def test_create_update_and_validation(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        # mock tool with a response sample
        r = c.post(
            "/api/studio/tools",
            json={
                "name": "crm.lookup",
                "kind": "mock",
                "config": {"response": "account ACME · gold"},
                "high_risk": False,
            },
        )
        assert r.status_code == 200 and r.json()["version"] == 1
        # re-create bumps version (update path)
        r2 = c.post(
            "/api/studio/tools",
            json={
                "name": "crm.lookup",
                "kind": "mock",
                "config": {"response": "account ACME · platinum"},
                "high_risk": False,
            },
        )
        assert r2.json()["version"] == 2
        # mock without a response → 422
        assert c.post("/api/studio/tools", json={"name": "bad.mock", "kind": "mock", "config": {}}).status_code == 422
        # http without absolute url → 422
        assert (
            c.post(
                "/api/studio/tools", json={"name": "bad.http", "kind": "http", "config": {"url": "example.com"}}
            ).status_code
            == 422
        )
        # creation audited
        audit = c.get("/api/audit", params={"action": "tool.define"}).json()
        assert any(a["subject"] == "crm.lookup" for a in audit)


def test_delete_blocked_while_bound(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        c.post("/api/studio/tools", json={"name": "crm.lookup", "kind": "mock",
                                          "config": {"response": "account ACME"}, "high_risk": False})
        c.post("/api/studio/agents", json={"name": "crm-agent", "system_prompt": "x", "tools": ["crm.lookup"]})
        r = c.delete("/api/studio/tools/crm.lookup")
        assert r.status_code == 409
        # unbind → delete succeeds
        c.post("/api/studio/agents", json={"name": "crm-agent", "system_prompt": "x", "tools": []})
        assert c.delete("/api/studio/tools/crm.lookup").status_code == 200


def test_mock_execution(tmp_path: Path) -> None:
    out = execute_tool({"name": "greeter", "kind": "mock", "config": {"response": "hello {input}!"}}, "world")
    assert out.ok and out.output == "hello world!"


def test_http_tool_ssf_guard() -> None:
    for target in (
        "http://127.0.0.1:9100/api/health",
        "http://localhost/x",
        "http://169.254.169.254/meta",
        "http://10.0.0.1/x",
        "http://192.168.1.1/x",
    ):
        out = execute_tool({"name": "t", "kind": "http", "config": {"url": target}})
        assert not out.ok and "refused" in out.output, f"{target} must be refused by the SSRF guard"


def test_http_tool_bad_scheme() -> None:
    out = execute_tool({"name": "t", "kind": "http", "config": {"url": "file:///etc/passwd"}})
    assert not out.ok
