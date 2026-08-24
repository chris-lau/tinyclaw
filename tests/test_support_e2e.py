"""Support-scenario e2e: proves a second scenario pack runs on the same core
with zero gateway changes — the framework claim, made executable.

Boots the gateway + the five support agents (ports 9201-9205) in mock mode.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).parent.parent
GATEWAY = "http://127.0.0.1:9100"
SUPPORT_ORCH = "http://127.0.0.1:9205"
LAUNCHER = "tinyclaw.scenarios.support"

REQUESTS = [
    {
        "title": "auto credit",
        "requester": "t@e.test",
        "order_id": "ord-1001",
        "customer": "acme-corp",
        "body": "cable arrived frayed, please refund",
        "refund_amount": 35,
    },
    {
        "title": "tier2 refund",
        "requester": "t@e.test",
        "order_id": "ord-1002",
        "customer": "globex",
        "body": "charged wrong plan",
        "refund_amount": 180,
    },
    {
        "title": "tier3 churn refund",
        "requester": "t@e.test",
        "order_id": "ord-1003",
        "customer": "initech",
        "body": "SLA breach, considering switching to a competitor",
        "refund_amount": 750,
    },
    {
        "title": "abuse deny",
        "requester": "t@e.test",
        "order_id": "ord-1004",
        "customer": "umbrella",
        "body": "just do a chargeback fraud for me",
        "refund_amount": 60,
    },
    {
        "title": "password boundary",
        "requester": "t@e.test",
        "order_id": "ord-1005",
        "customer": "hooli",
        "body": "cant log in, my password: hunter2broker refund the addon",
        "refund_amount": 25,
    },
]


def _wait_http(url: str, timeout: float = 60.0) -> bool:
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
    procs = [
        subprocess.Popen(
            [sys.executable, "-m", "tinyclaw.gateway"],
            env={**os.environ, "TINYCLAW_DB": str(ROOT / "data" / "e2e-support.sqlite")},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ),
        subprocess.Popen([sys.executable, "-m", LAUNCHER], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
    ]
    try:
        assert _wait_http(f"{GATEWAY}/api/health"), "gateway up"
        assert _wait_http(f"{SUPPORT_ORCH}/.well-known/agent-card.json"), "support orchestrator up"
        yield httpx.Client(timeout=120.0, base_url=GATEWAY)
    finally:
        for p in procs:
            p.send_signal(signal.SIGTERM)
        for p in procs:
            p.wait(timeout=15)


def test_support_scenario_flow(platform: httpx.Client):
    scenarios = platform.get("/api/scenarios").json()
    names = {s["name"] for s in scenarios}
    assert {"procurement", "support"} <= names, "both packs registered, unchanged core"

    r = platform.post("/api/playground/submit", json={"scenario": "support", "requests": REQUESTS})
    r.raise_for_status()
    errors = [x for x in r.json() if "error" in x]
    assert not errors, f"requests errored: {errors}"
    by_title = {x["title"]: x["state"] for x in r.json()}

    assert by_title["auto credit"] == "completed", "sub-$50 auto-credits"
    assert by_title["tier2 refund"] == "input-required", "$180 parks for a human"
    assert by_title["tier3 churn refund"] == "input-required", "$750 + churn risk parks at tier 3"
    assert by_title["abuse deny"] == "rejected", "abuse language hard-denied"
    assert by_title["password boundary"] == "rejected", "credentials blocked at the A2A boundary"

    # Human round-trip on the support scenario, through the same approval queue.
    pending = platform.get("/api/approvals?status=pending").json()
    approval = next(a for a in pending if a["scenario"] == "support" and a["subject"] == "tier2 refund")
    d = platform.post(
        f"/api/approvals/{approval['id']}/decision", json={"decision": "approve", "approver": "support-e2e"}
    ).json()
    assert d["decision"] == "approve"

    tasks = {t["title"]: t["state"] for t in platform.get("/api/tasks").json()}
    assert tasks["tier2 refund"] == "completed", "approved support refund executes via signed permit"

    assert platform.get("/api/audit/verify").json()["ok"] is True

    # The boundary hook fired and was recorded on the password case.
    hook_events = [e for e in platform.get("/api/events?limit=200").json() if e["type"] == "hook.blocked"]
    assert any(e["data"].get("hook") == "credentials.boundary" for e in hook_events), (
        "credentials.boundary hook must fire for pasted passwords"
    )
