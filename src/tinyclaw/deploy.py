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
    selected = _selected_scenarios()

    # Single-process mesh (default): every agent app runs as a thread with its
    # own event loop inside this one interpreter — one shared copy of the
    # heavy imports (a2a/grpc/starlette) instead of one per agent. This is
    # what lets BOTH scenario meshes fit in a 512MB free-tier container.
    # Set TINYCLAW_SINGLE_PROCESS=0 for one-process-per-agent (subprocess)
    # mode, which isolates crashes but multiplies memory.
    if os.environ.get("TINYCLAW_SINGLE_PROCESS", "1") != "0":
        _run_single_process(port, selected)
        return
    _run_subprocesses(port, selected)


def _selected_scenarios() -> list[str]:
    selected = [s.strip() for s in os.environ.get("TINYCLAW_SCENARIOS", ",".join(SCENARIOS)).split(",") if s.strip()]
    unknown = [s for s in selected if s not in SCENARIOS]
    if unknown:
        raise SystemExit(f"unknown scenario(s) {unknown}; available: {list(SCENARIOS)}")
    return selected


def _run_subprocesses(port: int, selected: list[str]) -> None:
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


def _run_single_process(port: int, selected: list[str]) -> None:
    import threading

    import uvicorn

    from .core.config import Settings
    from .core.observability import tracing
    from .gateway.app import create_app
    from .runtime.supervisor import app as runtime_app
    from .scenarios.procurement.agents import executor as procurement_executor
    from .scenarios.procurement.agents import intake as procurement_intake
    from .scenarios.procurement.agents import orchestrator as procurement_orch
    from .scenarios.procurement.agents import policy_agent as procurement_policy
    from .scenarios.procurement.agents import research as procurement_research
    from .scenarios.support.agents import executor as support_executor
    from .scenarios.support.agents import intake as support_intake
    from .scenarios.support.agents import orchestrator as support_orch
    from .scenarios.support.agents import policy_agent as support_policy
    from .scenarios.support.agents import research as support_research

    # FORCE, not setdefault: the Dockerfile bakes TINYCLAW_GATEWAY_URL=:9100,
    # but platforms assign their own $PORT — deferring to a stale baked value
    # silently blackholes every agent→gateway report (events/audit/permits).
    os.environ["TINYCLAW_GATEWAY_URL"] = f"http://127.0.0.1:{port}"
    tracing.setup_tracing("tinyclaw-mesh", os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))

    factories = {
        "procurement": [
            procurement_intake.IntakeExecutor,
            procurement_research.ResearchExecutor,
            procurement_policy.PolicyExecutor,
            procurement_executor.ExecutorExecutor,
            procurement_orch.OrchestratorExecutor,
        ],
        "support": [
            support_intake.SupportIntakeExecutor,
            support_research.SupportResearchExecutor,
            support_policy.SupportPolicyExecutor,
            support_executor.SupportExecutorExecutor,
            support_orch.SupportOrchestratorExecutor,
        ],
    }

    from .core.agent import build_agent_app
    from .core.persistence import durable_task_store

    threads: list[threading.Thread] = []

    def _serve(app, bind_port: int) -> None:
        config = uvicorn.Config(app, host="127.0.0.1", port=bind_port, log_level="warning")
        uvicorn.Server(config).run()  # own event loop per thread; signals main-thread only

    for scen in selected:
        for executor_cls in factories[scen]:
            executor = executor_cls(Settings())
            app = build_agent_app(executor.spec, executor, task_store=durable_task_store(executor.spec.name)).build()
            t = threading.Thread(
                target=_serve, args=(app, _port_of(executor.spec.url)), daemon=True, name=f"agent:{executor.spec.name}"
            )
            t.start()
            threads.append(t)

    # Agent Studio runtime supervisor on its usual port.
    threading.Thread(target=_serve, args=(runtime_app, 9111), daemon=True, name="runtime").start()

    print(f"[deploy] single-process mesh · scenarios={selected} · {len(threads) + 1} agent apps + gateway", flush=True)

    # Gateway is the only public face and the main thread — its uvicorn owns
    # signal handling; when it exits, daemon agent threads end with the process.
    gateway = create_app(Settings(service_name="tinyclaw-gateway"))
    uvicorn.run(gateway, host="0.0.0.0", port=port, log_level="info")


def _port_of(url: str) -> int:
    return int(url.rsplit(":", 1)[-1].split("/")[0])


if __name__ == "__main__":
    main()
