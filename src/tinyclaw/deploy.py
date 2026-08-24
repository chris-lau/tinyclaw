"""Single-container deployment entrypoint (Render & friends).

Cloud platforms give you one public port per service, but tinyclaw is a mesh
of five A2A agents plus a runtime supervisor plus the gateway. This module
runs the whole mesh inside one container:

    ┌──────────────── one Render web service ────────────────┐
    │ gateway (uvicorn, 0.0.0.0:$PORT)  ← the only public face │
    │ intake :9101 · research :9102 · policy :9103 (loopback) │
    │ executor :9104 · orchestrator :9105 · runtime :9111     │
    └─────────────────────────────────────────────────────────┘

Agents talk to each other over container-loopback; the gateway proxies
nothing — it just shares the process namespace. State goes to Postgres when
TINYCLAW_DATABASE_URL is set (Aiven), else SQLite on local disk.

    uv run python -m tinyclaw.deploy          # PORT env respected (default 9100)
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys

SCENARIOS: dict[str, list[str]] = {
    "procurement": [
        "tinyclaw.scenarios.procurement.agents.intake",
        "tinyclaw.scenarios.procurement.agents.research",
        "tinyclaw.scenarios.procurement.agents.policy_agent",
        "tinyclaw.scenarios.procurement.agents.executor",
        "tinyclaw.scenarios.procurement.agents.orchestrator",
    ],
    "support": [
        "tinyclaw.scenarios.support.agents.intake",
        "tinyclaw.scenarios.support.agents.research",
        "tinyclaw.scenarios.support.agents.policy_agent",
        "tinyclaw.scenarios.support.agents.executor",
        "tinyclaw.scenarios.support.agents.orchestrator",
    ],
}
MESH_SERVICES = ["tinyclaw.runtime"]


def main() -> None:
    port = int(os.environ.get("PORT", "9100"))
    # Scenario selection: each mesh is ~5 processes; on memory-constrained
    # plans (Render free = 512MB) run a subset, e.g. TINYCLAW_SCENARIOS=procurement.
    selected = [s.strip() for s in os.environ.get("TINYCLAW_SCENARIOS", ",".join(SCENARIOS)).split(",") if s.strip()]
    unknown = [s for s in selected if s not in SCENARIOS]
    if unknown:
        raise SystemExit(f"unknown scenario(s) {unknown}; available: {list(SCENARIOS)}")
    modules = [m for s in selected for m in SCENARIOS[s]] + MESH_SERVICES

    # Agents reach the gateway on container-loopback, whatever the public host is.
    child_env = {
        **os.environ,
        "TINYCLAW_GATEWAY_URL": f"http://127.0.0.1:{port}",
    }

    procs = [subprocess.Popen([sys.executable, "-m", mod], env=child_env) for mod in modules]
    print(f"[deploy] scenarios={selected} · started {len(procs)} processes", flush=True)

    # SIGTERM must tear children down (platforms send it on redeploys).
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    from .core.config import Settings
    from .gateway.app import create_app

    app = create_app(Settings(service_name="tinyclaw-gateway"))

    import uvicorn

    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    try:
        server.run()
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        print("[deploy] agents stopped", flush=True)


if __name__ == "__main__":
    main()
