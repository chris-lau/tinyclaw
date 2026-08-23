"""Runtime supervisor: hosts Agent Studio deployments.

    uv run python -m tinyclaw.runtime

FastAPI service on :9111. Deploying a definition makes the supervisor spawn
a real agent process (port per agent, Agent Card at /.well-known/…), turning
"deploy" into a concrete, observable action — gated by the same approval
queue as everything else when the definition is high-risk.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException

from ..core.config import Settings

PORT_BASE = 9111  # supervisor itself
AGENT_PORT_BASE = 9120

app = FastAPI(title="tinyclaw runtime")
_settings = Settings()
_hosted: dict[str, dict] = {}  # name -> {proc, port, definition}


@app.get("/hosted")
async def hosted() -> list[dict]:
    return [{"name": n, "port": h["port"], "alive": h["proc"].poll() is None} for n, h in _hosted.items()]


@app.post("/host")
async def host(body: dict) -> dict:
    definition = body["definition"]
    name = definition["name"]
    if name in _hosted and _hosted[name]["proc"].poll() is None:
        raise HTTPException(409, f"agent {name} already hosted")

    port = AGENT_PORT_BASE + len(_hosted) + 1
    def_path = Path(tempfile.gettempdir()) / f"tinyclaw-def-{name}-{int(time.time())}.json"
    def_path.write_text(json.dumps(definition))
    proc = subprocess.Popen(
        [sys.executable, "-m", "tinyclaw.runtime.agent", "--definition", str(def_path), "--port", str(port)]
    )
    _hosted[name] = {"proc": proc, "port": port, "definition": definition}
    return {"name": name, "port": port, "url": f"http://127.0.0.1:{port}"}


@app.delete("/hosted/{name}")
async def stop(name: str) -> dict:
    entry = _hosted.pop(name, None)
    if not entry:
        raise HTTPException(404, "not hosted")
    entry["proc"].terminate()
    return {"stopped": name}


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=PORT_BASE, log_level="warning")


if __name__ == "__main__":
    main()
