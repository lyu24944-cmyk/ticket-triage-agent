"""P5：待人审状态与节点追踪落在 SQLite，进程重启后仍可恢复。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ticket_triage.models import Classification, DraftReply, TicketIn
from ticket_triage.tenants import DEFAULT_TENANT_ID

_LOCK = threading.Lock()
_MODELS = {
    "TicketIn": TicketIn,
    "Classification": Classification,
    "DraftReply": DraftReply,
}
_DEFAULT_DB = Path(__file__).resolve().parents[2] / ".data" / "runtime.db"


def runtime_db_path() -> Path:
    from ticket_triage.config import settings

    raw = settings.runtime_db_path
    path = Path(raw) if raw else _DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class QueueFull(Exception):
    """已受理未结束的单已经达到上限。"""


class ClaimLost(Exception):
    """这张单的领取标记已经不属于当前工人。"""

    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id
        super().__init__(trace_id)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(runtime_db_path()), check_same_thread=False)
    conn.execute("PRAGMA busy_timeout=2000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_runs (
            ticket_id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS closed_runs (
            ticket_id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            state_json TEXT NOT NULL,
            closed_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_jobs (
            trace_id TEXT PRIMARY KEY,
            ticket_id TEXT NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            request_json TEXT NOT NULL DEFAULT '',
            checkpoint_json TEXT NOT NULL DEFAULT '',
            next_node TEXT NOT NULL DEFAULT '',
            claim_token TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trace_spans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT NOT NULL,
            node TEXT NOT NULL,
            duration_ms REAL NOT NULL,
            status TEXT NOT NULL,
            detail TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    _migrate(conn)
    conn.commit()
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in have:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _migrate_tenant_tables(conn: sqlite3.Connection) -> None:
    """pending/closed/run_jobs 补 tenant_id；幂等表；按需重建复合主键。"""
    for table in ("pending_runs", "closed_runs", "run_jobs"):
        _ensure_column(conn, table, "tenant_id", "TEXT NOT NULL DEFAULT 'default'")
        conn.execute(
            f"UPDATE {table} SET tenant_id = ? WHERE tenant_id = '' OR tenant_id IS NULL",
            (DEFAULT_TENANT_ID,),
        )

    pending_pk = [row[1] for row in conn.execute("PRAGMA table_info(pending_runs)") if row[5]]
    if pending_pk == ["ticket_id"]:
        conn.execute(
            """
            CREATE TABLE pending_runs_v3 (
                tenant_id TEXT NOT NULL DEFAULT 'default',
                ticket_id TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                state_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, ticket_id)
            )
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO pending_runs_v3
                (tenant_id, ticket_id, trace_id, state_json, updated_at)
            SELECT COALESCE(NULLIF(tenant_id, ''), 'default'), ticket_id, trace_id,
                   state_json, updated_at FROM pending_runs
            """
        )
        conn.execute("DROP TABLE pending_runs")
        conn.execute("ALTER TABLE pending_runs_v3 RENAME TO pending_runs")

    closed_pk = [row[1] for row in conn.execute("PRAGMA table_info(closed_runs)") if row[5]]
    if closed_pk == ["ticket_id"]:
        conn.execute(
            """
            CREATE TABLE closed_runs_v3 (
                tenant_id TEXT NOT NULL DEFAULT 'default',
                ticket_id TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                state_json TEXT NOT NULL,
                closed_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, ticket_id)
            )
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO closed_runs_v3
                (tenant_id, ticket_id, trace_id, state_json, closed_at)
            SELECT COALESCE(NULLIF(tenant_id, ''), 'default'), ticket_id, trace_id,
                   state_json, closed_at FROM closed_runs
            """
        )
        conn.execute("DROP TABLE closed_runs")
        conn.execute("ALTER TABLE closed_runs_v3 RENAME TO closed_runs")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            tenant_id TEXT NOT NULL,
            idem_key TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            response_json TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, idem_key)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_run_jobs_tenant_active "
        "ON run_jobs(tenant_id, status, created_at)"
    )


def _migrate(conn: sqlite3.Connection) -> None:
    """已有库补列。新建库的 CREATE TABLE 已经带这些列，这里再记版本。"""
    _ensure_column(conn, "run_jobs", "request_json", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "run_jobs", "checkpoint_json", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "run_jobs", "next_node", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "run_jobs", "claim_token", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "run_jobs", "created_at", "TEXT NOT NULL DEFAULT ''")
    _migrate_tenant_tables(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for version in (1, 2, 3):
        conn.execute(
            """
            INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            (version, now),
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_run_jobs_active ON run_jobs(status, created_at)"
    )


