"""工单库（SQLite 文件）— Agent 与 MCP 子进程共享同一数据文件。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ticket_triage.tenants import DEFAULT_TENANT_ID

_LOCK = threading.Lock()
_DEFAULT_DB = Path(__file__).resolve().parents[3] / ".data" / "tickets.db"


def _db_path() -> Path:
    from ticket_triage.config import settings

    raw = settings.ticket_db_path
    path = Path(raw) if raw else _DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _migrate(conn: sqlite3.Connection) -> None:
    """补 tenant_id，并在需要时重建为 (tenant_id, ticket_id) 复合主键。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tickets (
            tenant_id TEXT NOT NULL DEFAULT 'default',
            ticket_id TEXT NOT NULL,
            status TEXT NOT NULL,
            subject TEXT,
            body TEXT,
            history_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (tenant_id, ticket_id)
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tickets)")}
    # 旧库：ticket_id 单列主键，无 tenant_id
    if "tenant_id" not in columns:
        conn.execute(
            "ALTER TABLE tickets ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
        )
        columns.add("tenant_id")
    if "updated_at" not in columns:
        conn.execute("ALTER TABLE tickets ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
    conn.execute(
        "UPDATE tickets SET updated_at = ? WHERE updated_at = '' OR updated_at IS NULL",
        (_now(),),
    )
    # 若仍是旧主键（仅 ticket_id），重建为复合主键
    pk_cols = [
        row[1]
        for row in conn.execute("PRAGMA table_info(tickets)")
        if row[5]  # pk ordinal
    ]
    if pk_cols == ["ticket_id"] or (len(pk_cols) == 1 and pk_cols[0] == "ticket_id"):
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tickets_v2 (
                tenant_id TEXT NOT NULL DEFAULT 'default',
                ticket_id TEXT NOT NULL,
                status TEXT NOT NULL,
                subject TEXT,
                body TEXT,
                history_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (tenant_id, ticket_id)
            )
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO tickets_v2
                (tenant_id, ticket_id, status, subject, body, history_json, updated_at)
            SELECT COALESCE(NULLIF(tenant_id, ''), 'default'), ticket_id, status,
                   subject, body, history_json, updated_at
            FROM tickets
            """
        )
        conn.execute("DROP TABLE tickets")
        conn.execute("ALTER TABLE tickets_v2 RENAME TO tickets")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tickets_status_updated "
        "ON tickets(tenant_id, status, updated_at DESC, ticket_id DESC)"
    )


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), check_same_thread=False)
    conn.execute("PRAGMA busy_timeout=2000")
    conn.execute("PRAGMA journal_mode=WAL")
    _migrate(conn)
    conn.commit()
    return conn


def _row_to_ticket(row: tuple) -> dict[str, Any]:
    from ticket_triage.data_crypto import open_text

    # (tenant_id, ticket_id, status, subject, body, history_json, updated_at)
    return {
        "tenant_id": row[0],
        "ticket_id": row[1],
        "status": row[2],
        "subject": open_text(row[3] or "") or "",
        "body": open_text(row[4] or "") or "",
        "history": list(json.loads(row[5])),
        "updated_at": row[6] or "",
    }


def _select_cols() -> str:
    return "tenant_id, ticket_id, status, subject, body, history_json, updated_at"


def get_ticket(
    ticket_id: str,
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, Any] | None:
    """读取一张工单及其全部状态历史。"""
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                f"""
                SELECT {_select_cols()}
                FROM tickets WHERE tenant_id = ? AND ticket_id = ?
                """,
                (tenant_id, ticket_id),
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    return _row_to_ticket(row)


def list_tickets(
    statuses: list[str] | None = None,
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
    q: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> list[dict[str, Any]]:
    """列出工单。可按状态、关键词、游标分页过滤。"""
    params: list[Any] = [tenant_id]
    where = ["tenant_id = ?"]
    if statuses:
        marks = ",".join("?" for _ in statuses)
        where.append(f"status IN ({marks})")
        params.extend(statuses)
    if q:
        like = f"%{q}%"
        where.append("(ticket_id LIKE ? OR subject LIKE ? OR body LIKE ?)")
        params.extend([like, like, like])
    if cursor:
        # cursor = updated_at|ticket_id（DESC）
        parts = cursor.split("|", 1)
        if len(parts) == 2:
            where.append("(updated_at < ? OR (updated_at = ? AND ticket_id < ?))")
            params.extend([parts[0], parts[0], parts[1]])
    sql = (
        f"SELECT {_select_cols()} FROM tickets WHERE {' AND '.join(where)} "
        "ORDER BY updated_at DESC, ticket_id DESC"
    )
    if limit is not None:
        sql += " LIMIT ?"
        params.append(max(1, min(int(limit), 200)))
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
        finally:
            conn.close()
    return [_row_to_ticket(row) for row in rows]


def get_ticket_history(
    ticket_id: str,
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> list[dict[str, Any]]:
    """返回工单状态历史。"""
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT history_json FROM tickets WHERE tenant_id = ? AND ticket_id = ?",
                (tenant_id, ticket_id),
            ).fetchone()
            if not row:
                return []
            return list(json.loads(row[0]))
        finally:
            conn.close()


def checkpoint_store() -> None:
    """把 WAL 收进主文件。停进程后备份才不会漏掉最后一次写入。"""
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
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
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, Any]:
    """更新工单状态并追加历史。"""
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT history_json FROM tickets WHERE tenant_id = ? AND ticket_id = ?",
                (tenant_id, ticket_id),
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
            now = _now()
            conn.execute(
                """
                INSERT INTO tickets
                    (tenant_id, ticket_id, status, subject, body, history_json, updated_at)
                VALUES (
                    ?, ?, ?,
                    COALESCE(
                        (SELECT subject FROM tickets WHERE tenant_id = ? AND ticket_id = ?),
                        ''
                    ),
                    COALESCE(
                        (SELECT body FROM tickets WHERE tenant_id = ? AND ticket_id = ?),
                        ''
                    ),
                    ?, ?
                )
                ON CONFLICT(tenant_id, ticket_id) DO UPDATE SET
                    status = excluded.status,
                    history_json = excluded.history_json,
                    updated_at = excluded.updated_at
                """,
                (
                    tenant_id,
                    ticket_id,
                    status,
                    tenant_id,
                    ticket_id,
                    tenant_id,
                    ticket_id,
                    json.dumps(history, ensure_ascii=False),
                    now,
                ),
            )
            conn.commit()
            return {"ticket_id": ticket_id, "status": status, "tenant_id": tenant_id}
        finally:
            conn.close()


def seed_ticket(
    ticket_id: str,
    subject: str,
    body: str,
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> None:
    """第一次入库写 open。已有工单保持现状，不改状态、不覆盖历史。"""
    from ticket_triage.data_crypto import seal_text

    sealed_subject = seal_text(subject) or ""
    sealed_body = seal_text(body) or ""
    with _LOCK:
        conn = _connect()
        try:
            history = [{"status": "open", "note": f"已入库: {subject}"}]
            conn.execute(
                """
                INSERT OR IGNORE INTO tickets
                    (tenant_id, ticket_id, status, subject, body, history_json, updated_at)
                VALUES (?, ?, 'open', ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    ticket_id,
                    sealed_subject,
                    sealed_body,
                    json.dumps(history, ensure_ascii=False),
                    _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()


def delete_ticket(
    ticket_id: str,
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> None:
    """删掉一张工单。演示脚本重跑固定编号时使用。"""
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "DELETE FROM tickets WHERE tenant_id = ? AND ticket_id = ?",
                (tenant_id, ticket_id),
            )
            conn.commit()
        finally:
            conn.close()


def list_expired_ticket_ids(
    *,
    retention_days: int | None = None,
    now: datetime | None = None,
) -> list[tuple[str, str]]:
    """updated_at 早于保留期限的 (tenant_id, ticket_id)。"""
    from ticket_triage.config import settings

    days = settings.ticket_retention_days if retention_days is None else int(retention_days)
    if days < 1:
        return []
    moment = now or datetime.now(timezone.utc)
    cutoff = (moment - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT tenant_id, ticket_id FROM tickets
                WHERE updated_at != '' AND updated_at < ?
                """,
                (cutoff,),
            ).fetchall()
        finally:
            conn.close()
    return [(str(row[0]), str(row[1])) for row in rows]


def purge_expired_tickets(
    *,
    retention_days: int | None = None,
    now: datetime | None = None,
) -> list[str]:
    """删掉过期工单正文所在行，并清掉 runtime 里同 ticket_id 的运行留存。返回删掉的 id。"""
    expired = list_expired_ticket_ids(retention_days=retention_days, now=now)
    if not expired:
        return []
    from ticket_triage.runtime import purge_ticket_runtime

    deleted: list[str] = []
    for tenant_id, ticket_id in expired:
        delete_ticket(ticket_id, tenant_id=tenant_id)
        purge_ticket_runtime(ticket_id, tenant_id=tenant_id)
        deleted.append(ticket_id)
    return deleted


def reset_store_for_tests() -> None:
    """测试用：清空数据库文件。"""
    path = _db_path()
    with _LOCK:
        if path.exists():
            path.unlink()
