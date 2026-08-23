"""Permit (signed execution token) tests."""

from __future__ import annotations

import time

from tinyclaw.core.hitl.tokens import Permit, issue_permit, verify_permit

SECRET = "test-secret"


def test_valid_permit_roundtrip() -> None:
    token = issue_permit(
        SECRET, Permit(task_id="t1", action="po.issue", route="human", approval_id="apr_1", approver="clau")
    )
    p = verify_permit(SECRET, token, task_id="t1", action="po.issue")
    assert p is not None and p.route == "human" and p.approver == "clau"


def test_wrong_secret_rejected() -> None:
    token = issue_permit(SECRET, Permit(task_id="t1", action="po.issue", route="auto"))
    assert verify_permit("other-secret", token, task_id="t1", action="po.issue") is None


def test_permit_bound_to_task_and_action() -> None:
    token = issue_permit(SECRET, Permit(task_id="t1", action="po.issue", route="auto"))
    assert verify_permit(SECRET, token, task_id="t2", action="po.issue") is None
    assert verify_permit(SECRET, token, task_id="t1", action="payment.execute") is None


def test_expired_permit_rejected() -> None:
    stale = issue_permit(
        SECRET, Permit(task_id="t1", action="po.issue", route="auto", issued_at=time.time() - 3600, ttl=60)
    )
    assert verify_permit(SECRET, stale, task_id="t1", action="po.issue") is None


def test_garbage_token_rejected() -> None:
    assert verify_permit(SECRET, "not.a.token", task_id="t1", action="po.issue") is None
