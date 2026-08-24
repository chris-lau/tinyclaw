"""Persistence for the gateway control plane.

Two backends behind one small abstraction:

* **SQLite** (default, stdlib) — local dev, zero config, WAL mode.
* **PostgreSQL** (Aiven or any PG) — set ``TINYCLAW_DATABASE_URL`` to a
  ``postgres://``/``postgresql://`` URI; requires the ``postgres`` extra
  (``psycopg``). Used for cloud deploys where the gateway restarts and state
  must survive.

SQL is kept in the portable subset; only placeholders (``?`` vs ``%s``) and
minor DDL differ between dialects.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_SCHEMA_SQLITE = """
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
CREATE TABLE IF NOT EXISTS kv (
  key TEXT PRIMARY KEY, value TEXT
);
"""

_SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, ts DOUBLE PRECISION, type TEXT, agent TEXT, task_id TEXT,
  trace_id TEXT, data TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id);
CREATE TABLE IF NOT EXISTS audit (
  seq BIGSERIAL PRIMARY KEY, id TEXT, ts DOUBLE PRECISION,
  actor TEXT, action TEXT, subject TEXT, decision TEXT,
  details TEXT, prev_hash TEXT, hash TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, ts DOUBLE PRECISION, task_id TEXT, context_id TEXT,
  orchestrator_url TEXT, scenario TEXT, subject TEXT, amount DOUBLE PRECISION,
  tier INTEGER, action TEXT, context_packet TEXT,
  status TEXT, decided_by TEXT, decided_at DOUBLE PRECISION, comment TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY, context_id TEXT, scenario TEXT, title TEXT,
  amount DOUBLE PRECISION, state TEXT, stage TEXT, current_agent TEXT, trace_id TEXT,
  requester TEXT, created_at DOUBLE PRECISION, updated_at DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS agent_defs (
  id TEXT PRIMARY KEY, name TEXT UNIQUE, version INTEGER, status TEXT,
  definition TEXT, created_at DOUBLE PRECISION, updated_at DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS kv (
  key TEXT PRIMARY KEY, value TEXT
);
"""


def _audit_body(entry: dict[str, Any]) -> dict[str, Any]:
    """Canonical, sorted field set hashed into the chain on both write & verify."""
    return {
        "id": entry.get("id"),
        "ts": entry.get("ts") or time.time(),
        "actor": entry.get("actor"),
        "action": entry.get("action"),
        "subject": entry.get("subject"),
        "decision": entry.get("decision") or "",
        "details": entry.get("details") or {},
    }


