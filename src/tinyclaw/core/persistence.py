"""Durable A2A task storage (Phase 2).

Implements the SDK's ``TaskStore`` protocol (``save`` / ``get`` / ``delete``)
on SQLite so a parked ``input_required`` task survives an agent restart:
kill the orchestrator mid-approval, restart it, and the human decision still
resumes the original task.

Enabled by default; opt out with ``TINYCLAW_DURABLE_TASKS=0`` (memory store).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from a2a.server.tasks import InMemoryTaskStore, TaskStore
from a2a.types import Task

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY,
  context_id TEXT,
  task_json TEXT NOT NULL,
  updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_tasks_context ON tasks(context_id);
"""


class SqliteTaskStore(TaskStore):
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    async def save(self, task: Task, context=None) -> None:
        import time

        with self._lock:
            self._conn.execute(
                "INSERT INTO tasks (task_id, context_id, task_json, updated_at) VALUES (?,?,?,?)"
                " ON CONFLICT(task_id) DO UPDATE SET task_json=excluded.task_json,"
                " updated_at=excluded.updated_at",
                (task.id, task.context_id, task.model_dump_json(exclude_none=True), time.time()),
            )
            self._conn.commit()

    async def get(self, task_id: str, context=None) -> Task | None:
        row = self._conn.execute("SELECT task_json FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            return None
        return Task.model_validate_json(row["task_json"])

    async def delete(self, task_id: str, context=None) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
            self._conn.commit()

    def task_ids(self) -> list[str]:
        return [r["task_id"] for r in self._conn.execute("SELECT task_id FROM tasks")]


def durable_task_store(agent_name: str, base_dir: Path | str = "data/tasks") -> TaskStore:
    """SQLite-backed store per agent, or in-memory if durability is disabled."""
    import os

    if os.environ.get("TINYCLAW_DURABLE_TASKS", "1") == "0":
        return InMemoryTaskStore()
    try:
        return SqliteTaskStore(Path(base_dir) / f"{agent_name}.sqlite")
    except Exception:
        return InMemoryTaskStore()
