"""Durability e2e (Phase 2): a human approval must survive an orchestrator
restart. Parked `input-required` tasks live in SQLite (data/tasks/), so a
kill -9 mid-approval loses nothing.

Boots gateway + five agents as individual processes (so the orchestrator can
be killed and restarted independently), all in mock mode.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).parent.parent
GATEWAY = "http://127.0.0.1:9100"
ORCH_URL = "http://127.0.0.1:9105"
ORCH_MODULE = "tinyclaw.scenarios.procurement.agents.orchestrator"

AGENTS = [
    "tinyclaw.scenarios.procurement.agents.intake",
    "tinyclaw.scenarios.procurement.agents.research",
    "tinyclaw.scenarios.procurement.agents.policy_agent",
    "tinyclaw.scenarios.procurement.agents.executor",
    ORCH_MODULE,
]


def _spawn(mod: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", mod], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _wait_http(url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=2.0).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


@pytest.fixture(scope="module")
def platform():
    (ROOT / "data").mkdir(exist_ok=True)
    procs = {mod: _spawn(mod) for mod in AGENTS}
    gw = subprocess.Popen(
        [sys.executable, "-m", "tinyclaw.gateway"],
        env={**os.environ, "TINYCLAW_DB": str(ROOT / "data" / "durability.sqlite")},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert _wait_http(f"{GATEWAY}/api/health"), "gateway up"
        assert _wait_http(f"{ORCH_URL}/.well-known/agent-card.json"), "orchestrator up"
        yield {"procs": procs, "gateway": gw}
    finally:
        for p in [*procs.values(), gw]:
            p.terminate()
        for p in [*procs.values(), gw]:
            p.wait(timeout=15)


def _poll_task(client: httpx.Client, task_id: str, want: str, timeout: float = 40.0) -> dict:
    deadline, t = time.time() + timeout, None
    while time.time() < deadline:
        tasks = client.get(f"{GATEWAY}/api/tasks").json()
        t = next((x for x in tasks if x["task_id"] == task_id), None)
        if t and t["state"] == want:
            return t
        time.sleep(0.5)
    raise AssertionError(f"task {task_id} never reached {want!r}; last={t}")


def test_approval_survives_orchestrator_restart(platform):
    orch = platform["procs"][ORCH_MODULE]
    with httpx.Client(timeout=120.0, base_url=GATEWAY) as client:
        # 1. A tier-2 request parks in input-required.
        r = client.post(
            "/api/playground/submit",
            json={
                "scenario": "procurement",
                "requests": [
                    {
                        "title": "restart-proof chairs",
                        "requester": "t@e.test",
                        "vendor": "Acme Office Supply",
                        "description": "chairs",
                        "amount": 12400,
                        "cost_center": "CC-1180",
                    }
                ],
            },
        )
        r.raise_for_status()
        task_id = r.json()[0]["task_id"]
        _poll_task(client, task_id, "input_required")

        # 2. SIGKILL the orchestrator while the approval is parked.
        orch.kill()
        orch.wait(timeout=10)
        time.sleep(1.0)  # let the port go

        # 3. Restart it — same SQLite task store; the parked task must survive.
        reborn = _spawn(ORCH_MODULE)
        try:
            assert _wait_http(f"{ORCH_URL}/.well-known/agent-card.json"), "orchestrator reborn"

            # 4. The human decision still lands: approve → task completes.
            pending = client.get("/api/approvals?status=pending").json()
            approval = next(a for a in pending if a["task_id"] == task_id)
            d = client.post(
                f"/api/approvals/{approval['id']}/decision", json={"decision": "approve", "approver": "durability-e2e"}
            ).json()
            assert d["decision"] == "approve"

            t = _poll_task(client, task_id, "completed")
            assert t["amount"] == 12400
            assert client.get("/api/audit/verify").json()["ok"] is True
        finally:
            reborn.terminate()
            reborn.wait(timeout=15)
