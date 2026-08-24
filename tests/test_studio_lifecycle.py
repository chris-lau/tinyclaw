"""Agent lifecycle deletion (Studio): hard delete when the agent has done
nothing, soft retire (history preserved) when it has — accountability rule,
made executable."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tinyclaw.core.config import Settings
from tinyclaw.gateway.app import create_app


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(database_path=tmp_path / "studio.sqlite", service_name="test-gateway")
    return TestClient(create_app(settings))


def test_hard_delete_when_no_activity(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        c.post("/api/studio/agents", json={"name": "ghost", "system_prompt": "x"})
        r = c.delete("/api/studio/agents/ghost")
        assert r.status_code == 200
        body = r.json()
        assert body["deleted"] == "hard"
        names = [d["name"] for d in c.get("/api/studio/agents").json()]
        assert "ghost" not in names, "no activity → definition removed entirely"


def test_soft_retire_when_activity_exists(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        c.post("/api/studio/agents", json={"name": "worker", "system_prompt": "x"})
        token = {"authorization": "Bearer dev-internal-token"}
        # the agent served something: it produced an artifact (audit record)
        c.post(
            "/internal/audit",
            json={"actor": "agent:worker", "action": "a2a.artifact", "subject": "t1", "decision": ""},
            headers=token,
        )
        r = c.delete("/api/studio/agents/worker")
        assert r.status_code == 200
        body = r.json()
        assert body["deleted"] == "soft" and "a2a.artifact" in body["evidence"]

        defs = {d["name"]: d for d in c.get("/api/studio/agents").json()}
        assert defs["worker"]["status"] == "retired", "activity → retired, not erased"

        # retired agents cannot be redeployed
        assert c.post("/api/studio/agents/worker/deploy").status_code == 409


def test_lifecycle_actions_do_not_count_as_activity(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        # define + deploy-approval audit entries exist for every agent; alone
        # they must NOT force soft-delete
        c.post("/api/studio/agents", json={"name": "quiet", "system_prompt": "x"})
        token = {"authorization": "Bearer dev-internal-token"}
        c.post(
            "/internal/audit",
            json={"actor": "gateway", "action": "agent.deploy", "subject": "quiet", "decision": "live"},
            headers=token,
        )
        r = c.delete("/api/studio/agents/quiet")
        assert r.json()["deleted"] == "hard", "define/deploy lifecycle alone is not activity"


def test_deletions_are_audited_and_chain_survives(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        c.post("/api/studio/agents", json={"name": "a", "system_prompt": "x"})
        c.delete("/api/studio/agents/a")
        c.post("/api/studio/agents", json={"name": "b", "system_prompt": "x"})
        token = {"authorization": "Bearer dev-internal-token"}
        c.post(
            "/internal/audit",
            json={"actor": "agent:b", "action": "task.state", "subject": "t2", "decision": "completed"},
            headers=token,
        )
        c.delete("/api/studio/agents/b")

        audit = c.get("/api/audit").json()
        deletes = [a for a in audit if a["action"] == "agent.delete"]
        assert {d["decision"] for d in deletes} == {"hard", "soft"}, "both deletion kinds recorded"
        assert c.get("/api/audit/verify").json()["ok"] is True


def test_unknown_agent_404(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        assert c.delete("/api/studio/agents/nope").status_code == 404