def _dump_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return {"__model__": value.__class__.__name__, "data": value.model_dump(mode="json")}
    if isinstance(value, list):
        return [_dump_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _dump_value(item) for key, item in value.items()}
    return value


def _load_value(value: Any) -> Any:
    if isinstance(value, dict) and "__model__" in value:
        model = _MODELS[value["__model__"]]
        return model.model_validate(value["data"])
    if isinstance(value, list):
        return [_load_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _load_value(item) for key, item in value.items()}
    return value


def save_pending(
    ticket_id: str,
    trace_id: str,
    state: dict[str, Any],
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> None:
    """把暂停中的图状态写入 SQLite。"""
    payload = json.dumps(_dump_value(dict(state)), ensure_ascii=False)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO pending_runs
                    (tenant_id, ticket_id, trace_id, state_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, ticket_id) DO UPDATE SET
                    trace_id = excluded.trace_id,
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (tenant_id, ticket_id, trace_id, payload, now),
            )
            conn.commit()
        finally:
            conn.close()


def load_pending(
    ticket_id: str,
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, Any] | None:
    """读取暂停状态；不存在则返回 None。"""
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                """
                SELECT state_json FROM pending_runs
                WHERE tenant_id = ? AND ticket_id = ?
                """,
                (tenant_id, ticket_id),
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    raw = json.loads(row[0])
    return _load_value(raw)


