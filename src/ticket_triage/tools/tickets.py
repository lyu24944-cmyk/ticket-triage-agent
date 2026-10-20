"""工单库（SQLite 文件）— Agent 与 MCP 子进程共享同一数据文件。"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_DEFAULT_DB = Path(__file__).resolve().parents[3] / ".data" / "tickets.db"


def _db_path() -> Path:
    from ticket_triage.config import settings

    raw = settings.ticket_db_path
    path = Path(raw) if raw else _DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            subject TEXT,
            body TEXT,
            history_json TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def get_ticket_history(ticket_id: str) -> list[dict[str, Any]]:
    """返回工单状态历史。"""
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT history_json FROM tickets WHERE ticket_id = ?",
                (ticket_id,),
            ).fetchone()
            if not row:
                return []
            return list(json.loads(row[0]))
        finally:
            conn.close()


def update_ticket_status(ticket_id: str, status: str, note: str | None = None) -> dict[str, Any]:
    """更新工单状态并追加历史。"""
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT history_json FROM tickets WHERE ticket_id = ?",
                (ticket_id,),
            ).fetchone()
            history: list[dict[str, Any]]
            if row:
                history = list(json.loads(row[0]))
            else:
                history = []
            history.append({"status": status, "note": note})
            conn.execute(
                """
                INSERT INTO tickets (ticket_id, status, subject, body, history_json)
                VALUES (?, ?, COALESCE((SELECT subject FROM tickets WHERE ticket_id = ?), ''),
                        COALESCE((SELECT body FROM tickets WHERE ticket_id = ?), ''), ?)
                ON CONFLICT(ticket_id) DO UPDATE SET
                    status = excluded.status,
                    history_json = excluded.history_json
                """,
                (ticket_id, status, ticket_id, ticket_id, json.dumps(history, ensure_ascii=False)),
            )
            conn.commit()
            return {"ticket_id": ticket_id, "status": status}
        finally:
            conn.close()


def seed_ticket(ticket_id: str, subject: str, body: str) -> None:
    """初始化一条工单记录。"""
    with _LOCK:
        conn = _connect()
        try:
            history = [{"status": "open", "note": f"已入库: {subject}"}]
            conn.execute(
                """
                INSERT INTO tickets (ticket_id, status, subject, body, history_json)
                VALUES (?, 'open', ?, ?, ?)
                ON CONFLICT(ticket_id) DO UPDATE SET
                    subject = excluded.subject,
                    body = excluded.body,
                    status = 'open',
                    history_json = excluded.history_json
                """,
                (ticket_id, subject, body, json.dumps(history, ensure_ascii=False)),
            )
            conn.commit()
        finally:
            conn.close()


def reset_store_for_tests() -> None:
    """测试用：清空数据库文件。"""
    path = _db_path()
    with _LOCK:
        if path.exists():
            path.unlink()
