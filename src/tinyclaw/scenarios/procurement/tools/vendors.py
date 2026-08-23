"""Mock vendor registries standing in for enterprise systems of record."""

from __future__ import annotations

VENDORS: dict[str, dict] = {
    "acme office supply": {
        "vendor_id": "vnd-291",
        "tier": "A",
        "on_time_pct": 96,
        "years": 3,
        "last_po": {"amount": 9100, "months_ago": 4},
        "benchmark_delta_pct": -8,
    },
    "keychron": {
        "vendor_id": "vnd-118",
        "tier": "B",
        "on_time_pct": 91,
        "years": 2,
        "last_po": None,
        "benchmark_delta_pct": 0,
    },
    "douk": {
        "vendor_id": "vnd-045",
        "tier": "B",
        "on_time_pct": 88,
        "years": 1,
        "last_po": None,
        "benchmark_delta_pct": 3,
    },
    "dell": {
        "vendor_id": "vnd-003",
        "tier": "A",
        "on_time_pct": 94,
        "years": 6,
        "last_po": {"amount": 41000, "months_ago": 9},
        "benchmark_delta_pct": -2,
    },
    "benq": {
        "vendor_id": "vnd-207",
        "tier": "B",
        "on_time_pct": 90,
        "years": 2,
        "last_po": None,
        "benchmark_delta_pct": 1,
    },
    "figma inc": {
        "vendor_id": "vnd-512",
        "tier": "A",
        "on_time_pct": 99,
        "years": 4,
        "last_po": {"amount": 15200, "months_ago": 12},
        "benchmark_delta_pct": -5,
    },
    "northwind trading": {
        "vendor_id": "vnd-777",
        "tier": "C",
        "on_time_pct": 61,
        "years": 1,
        "last_po": None,
        "benchmark_delta_pct": 22,
    },
}

SANCTIONED = {"northwind trading", "globex sanctions ltd"}

BUDGETS = {
    "CC-1180": {"remaining": 48200, "owner": "ops@acme.test"},
    "CC-2040": {"remaining": 121000, "owner": "fin@acme.test"},
}


def lookup_vendor(name: str) -> dict:
    """Vendor profile or a minimal unknown-vendor record."""
    key = (name or "").strip().lower()
    profile = dict(VENDORS.get(key, {}))
    profile.setdefault("vendor_id", "vnd-unknown")
    profile.setdefault("tier", "unrated")
    profile["name"] = name
    profile["sanctioned"] = key in SANCTIONED
    return profile


def budget_for(cost_center: str | None) -> dict:
    return BUDGETS.get(cost_center or "", {"remaining": 0, "owner": None, "unknown_cost_center": True})