class Database:
    """Single-writer store for events, audit chain, approvals, tasks, defs, kv."""

    def __init__(self, path: Path | None = None, url: str | None = None) -> None:
        self._lock = threading.Lock()
        self._pg = False
        if url and url.startswith(("postgres://", "postgresql://")):
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as e:  # pragma: no cover
                raise RuntimeError(
                    "TINYCLAW_DATABASE_URL points at Postgres but psycopg is not "
                    "installed — run `uv sync --extra postgres`"
                ) from e
            self._conn: Any = psycopg.connect(url, row_factory=dict_row, autocommit=False)
            self._pg = True
            with self._lock:
                self._conn.execute(_SCHEMA_PG)
                self._conn.commit()
        else:
            path = path or Path("data/tinyclaw.sqlite")
            path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            with self._lock:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.executescript(_SCHEMA_SQLITE)
                self._conn.commit()

    # -- backend helpers --------------------------------------------------------

    def _q(self, sql: str) -> str:
        """Translate sqlite-style placeholders for the PG backend."""
        return sql.replace("?", "%s") if self._pg else sql

    def _exec(self, sql: str, args: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(self._q(sql), args)
            self._conn.commit()

    def _one(self, sql: str, args: tuple = ()) -> dict[str, Any] | None:
        row = self._conn.execute(self._q(sql), args).fetchone()
        if row is None:
            return None
        return dict(row) if not isinstance(row, dict) else row

    def _all(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        rows = self._conn.execute(self._q(sql), args).fetchall()
        return [dict(r) if not isinstance(r, dict) else r for r in rows]

    # -- events ---------------------------------------------------------------

    def insert_event(self, ev: dict[str, Any]) -> None:
        self._exec(
            "INSERT INTO events (id, ts, type, agent, task_id, trace_id, data) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET ts=excluded.ts, type=excluded.type, agent=excluded.agent,"
            " task_id=excluded.task_id, trace_id=excluded.trace_id, data=excluded.data",
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

    def recent_events(self, limit: int = 200, task_id: str | None = None) -> list[dict[str, Any]]:
        if task_id:
            rows = self._all("SELECT * FROM events WHERE task_id = ? ORDER BY ts DESC LIMIT ?", (task_id, limit))
        else:
            rows = self._all("SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,))
        return [{**r, "data": json.loads(r["data"] or "{}")} for r in rows]

    # -- audit chain (hash-chained; the gateway is the single writer) ----------

    def append_audit(self, entry: dict[str, Any]) -> dict[str, Any]:
        import hashlib

        entry = _audit_body(entry)  # canonical field set — verify() must hash the same
        with self._lock:
            row = self._one("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1")
            prev = row["hash"] if row else "0" * 64
            body = json.dumps(entry, sort_keys=True, default=str)
            digest = hashlib.sha256((prev + body).encode()).hexdigest()
            self._conn.execute(
                self._q(
                    "INSERT INTO audit (id, ts, actor, action, subject, decision, details, prev_hash, hash)"
                    " VALUES (?,?,?,?,?,?,?,?,?)"
                ),
                (
                    entry.get("id"),
                    entry.get("ts"),
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
        rows = self._all("SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,))
        return [{**r, "details": json.loads(r["details"] or "{}")} for r in rows]

    def audit_verify(self) -> dict[str, Any]:
        import hashlib

        rows = self._all("SELECT * FROM audit ORDER BY seq ASC")
        prev, bad = "0" * 64, -1
        for i, r in enumerate(rows):
            entry = _audit_body({**r, "details": json.loads(r["details"] or "{}")})
            body = json.dumps(entry, sort_keys=True, default=str)
            expect = hashlib.sha256((prev + body).encode()).hexdigest()
            if r["prev_hash"] != prev or r["hash"] != expect:
                bad = i
                break
            prev = r["hash"]
        return {"ok": bad < 0, "first_bad_seq": bad, "entries": len(rows)}

    # -- approvals --------------------------------------------------------------

    def create_approval(self, a: dict[str, Any]) -> None:
        self._exec(
            "INSERT INTO approvals (id, ts, task_id, context_id, orchestrator_url, scenario, subject,"
            " amount, tier, action, context_packet, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET ts=excluded.ts, task_id=excluded.task_id,"
            " context_id=excluded.context_id, orchestrator_url=excluded.orchestrator_url,"
            " scenario=excluded.scenario, subject=excluded.subject, amount=excluded.amount,"
            " tier=excluded.tier, action=excluded.action, context_packet=excluded.context_packet,"
            " status=excluded.status",
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

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        return {**row, "context_packet": json.loads(row["context_packet"] or "{}")} if row else None

    def list_approvals(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self._all("SELECT * FROM approvals WHERE status = ? ORDER BY ts DESC LIMIT 100", (status,))
        else:
            rows = self._all("SELECT * FROM approvals ORDER BY ts DESC LIMIT 100")
        return [{**r, "context_packet": json.loads(r["context_packet"] or "{}")} for r in rows]

    def decide_approval(self, approval_id: str, status: str, decided_by: str, comment: str) -> None:
        self._exec(
            "UPDATE approvals SET status = ?, decided_by = ?, decided_at = ?, comment = ? WHERE id = ?",
            (status, decided_by, time.time(), comment, approval_id),
        )

    # -- tasks -------------------------------------------------------------------

    def upsert_task(self, t: dict[str, Any]) -> None:
        now = time.time()
        self._exec(
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

    def list_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?", (limit,))

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM tasks WHERE task_id = ?", (task_id,))

    # -- agent definitions (Agent Studio) ----------------------------------------

    def upsert_agent_def(self, d: dict[str, Any]) -> None:
        now = time.time()
        self._exec(
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

    def list_agent_defs(self) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM agent_defs ORDER BY updated_at DESC")
        return [{**r, "definition": json.loads(r["definition"] or "{}")} for r in rows]

    def get_agent_def(self, name: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM agent_defs WHERE name = ?", (name,))
        return {**row, "definition": json.loads(row["definition"] or "{}")} if row else None

    def delete_agent_def(self, name: str) -> bool:
        """Hard delete of the definition row. True if a row was removed."""
        with self._lock:
            cur = self._conn.execute(self._q("DELETE FROM agent_defs WHERE name = ?"), (name,))
            self._conn.commit()
        return bool(cur.rowcount)

    # -- key/value (posture + other runtime settings) --------------------------

    def kv_get(self, key: str, default: str | None = None) -> str | None:
        row = self._one("SELECT value FROM kv WHERE key = ?", (key,))
        return row["value"] if row else default

    def kv_set(self, key: str, value: str) -> None:
        self._exec(
            "INSERT INTO kv (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
