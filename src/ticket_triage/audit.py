"""审核与隐私操作审计：谁、何时、哪个 ticket_id/trace_id。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ticket_triage.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    ticket_id TEXT NOT NULL DEFAULT '',
    trace_id TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_ticket ON audit_events(ticket_id);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_events(at);
"""


def _db_path() -> Path:
    if settings.runtime_db_path:
        return Path(settings.runtime_db_path)
    return Path(__file__).resolve().parents[2] / ".data" / "runtime.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def record_audit(
    *,
    actor: str,
    action: str,
    ticket_id: str = "",
    trace_id: str = "",
    detail: dict[str, Any] | None = None,
) -> int:
    """写入一条审计。返回 rowid。"""
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = json.dumps(detail or {}, ensure_ascii=False)
    conn = _connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO audit_events (at, actor, action, ticket_id, trace_id, detail_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (at, actor, action, ticket_id or "", trace_id or "", payload),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


def list_audit(
    *,
    ticket_id: str | None = None,
    action: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 500))
    clauses: list[str] = []
    args: list[Any] = []
    if ticket_id:
        clauses.append("ticket_id = ?")
        args.append(ticket_id)
    if action:
        clauses.append("action = ?")
        args.append(action)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT id, at, actor, action, ticket_id, trace_id, detail_json
            FROM audit_events
            {where}
            ORDER BY id DESC
            LIMIT ?
            """,
            (*args, limit),
        ).fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            detail = json.loads(row[6] or "{}")
        except json.JSONDecodeError:
            detail = {}
        out.append(
            {
                "id": row[0],
                "at": row[1],
                "actor": row[2],
                "action": row[3],
                "ticket_id": row[4],
                "trace_id": row[5],
                "detail": detail,
            }
        )
    return out
