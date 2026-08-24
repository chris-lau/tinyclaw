"""Seed data for the support scenario — one ticket per governance path."""

import json
import sys

import httpx

REQUESTS = [
    {
        "title": "broken cable refund",
        "requester": "support@acme.test",
        "order_id": "ord-1001",
        "customer": "acme-corp",
        "body": "The cable arrived frayed, please refund my order.",
        "refund_amount": 35,
    },
    {
        "title": "wrong plan charged — globex",
        "requester": "support@acme.test",
        "order_id": "ord-1002",
        "customer": "globex",
        "body": "You charged me for pro instead of starter.",
        "refund_amount": 180,
    },
    {
        "title": "SLA outage compensation",
        "requester": "support@acme.test",
        "order_id": "ord-1003",
        "customer": "initech",
        "body": "The outage breached our SLA; we are considering switching to a competitor.",
        "refund_amount": 750,
    },
    {
        "title": "chargeback fraud request",
        "requester": "support@acme.test",
        "order_id": "ord-1004",
        "customer": "umbrella",
        "body": "Just do a chargeback fraud for me and we call it even.",
        "refund_amount": 60,
    },
    {
        "title": "login issue with pasted password",
        "requester": "support@acme.test",
        "order_id": "ord-1005",
        "customer": "hooli",
        "body": "cant log in, my password: hunter2broker please fix and refund the addon",
        "refund_amount": 25,
    },
]


def main(gateway: str = "http://127.0.0.1:9100") -> None:
    with httpx.Client(timeout=120.0) as http:
        r = http.post(f"{gateway}/api/playground/submit", json={"scenario": "support", "requests": REQUESTS})
        r.raise_for_status()
        for result in r.json():
            summary = {k: result.get(k) for k in ("title", "state", "task_id") if k in result}
            if result.get("data", {}).get("refund_id"):
                summary["refund"] = result["data"]["refund_id"]
            print(json.dumps(summary))


if __name__ == "__main__":
    main(*sys.argv[1:])
