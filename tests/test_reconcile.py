"""Reconciliation: tasks parked in input_required whose approval is already
decided must not linger as false 'pending' — they reconcile to an honest
terminal state (the redeploy fossil case from docs/deployment.md)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tinyclaw.core.config import Settings
from tinyclaw.gateway.app import create_app


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(database_path=tmp_path / "reconcile.sqlite", service_name="test-gateway")
    return TestClient(create_app(settings))


def _park_and_decide(c: TestClient, title: str) -> str:
    """Create a task + approval directly (as the mesh would), then decide."""
    import uuid

    task_id = f"t-{uuid.uuid4().hex[:8]}"
    token = {"authorization": "Bearer dev-internal-token"}
    c.post("/internal/tasks", json={"task_id": task_id, "scenario": "procurement", "title": title,
                                    "amount": 12400, "state": "input_required", "stage": "approval"}, headers=token)
    apr_id = f"apr_{uuid.uuid4().hex[:8]}"
    c.post("/internal/audit", json={"actor": "gateway", "action": "approval.requested", "subject": task_id}, headers=token)
    # decide with an orchestrator URL that does not exist → resume fails
    c.post("/internal/permits", json={"task_id": task_id, "action": "po.issue", "route": "human",
                                      "tier": 2, "scenario": "procurement", "subject": title,
                                      "amount": 12400, "orchestrator_url": "http://127.0.0.1:9"}, headers=token)
    r = c.post(f"/api/approvals/{[a['id'] for a in c.get('/api/approvals?status=pending').json() if a['task_id'] == task_id][0]}/decision",
               json={"decision": "approve", "approver": "t"})
    assert r.status_code == 200
    return task_id


def test_failed_resume_reconciles_immediately(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        task_id = _park_and_decide(c, "chairs")
        t = next(x for x in c.get("/api/tasks").json() if x["task_id"] == task_id)
        assert t["state"] == "failed" and t["stage"] == "stale", "approved-but-unresumable reconciles at decide time"


def test_reconcile_sweep_catches_pre_existing_fossils(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        task_id = _park_and_decide(c, "old fossil")
        # simulate the pre-fix behavior: task stuck in input_required
        token = {"authorization": "Bearer dev-internal-token"}
        c.post("/internal/tasks", json={"task_id": task_id, "scenario": "procurement", "title": "old fossil",
                                        "state": "input_required", "stage": "approval"}, headers=token)
        r = c.post("/api/admin/reconcile")
        assert r.status_code == 200
        fixed = {x["task_id"]: x["state"] for x in r.json()["reconciled"]}
        assert fixed.get(task_id) == "failed"
        t = next(x for x in c.get("/api/tasks").json() if x["task_id"] == task_id)
        assert t["state"] == "failed" and t["stage"] == "stale"


def test_reconcile_leaves_genuinely_pending_alone(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        import uuid

        token = {"authorization": "Bearer dev-internal-token"}
        task_id = f"t-{uuid.uuid4().hex[:8]}"
        c.post("/internal/tasks", json={"task_id": task_id, "scenario": "support", "title": "live park",
                                        "state": "input_required", "stage": "approval"}, headers=token)
        c.post("/internal/permits", json={"task_id": task_id, "action": "refund.issue", "route": "human",
                                          "tier": 2, "scenario": "support", "subject": "live park",
                                          "orchestrator_url": None}, headers=token)
        r = c.post("/api/admin/reconcile")
        assert not r.json()["reconciled"], "pending approvals must not be swept"