def list_pending(
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
    q: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> list[dict[str, Any]]:
    """列出待人审工单，供审核台展示。不含完整图状态。"""
    params: list[Any] = [tenant_id]
    where = ["tenant_id = ?"]
    if cursor:
        parts = cursor.split("|", 1)
        if len(parts) == 2:
            where.append("(updated_at < ? OR (updated_at = ? AND ticket_id < ?))")
            params.extend([parts[0], parts[0], parts[1]])
    sql = (
        "SELECT tenant_id, ticket_id, trace_id, state_json, updated_at "
        f"FROM pending_runs WHERE {' AND '.join(where)} "
        "ORDER BY updated_at DESC, ticket_id DESC"
    )
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
        finally:
            conn.close()
    items: list[dict[str, Any]] = []
    needle = (q or "").strip().lower()
    for row_tenant, ticket_id, trace_id, state_json, updated_at in rows:
        state = _load_value(json.loads(state_json))
        ticket = state.get("ticket")
        classification = state.get("classification")
        draft = state.get("draft")
        subject = getattr(ticket, "subject", "") or ""
        body = getattr(ticket, "body", "") or ""
        if needle and needle not in f"{ticket_id} {subject} {body}".lower():
            continue
        hits = []
        for hit in state.get("kb_hits") or []:
            if isinstance(hit, dict):
                hits.append(
                    {"id": hit.get("id"), "title": hit.get("title"), "body": hit.get("body")}
                )
        items.append(
            {
                "tenant_id": row_tenant,
                "ticket_id": ticket_id,
                "trace_id": trace_id,
                "updated_at": updated_at,
                "subject": subject,
                "body": body,
                "category": classification.category.value if classification else None,
                "priority": classification.priority.value if classification else None,
                "draft_body": draft.body if draft else "",
                "citations": list(draft.citations) if draft else [],
                "kb_hits": hits,
                "errors": list(state.get("errors") or []),
                "tool_calls": [
                    {
                        "name": call.get("name"),
                        "ok": bool(call.get("ok")),
                        "error": call.get("error"),
                    }
                    for call in (state.get("tool_calls") or [])
                    if isinstance(call, dict)
                ],
            }
        )
        if limit is not None and len(items) >= max(1, min(int(limit), 200)):
            break
    return items


def archive_pending(
    ticket_id: str,
    trace_id: str,
    state: dict[str, Any],
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> None:
    """结案前留下草稿，便于以后原样送回待审。"""
    payload = json.dumps(_dump_value(dict(state)), ensure_ascii=False)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO closed_runs
                    (tenant_id, ticket_id, trace_id, state_json, closed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, ticket_id) DO UPDATE SET
                    trace_id = excluded.trace_id,
                    state_json = excluded.state_json,
                    closed_at = excluded.closed_at
                """,
                (tenant_id, ticket_id, trace_id, payload, now),
            )
            conn.execute(
                "DELETE FROM pending_runs WHERE tenant_id = ? AND ticket_id = ?",
                (tenant_id, ticket_id),
            )
            conn.commit()
        finally:
            conn.close()


def load_closed(
    ticket_id: str,
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, Any] | None:
    """读取结案时留档的图状态；没有则返回 None。"""
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                """
                SELECT state_json FROM closed_runs
                WHERE tenant_id = ? AND ticket_id = ?
                """,
                (tenant_id, ticket_id),
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    return _load_value(json.loads(row[0]))


def delete_closed(
    ticket_id: str,
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "DELETE FROM closed_runs WHERE tenant_id = ? AND ticket_id = ?",
                (tenant_id, ticket_id),
            )
            conn.commit()
        finally:
            conn.close()


def delete_pending(
    ticket_id: str,
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "DELETE FROM pending_runs WHERE tenant_id = ? AND ticket_id = ?",
                (tenant_id, ticket_id),
            )
            conn.commit()
        finally:
            conn.close()


def record_span(
    trace_id: str,
    node: str,
    duration_ms: float,
    status: str,
    detail: str | None = None,
) -> None:
    """记录一个节点的耗时与状态。"""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO trace_spans (trace_id, node, duration_ms, status, detail, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (trace_id, node, duration_ms, status, detail, now),
            )
            conn.commit()
        finally:
            conn.close()


def save_run(result: Any) -> None:
    """保存一次 HTTP 运行的当前结果。运行中和结束后都写在这里，供轮询读取。"""
    from ticket_triage.models import RunResult

    if not isinstance(result, RunResult):
        result = RunResult.model_validate(result)
    if not result.trace_id:
        raise ValueError("run result missing trace_id")
    payload = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO run_jobs (trace_id, ticket_id, status, result_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(trace_id) DO UPDATE SET
                    ticket_id = excluded.ticket_id,
                    status = excluded.status,
                    result_json = excluded.result_json,
                    updated_at = excluded.updated_at
                """,
                (result.trace_id, result.ticket_id, result.status, payload, now),
            )
            conn.commit()
        finally:
            conn.close()


def load_run(trace_id: str) -> Any | None:
    """按 trace_id 读取运行结果。没有记录时返回 None。"""
    from ticket_triage.models import RunResult

    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT result_json FROM run_jobs WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()
        finally:
            conn.close()
    if row is None:
        return None
    return RunResult.model_validate(json.loads(row[0]))


def _now() -> str:
    """UTC 时间，含毫秒。同一秒内多张单靠毫秒和 rowid 保持受理顺序。"""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time() * 1000) % 1000:03d}Z"


