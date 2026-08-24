"""Run the support scenario agents: ports 9201-9205.

uv run python -m tinyclaw.scenarios.support
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time

AGENTS = [
    "tinyclaw.scenarios.support.agents.intake",
    "tinyclaw.scenarios.support.agents.research",
    "tinyclaw.scenarios.support.agents.policy_agent",
    "tinyclaw.scenarios.support.agents.executor",
    "tinyclaw.scenarios.support.agents.orchestrator",
]


def main() -> None:
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    procs = [subprocess.Popen([sys.executable, "-m", mod]) for mod in AGENTS]
    print(f"started {len(procs)} support agents (ports 9201-9205); ctrl-c to stop", flush=True)
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
