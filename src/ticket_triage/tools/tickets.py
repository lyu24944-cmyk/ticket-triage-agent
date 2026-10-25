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
    conn.execute("PRAGMA busy_timeout=2000")
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


def _row_to_ticket(row: tuple) -> dict[str, Any]:
    return {
        "ticket_id": row[0],
        "status": row[1],
        "subject": row[2] or "",
        "body": row[3] or "",
        "history": list(json.loads(row[4])),
    }


def get_ticket(ticket_id: str) -> dict[str, Any] | None:
    """读取一张工单及其全部状态历史。"""
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                """
                SELECT ticket_id, status, subject, body, history_json
                FROM tickets WHERE ticket_id = ?
                """,
                (ticket_id,),
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    return _row_to_ticket(row)


def list_tickets(statuses: list[str] | None = None) -> list[dict[str, Any]]:
    """列出工单。传入 statuses 时只返回这些状态。"""
    with _LOCK:
        conn = _connect()
        try:
            if statuses:
                marks = ",".join("?" for _ in statuses)
                rows = conn.execute(
                    f"""
                    SELECT ticket_id, status, subject, body, history_json
                    FROM tickets WHERE status IN ({marks})
                    ORDER BY rowid DESC
                    """,
                    tuple(statuses),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT ticket_id, status, subject, body, history_json
                    FROM tickets ORDER BY rowid DESC
                    """
                ).fetchall()
        finally:
            conn.close()
    return [_row_to_ticket(row) for row in rows]


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


def probe_writable() -> None:
    """确认工单库可以写入。失败时抛出异常。"""
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS _health (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO _health (id) VALUES (1) ON CONFLICT(id) DO NOTHING")
            conn.commit()
        finally:
            conn.close()


def update_ticket_status(
    ticket_id: str,
    status: str,
    note: str | None = None,
    reviewer: str | None = None,
) -> dict[str, Any]:
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
            entry: dict[str, Any] = {"status": status, "note": note}
            if reviewer:
                entry["reviewer"] = reviewer
            history.append(entry)
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
    """第一次入库写 open。已有工单保持现状，不改状态、不覆盖历史。"""
    with _LOCK:
        conn = _connect()
        try:
            history = [{"status": "open", "note": f"已入库: {subject}"}]
            conn.execute(
                """
                INSERT OR IGNORE INTO tickets (ticket_id, status, subject, body, history_json)
                VALUES (?, 'open', ?, ?, ?)
                """,
                (ticket_id, subject, body, json.dumps(history, ensure_ascii=False)),
            )
            conn.commit()
        finally:
            conn.close()


def delete_ticket(ticket_id: str) -> None:
    """删掉一张工单。演示脚本重跑固定编号时使用。"""
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("DELETE FROM tickets WHERE ticket_id = ?", (ticket_id,))
            conn.commit()
        finally:
            conn.close()


def reset_store_for_tests() -> None:
    """测试用：清空数据库文件。"""
    path = _db_path()
    with _LOCK:
        if path.exists():
            path.unlink()
