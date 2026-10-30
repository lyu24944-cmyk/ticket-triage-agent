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
    # Everything 风格：本地倒排/trigram 索引，检索走索引而非全表 LIKE。
    # 静态加密开启时不建可用明文索引（密文无法子串检索）。
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS tickets_fts USING fts5(
            tenant_id UNINDEXED,
            ticket_id UNINDEXED,
            subject,
            body,
            tokenize='trigram'
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(
            article_id UNINDEXED,
            title,
            body,
            keywords,
            tokenize='trigram'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kb_fts_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


def _fts_quote(term: str) -> str:
    """FTS5 短语查询：双引号包裹并转义内部引号。"""
    return '"' + term.replace('"', '""') + '"'


def _sync_ticket_fts(
    conn: sqlite3.Connection,
    tenant_id: str,
    ticket_id: str,
    subject: str,
    body: str,
) -> None:
    from ticket_triage.data_crypto import encryption_enabled

    if encryption_enabled():
        return
    conn.execute(
        "DELETE FROM tickets_fts WHERE tenant_id = ? AND ticket_id = ?",
        (tenant_id, ticket_id),
    )
    conn.execute(
        """
        INSERT INTO tickets_fts (tenant_id, ticket_id, subject, body)
        VALUES (?, ?, ?, ?)
        """,
        (tenant_id, ticket_id, subject or "", body or ""),
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
    fts_q = (q or "").strip()
    use_fts = bool(fts_q)
    if fts_q:
        from ticket_triage.data_crypto import encryption_enabled

        if encryption_enabled():
            # 密文无法建可用 FTS；退回 LIKE（与加密前行为一致，命中率仍有限）
            use_fts = False
            like = f"%{fts_q}%"
            where.append("(ticket_id LIKE ? OR subject LIKE ? OR body LIKE ?)")
            params.extend([like, like, like])
    if cursor:
        # cursor = updated_at|ticket_id（DESC）
        parts = cursor.split("|", 1)
        if len(parts) == 2:
            where.append("(updated_at < ? OR (updated_at = ? AND ticket_id < ?))")
            params.extend([parts[0], parts[0], parts[1]])
    with _LOCK:
        conn = _connect()
        try:
            if use_fts:
                # 短查询走 trigram；过长则截断，避免整段正文 AND 过严
                term = fts_q if len(fts_q) <= 64 else fts_q[:64]
                fts_params: list[Any] = [tenant_id, _fts_quote(term)]
                fts_where = ["t.tenant_id = ?", "tickets_fts MATCH ?"]
                if statuses:
                    marks = ",".join("?" for _ in statuses)
                    fts_where.append(f"t.status IN ({marks})")
                    fts_params.extend(statuses)
                if cursor:
                    parts = cursor.split("|", 1)
                    if len(parts) == 2:
                        fts_where.append(
                            "(t.updated_at < ? OR (t.updated_at = ? AND t.ticket_id < ?))"
                        )
                        fts_params.extend([parts[0], parts[0], parts[1]])
                cols = (
                    "t.tenant_id, t.ticket_id, t.status, t.subject, t.body, "
                    "t.history_json, t.updated_at"
                )
                sql = (
                    f"SELECT {cols} FROM tickets AS t "
                    "JOIN tickets_fts ON t.tenant_id = tickets_fts.tenant_id "
                    "AND t.ticket_id = tickets_fts.ticket_id "
                    f"WHERE {' AND '.join(fts_where)} "
                    "ORDER BY t.updated_at DESC, t.ticket_id DESC"
                )
                if limit is not None:
                    sql += " LIMIT ?"
                    fts_params.append(max(1, min(int(limit), 200)))
                rows = conn.execute(sql, tuple(fts_params)).fetchall()
            else:
                sql = (
                    f"SELECT {_select_cols()} FROM tickets WHERE {' AND '.join(where)} "
                    "ORDER BY updated_at DESC, ticket_id DESC"
                )
                local_params = list(params)
                if limit is not None:
                    sql += " LIMIT ?"
                    local_params.append(max(1, min(int(limit), 200)))
                rows = conn.execute(sql, tuple(local_params)).fetchall()
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
            # 索引明文；已存在工单也刷新 FTS，避免 IGNORE 后索引缺失
            _sync_ticket_fts(conn, tenant_id, ticket_id, subject, body)
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
            conn.execute(
                "DELETE FROM tickets_fts WHERE tenant_id = ? AND ticket_id = ?",
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
