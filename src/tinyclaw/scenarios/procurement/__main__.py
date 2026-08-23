"""Run the whole procurement scenario locally: five A2A agent processes.

    uv run python -m tinyclaw.scenarios.procurement

Starts intake/research/policy/executor/orchestrator on ports 9101–9105.
Run the gateway separately (`uv run python -m tinyclaw.gateway`).
"""

from __future__ import annotations

import subprocess
import sys
import time

AGENTS = [
    "tinyclaw.scenarios.procurement.agents.intake",
    "tinyclaw.scenarios.procurement.agents.research",
    "tinyclaw.scenarios.procurement.agents.policy_agent",
    "tinyclaw.scenarios.procurement.agents.executor",
    "tinyclaw.scenarios.procurement.agents.orchestrator",
]


def main() -> None:
    procs = [subprocess.Popen([sys.executable, "-m", mod]) for mod in AGENTS]
    print(f"started {len(procs)} agents (ports 9101-9105); ctrl-c to stop", flush=True)
    try:
        while True:
            for p in procs:
                if p.poll() is not None:
                    raise SystemExit(f"agent exited with {p.returncode}")
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(timeout=10)


if __name__ == "__main__":
    main()
