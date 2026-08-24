"""CLI: run the runtime supervisor on :9111.

uv run python -m tinyclaw.runtime
"""

from __future__ import annotations

import uvicorn

from .supervisor import PORT_BASE, app


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=PORT_BASE, log_level="warning")


if __name__ == "__main__":
    main()