def _job_from_row(row: tuple, claim: str) -> dict[str, Any]:
    from ticket_triage.models import RunRequest

    trace_id, ticket_id, request_json, checkpoint_json, next_node = row
    state = _load_value(json.loads(checkpoint_json)) if checkpoint_json else None
    request = RunRequest.model_validate(json.loads(request_json)) if request_json else None
    return {
        "trace_id": trace_id,
        "ticket_id": ticket_id,
        "request": request,
        "state": state,
        "next_node": next_node or "",
        "claim": claim,
    }


def accept_run(
    ticket_id: str,
    trace_id: str,
    request: dict[str, Any],
    limit: int,
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> Any:
    """受理一张新单，或返回这张单上已经在跑的结果。队列满时抛出 QueueFull。

    队列计数按 tenant_id 隔离。
    """
    from ticket_triage.models import RunResult

    result = RunResult(ticket_id=ticket_id, status="running", trace_id=trace_id)
    payload = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
    request_json = json.dumps(request, ensure_ascii=False)
    now = _now()
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT result_json FROM run_jobs
                WHERE tenant_id = ? AND ticket_id = ? AND status = 'running'
                ORDER BY created_at DESC LIMIT 1
                """,
                (tenant_id, ticket_id),
            ).fetchone()
            if existing is not None:
                conn.rollback()
                return RunResult.model_validate(json.loads(existing[0]))
            active = conn.execute(
                """
                SELECT COUNT(*) FROM run_jobs
                WHERE tenant_id = ? AND status = 'running'
                """,
                (tenant_id,),
            ).fetchone()[0]
            if active >= limit:
                conn.rollback()
                raise QueueFull()
            conn.execute(
                """
                INSERT INTO run_jobs (
                    trace_id, ticket_id, status, result_json, updated_at,
                    request_json, checkpoint_json, next_node, claim_token, created_at,
                    tenant_id
                ) VALUES (?, ?, 'running', ?, ?, ?, '', 'ingest', '', ?, ?)
                """,
                (trace_id, ticket_id, payload, now, request_json, now, tenant_id),
            )
            conn.commit()
        finally:
            conn.close()
    return result


def active_job_count_for_tenant(tenant_id: str = DEFAULT_TENANT_ID) -> int:
    """某租户已受理未结束的单数。"""
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM run_jobs
                WHERE tenant_id = ? AND status = 'running'
                """,
                (tenant_id,),
            ).fetchone()
        finally:
            conn.close()
    return int(row[0])


