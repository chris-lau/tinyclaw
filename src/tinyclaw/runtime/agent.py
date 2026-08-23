"""Entry point for one hosted declarative agent (spawned by the supervisor)."""

from __future__ import annotations

import argparse
import json

from ..core.agent import serve
from ..core.config import Settings
from .declarative import DeclarativeExecutor


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--definition", required=True, help="path to agent definition JSON")
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()

    with open(args.definition) as f:
        definition = json.load(f)
    settings = Settings(service_name=f"studio-{definition['name']}")
    executor = DeclarativeExecutor(definition, settings, args.port)
    serve(executor.spec, executor)


if __name__ == "__main__":
    main()
