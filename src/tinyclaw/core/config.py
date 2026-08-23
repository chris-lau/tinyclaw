"""Central configuration, driven entirely by environment variables.

Everything has a working default so the whole platform runs with zero setup
(``mock`` LLM mode, no OTLP endpoint, in-process event bus).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


@dataclass(frozen=True)
class Settings:
    # Identity of this process for tracing / events.
    service_name: str = "tinyclaw"

    # LLM: "mock" (default, deterministic, keyless) | "openai" | "anthropic"
    llm_provider: str = field(default_factory=lambda: _env("TINYCLAW_LLM_PROVIDER", "mock"))
    llm_model: str = field(default_factory=lambda: _env("TINYCLAW_LLM_MODEL", "gpt-4o"))

    # Observability. When no OTLP endpoint is set, tracing is a no-op and the
    # platform still runs fully (spans simply go nowhere).
    otlp_endpoint: str | None = field(default_factory=lambda: os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))

    # Control-plane gateway. Agents report events/audit here.
    gateway_url: str = field(default_factory=lambda: _env("TINYCLAW_GATEWAY_URL", "http://127.0.0.1:9100"))

    # Shared secret used to sign/verify human-approval tokens handed to the
    # executor. Demo-grade (static); production path is documented in README.
    approval_secret: str = field(default_factory=lambda: _env("TINYCLAW_APPROVAL_SECRET", "dev-approval-secret"))

    # SQLite database used by the gateway (audit chain, approvals, tasks, agent defs).
    database_path: Path = field(default_factory=lambda: Path(_env("TINYCLAW_DB", "data/tinyclaw.sqlite")))

    # PostgreSQL (Aiven or any PG) — takes precedence over the SQLite path when
    # set to a postgres:// URI. Requires the `postgres` extra (psycopg).
    database_url: str | None = field(default_factory=lambda: os.environ.get("TINYCLAW_DATABASE_URL"))

    # Extra CORS origins for the gateway API (comma-separated) — e.g. your
    # Cloudflare Pages domain: https://tinyclaw.pages.dev
    cors_origins: list[str] = field(
        default_factory=lambda: [o.strip() for o in os.environ.get("TINYCLAW_CORS_ORIGINS", "").split(",") if o.strip()]
    )

    # Bearer token agents use to talk to the gateway internal API (demo-grade).
    internal_token: str = field(default_factory=lambda: _env("TINYCLAW_INTERNAL_TOKEN", "dev-internal-token"))


def settings(**overrides) -> Settings:
    """Build settings with optional per-process overrides (e.g. service_name)."""
    base = Settings()
    if not overrides:
        return base
    from dataclasses import replace

    return replace(base, **overrides)