def lookup_idempotency(
    tenant_id: str,
    idem_key: str,
) -> tuple[int, dict[str, Any]] | None:
    """命中未过期幂等键时返回 (status_code, body)。"""
    from ticket_triage.config import settings

    ttl = max(60, int(settings.idempotency_ttl_seconds))
    cutoff = time.time() - ttl
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                """
                SELECT request_hash, response_json, status_code, created_at
                FROM idempotency_keys
                WHERE tenant_id = ? AND idem_key = ?
                """,
                (tenant_id, idem_key),
            ).fetchone()
        finally:
            conn.close()
    if row is None:
        return None
    created = row[3]
    # created_at 为 ISO；解析失败则视为仍有效
    try:
        created_ts = time.mktime(time.strptime(created[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        created_ts = time.time()
    if created_ts < cutoff:
        return None
    return int(row[2]), {
        "request_hash": row[0],
        "body": json.loads(row[1]),
    }


def save_idempotency(
    tenant_id: str,
    idem_key: str,
    request_hash: str,
    status_code: int,
    body: dict[str, Any],
) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload = json.dumps(body, ensure_ascii=False)
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO idempotency_keys
                    (tenant_id, idem_key, request_hash, response_json, status_code, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, idem_key) DO UPDATE SET
                    request_hash = excluded.request_hash,
                    response_json = excluded.response_json,
                    status_code = excluded.status_code,
                    created_at = excluded.created_at
                """,
                (tenant_id, idem_key, request_hash, payload, status_code, now),
            )
            conn.commit()
        finally:
            conn.close()


def claim_next_job() -> dict[str, Any] | None:
    """领取最早受理、还没有工人的运行。没有则返回 None。"""
    token = uuid.uuid4().hex
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT trace_id, ticket_id, request_json, checkpoint_json, next_node
                FROM run_jobs
                WHERE status = 'running' AND claim_token = '' AND request_json != ''
                ORDER BY created_at ASC, rowid ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                conn.rollback()
                return None
            updated = conn.execute(
                """
                UPDATE run_jobs SET claim_token = ?
                WHERE trace_id = ? AND status = 'running' AND claim_token = ''
                """,
                (token, row[0]),
            )
            if updated.rowcount != 1:
                conn.rollback()
                return None
            conn.commit()
        finally:
            conn.close()
    return _job_from_row(row, token)


def save_job_checkpoint(trace_id: str, claim: str, state: dict[str, Any], next_node: str) -> bool:
    """记下刚完成的状态和下一个节点。领取标记对不上时返回 False。"""
    payload = json.dumps(_dump_value(dict(state)), ensure_ascii=False)
    with _LOCK:
        conn = _connect()
        try:
            updated = conn.execute(
                """
                UPDATE run_jobs
                SET checkpoint_json = ?, next_node = ?, updated_at = ?
                WHERE trace_id = ? AND claim_token = ? AND status = 'running'
                """,
                (payload, next_node, _now(), trace_id, claim),
            )
            conn.commit()
            return updated.rowcount == 1
        finally:
            conn.close()


def clear_claim(trace_id: str, claim: str) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                """
                UPDATE run_jobs SET claim_token = ''
                WHERE trace_id = ? AND claim_token = ?
                """,
                (trace_id, claim),
            )
            conn.commit()
        finally:
            conn.close()


def release_running_claims() -> int:
    """进程已经退出。进行中的单放下领取标记，等新进程的工人来领。"""
    with _LOCK:
        conn = _connect()
        try:
            updated = conn.execute(
                """
                UPDATE run_jobs SET claim_token = ''
                WHERE status = 'running' AND claim_token != '' AND request_json != ''
                """
            )
            conn.commit()
            return updated.rowcount
        finally:
            conn.close()


def active_job_count() -> int:
    """已受理但还没结束的单，含排队和正在执行。"""
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM run_jobs WHERE status = 'running'"
            ).fetchone()
        finally:
            conn.close()
    return int(row[0])


def stage_running_job(
    *,
    trace_id: str,
    ticket_id: str,
    request: dict[str, Any],
    state: dict[str, Any] | None,
    next_node: str,
    claim_token: str = "",
) -> None:
    """写入一张可恢复的运行。测试用来模拟进程在某个节点退出。"""
    from ticket_triage.models import RunResult

    result = RunResult(ticket_id=ticket_id, status="running", trace_id=trace_id)
    payload = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
    request_json = json.dumps(request, ensure_ascii=False)
    checkpoint = json.dumps(_dump_value(dict(state)), ensure_ascii=False) if state else ""
    now = _now()
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO run_jobs (
                    trace_id, ticket_id, status, result_json, updated_at,
                    request_json, checkpoint_json, next_node, claim_token, created_at
                ) VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    ticket_id,
                    payload,
                    now,
                    request_json,
                    checkpoint,
                    next_node,
                    claim_token,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()


def read_job(trace_id: str) -> dict[str, Any] | None:
    """读取运行的检查点。没有这条记录时返回 None。"""
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                """
                SELECT trace_id, ticket_id, status, request_json, checkpoint_json,
                       next_node, claim_token
                FROM run_jobs WHERE trace_id = ?
                """,
                (trace_id,),
            ).fetchone()
        finally:
            conn.close()
    if row is None:
        return None
    state = _load_value(json.loads(row[4])) if row[4] else None
    return {
        "trace_id": row[0],
        "ticket_id": row[1],
        "status": row[2],
        "state": state,
        "next_node": row[5] or "",
        "claim": row[6] or "",
    }


def mark_interrupted_runs() -> int:
    """没有请求正文的旧运行无法接着跑，标成失败。有检查点的单留着。"""
    from ticket_triage.models import RunResult

    now = _now()
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT trace_id, ticket_id FROM run_jobs
                WHERE status = 'running' AND COALESCE(request_json, '') = ''
                """
            ).fetchall()
            for trace_id, ticket_id in rows:
                result = RunResult(
                    ticket_id=ticket_id,
                    status="failed",
                    errors=["run_interrupted"],
                    trace_id=trace_id,
                )
                payload = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
                conn.execute(
                    """
                    UPDATE run_jobs
                    SET status = ?, result_json = ?, updated_at = ?
                    WHERE trace_id = ?
                    """,
                    ("failed", payload, now, trace_id),
                )
            conn.commit()
        finally:
            conn.close()
    return len(rows)


def checkpoint_runtime() -> None:
    """把 WAL 收进主文件。停进程后备份才不会漏掉最后一次写入。"""
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()


def probe_writable() -> None:
    """确认运行库可以写入。失败时抛出异常。"""
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS _health (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO _health (id) VALUES (1) ON CONFLICT(id) DO NOTHING")
            conn.commit()
        finally:
            conn.close()


def get_trace(trace_id: str) -> dict[str, Any]:
    """汇总一次运行的节点追踪。"""
    from ticket_triage.config import settings

    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT node, duration_ms, status, detail, created_at
                FROM trace_spans WHERE trace_id = ? ORDER BY id
                """,
                (trace_id,),
            ).fetchall()
        finally:
            conn.close()
    spans = [
        {
            "node": row[0],
            "duration_ms": row[1],
            "status": row[2],
            "detail": row[3],
            "created_at": row[4],
        }
        for row in rows
    ]
    total = sum(span["duration_ms"] for span in spans)
    return {
        "trace_id": trace_id,
        "spans": spans,
        "total_ms": total,
        "over_budget": total >= settings.trace_budget_ms,
        "budget_ms": settings.trace_budget_ms,
    }


def purge_ticket_runtime(
    ticket_id: str,
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> int:
    """删掉一张工单在 runtime 里的 pending / closed / run_jobs / 相关 spans。"""
    with _LOCK:
        conn = _connect()
        try:
            traces: set[str] = set()
            for table in ("run_jobs", "pending_runs", "closed_runs"):
                for row in conn.execute(
                    f"SELECT trace_id FROM {table} WHERE tenant_id = ? AND ticket_id = ?",
                    (tenant_id, ticket_id),
                ).fetchall():
                    traces.add(str(row[0]))
            for table in ("pending_runs", "closed_runs", "run_jobs"):
                conn.execute(
                    f"DELETE FROM {table} WHERE tenant_id = ? AND ticket_id = ?",
                    (tenant_id, ticket_id),
                )
            deleted_spans = 0
            for trace_id in traces:
                cur = conn.execute("DELETE FROM trace_spans WHERE trace_id = ?", (trace_id,))
                deleted_spans += int(cur.rowcount)
            conn.commit()
            return len(traces) + deleted_spans
        finally:
            conn.close()


def ticket_runtime_residue(
    ticket_id: str,
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> list[str]:
    """DSAR 验收：该 ticket_id 是否还在 runtime 表里。"""
    issues: list[str] = []
    with _LOCK:
        conn = _connect()
        try:
            for table in ("pending_runs", "closed_runs", "run_jobs"):
                row = conn.execute(
                    f"""
                    SELECT 1 FROM {table}
                    WHERE tenant_id = ? AND ticket_id = ? LIMIT 1
                    """,
                    (tenant_id, ticket_id),
                ).fetchone()
                if row:
                    issues.append(f"{table}_present")
        finally:
            conn.close()
    return issues
