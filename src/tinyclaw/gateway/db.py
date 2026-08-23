"""SQLite persistence for the gateway control plane.

Plain stdlib sqlite (WAL) — zero-magic, fully readable. The gateway is the
single writer for tasks, approvals, the audit chain and agent definitions.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, ts REAL, type TEXT, agent TEXT, task_id TEXT,
  trace_id TEXT, data TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id);
CREATE TABLE IF NOT EXISTS audit (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT, ts REAL,
  actor TEXT, action TEXT, subject TEXT, decision TEXT,
  details TEXT, prev_hash TEXT, hash TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, ts REAL, task_id TEXT, context_id TEXT,
  orchestrator_url TEXT, scenario TEXT, subject TEXT, amount REAL,
  tier INTEGER, action TEXT, context_packet TEXT,
  status TEXT, decided_by TEXT, decided_at REAL, comment TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY, context_id TEXT, scenario TEXT, title TEXT,
  amount REAL, state TEXT, stage TEXT, current_agent TEXT, trace_id TEXT,
  requester TEXT, created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS agent_defs (
  id TEXT PRIMARY KEY, name TEXT UNIQUE, version INTEGER, status TEXT,
  definition TEXT, created_at REAL, updated_at REAL
);
"""


def _audit_body(entry: dict[str, Any]) -> dict[str, Any]:
    """Canonical, sorted field set hashed into the chain on both write & verify."""
    import time as _time

    return {
        "id": entry.get("id"),
        "ts": entry.get("ts") or _time.time(),
        "actor": entry.get("actor"),
        "action": entry.get("action"),
        "subject": entry.get("subject"),
        "decision": entry.get("decision") or "",
        "details": entry.get("details") or {},
    }


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # -- events ---------------------------------------------------------------

    def insert_event(self, ev: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO events (id, ts, type, agent, task_id, trace_id, data) VALUES (?,?,?,?,?,?,?)",
                (
                    ev["id"],
                    ev.get("ts", time.time()),
                    ev.get("type"),
                    ev.get("agent"),
                    ev.get("task_id"),
                    ev.get("trace_id"),
                    json.dumps(ev.get("data") or {}),
                ),
            )
            self._conn.commit()

    def recent_events(self, limit: int = 200, task_id: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM events" + (" WHERE task_id = ?" if task_id else "") + " ORDER BY ts DESC LIMIT ?"
        args: tuple = (task_id, limit) if task_id else (limit,)
        rows = self._conn.execute(q, args).fetchall()
        return [{**dict(r), "data": json.loads(r["data"] or "{}")} for r in rows]

    # -- audit chain (hash-chained; the gateway is the single writer) ----------

    def append_audit(self, entry: dict[str, Any]) -> dict[str, Any]:
        import hashlib

        entry = _audit_body(entry)  # canonical field set — verify() must hash the same
        with self._lock:
            row = self._conn.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            prev = row["hash"] if row else "0" * 64
            body = json.dumps(entry, sort_keys=True, default=str)
            digest = hashlib.sha256((prev + body).encode()).hexdigest()
            self._conn.execute(
                "INSERT INTO audit (id, ts, actor, action, subject, decision,"
                " details, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    entry.get("id"),
                    entry.get("ts", time.time()),
                    entry.get("actor"),
                    entry.get("action"),
                    entry.get("subject"),
                    entry.get("decision", ""),
                    json.dumps(entry.get("details") or {}),
                    prev,
                    digest,
                ),
            )
            self._conn.commit()
            return {**entry, "prev_hash": prev, "hash": digest}

    def audit_entries(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "details": json.loads(r["details"] or "{}")} for r in rows]

    def audit_verify(self) -> dict[str, Any]:
        import hashlib

        rows = self._conn.execute("SELECT * FROM audit ORDER BY seq ASC").fetchall()
        prev, bad = "0" * 64, -1
        for i, r in enumerate(rows):
            entry = _audit_body(dict(r) | {"details": json.loads(r["details"] or "{}")})
            body = json.dumps(entry, sort_keys=True, default=str)
            expect = hashlib.sha256((prev + body).encode()).hexdigest()
            if r["prev_hash"] != prev or r["hash"] != expect:
                bad = i
                break
            prev = r["hash"]
        return {"ok": bad < 0, "first_bad_seq": bad, "entries": len(rows)}

    # -- approvals --------------------------------------------------------------

    def create_approval(self, a: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO approvals (id, ts, task_id, context_id, orchestrator_url, scenario, subject,"
                " amount, tier, action, context_packet, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    a["id"],
                    a.get("ts", time.time()),
                    a["task_id"],
                    a.get("context_id"),
                    a.get("orchestrator_url"),
                    a.get("scenario"),
                    a.get("subject"),
                    a.get("amount"),
                    a.get("tier"),
                    a.get("action"),
                    json.dumps(a.get("context_packet") or {}),
                    "pending",
                ),
            )
            self._conn.commit()

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        return {**dict(row), "context_packet": json.loads(row["context_packet"] or "{}")} if row else None

    def list_approvals(self, status: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM approvals" + (" WHERE status = ?" if status else "") + " ORDER BY ts DESC LIMIT 100"
        rows = self._conn.execute(q, (status,) if status else ()).fetchall()
        return [{**dict(r), "context_packet": json.loads(r["context_packet"] or "{}")} for r in rows]

    def decide_approval(self, approval_id: str, status: str, decided_by: str, comment: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE approvals SET status = ?, decided_by = ?, decided_at = ?, comment = ? WHERE id = ?",
                (status, decided_by, time.time(), comment, approval_id),
            )
            self._conn.commit()

    # -- tasks -------------------------------------------------------------------

    def upsert_task(self, t: dict[str, Any]) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO tasks (task_id, context_id, scenario, title, amount, state, stage, current_agent,"
                " trace_id, requester, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(task_id) DO UPDATE SET state=excluded.state, stage=excluded.stage,"
                " current_agent=excluded.current_agent, title=excluded.title, amount=excluded.amount,"
                " updated_at=excluded.updated_at",
                (
                    t["task_id"],
                    t.get("context_id"),
                    t.get("scenario"),
                    t.get("title"),
                    t.get("amount"),
                    t.get("state"),
                    t.get("stage"),
                    t.get("current_agent"),
                    t.get("trace_id"),
                    t.get("requester"),
                    now,
                    now,
                ),
            )
            self._conn.commit()

    def list_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self._conn.execute("SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        ]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    # -- agent definitions (Agent Studio) ----------------------------------------

    def upsert_agent_def(self, d: dict[str, Any]) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO agent_defs (id, name, version, status, definition, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(name) DO UPDATE SET version=excluded.version, status=excluded.status,"
                " definition=excluded.definition, updated_at=excluded.updated_at",
                (
                    d["id"],
                    d["name"],
                    d.get("version", 1),
                    d.get("status", "draft"),
                    json.dumps(d.get("definition") or {}),
                    now,
                    now,
                ),
            )
            self._conn.commit()

    def list_agent_defs(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM agent_defs ORDER BY updated_at DESC").fetchall()
        return [{**dict(r), "definition": json.loads(r["definition"] or "{}")} for r in rows]

    def get_agent_def(self, name: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM agent_defs WHERE name = ?", (name,)).fetchone()
        return {**dict(row), "definition": json.loads(row["definition"] or "{}")} if row else None
