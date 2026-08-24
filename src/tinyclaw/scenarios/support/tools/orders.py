"""Mock order & customer systems for the support scenario."""

from __future__ import annotations

ORDERS: dict[str, dict] = {
    "ord-1001": {"customer": "acme-corp", "plan": "pro", "amount": 35, "days_ago": 6, "category": "hardware"},
    "ord-1002": {"customer": "globex", "plan": "starter", "amount": 180, "days_ago": 12, "category": "subscription"},
    "ord-1003": {"customer": "initech", "plan": "enterprise", "amount": 750, "days_ago": 2, "category": "sla"},
    "ord-1004": {"customer": "umbrella", "plan": "pro", "amount": 60, "days_ago": 40, "category": "subscription"},
    "ord-1005": {"customer": "hooli", "plan": "starter", "amount": 25, "days_ago": 1, "category": "addon"},
}

CUSTOMERS: dict[str, dict] = {
    "acme-corp": {"tier": "gold", "lifetime_value": 48000, "churn_risk": False},
    "globex": {"tier": "silver", "lifetime_value": 9200, "churn_risk": False},
    "initech": {"tier": "platinum", "lifetime_value": 210000, "churn_risk": True},
    "umbrella": {"tier": "silver", "lifetime_value": 3100, "churn_risk": True},
    "hooli": {"tier": "free", "lifetime_value": 480, "churn_risk": False},
}


def lookup_order(order_id: str) -> dict:
    order = dict(ORDERS.get(order_id, {}))
    order.setdefault("order_id", order_id)
    order["found"] = order_id in ORDERS
    customer = CUSTOMERS.get(order.get("customer", ""), {})
    return {**order, "customer_profile": customer or None}
