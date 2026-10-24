"""P5：待人审状态与节点追踪落在 SQLite，进程重启后仍可恢复。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from ticket_triage.models import Classification, DraftReply, TicketIn

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


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(runtime_db_path()), check_same_thread=False)
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
    conn.commit()
    return conn


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


def save_pending(ticket_id: str, trace_id: str, state: dict[str, Any]) -> None:
    """把暂停中的图状态写入 SQLite。"""
    payload = json.dumps(_dump_value(dict(state)), ensure_ascii=False)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO pending_runs (ticket_id, trace_id, state_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(ticket_id) DO UPDATE SET
                    trace_id = excluded.trace_id,
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (ticket_id, trace_id, payload, now),
            )
            conn.commit()
        finally:
            conn.close()


def load_pending(ticket_id: str) -> dict[str, Any] | None:
    """读取暂停状态；不存在则返回 None。"""
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT state_json FROM pending_runs WHERE ticket_id = ?",
                (ticket_id,),
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    raw = json.loads(row[0])
    return _load_value(raw)


def list_pending() -> list[dict[str, Any]]:
    """列出待人审工单，供审核台展示。不含完整图状态。"""
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT ticket_id, trace_id, state_json, updated_at
                FROM pending_runs ORDER BY updated_at DESC
                """
            ).fetchall()
        finally:
            conn.close()
    items: list[dict[str, Any]] = []
    for ticket_id, trace_id, state_json, updated_at in rows:
        state = _load_value(json.loads(state_json))
        ticket = state.get("ticket")
        classification = state.get("classification")
        draft = state.get("draft")
        hits = []
        for hit in state.get("kb_hits") or []:
            if isinstance(hit, dict):
                hits.append(
                    {"id": hit.get("id"), "title": hit.get("title"), "body": hit.get("body")}
                )
        items.append(
            {
                "ticket_id": ticket_id,
                "trace_id": trace_id,
                "updated_at": updated_at,
                "subject": getattr(ticket, "subject", "") or "",
                "body": getattr(ticket, "body", "") or "",
                "category": classification.category.value if classification else None,
                "priority": classification.priority.value if classification else None,
                "draft_body": draft.body if draft else "",
                "citations": list(draft.citations) if draft else [],
                "kb_hits": hits,
            }
        )
    return items


def archive_pending(ticket_id: str, trace_id: str, state: dict[str, Any]) -> None:
    """结案前留下草稿，便于以后原样送回待审。"""
    payload = json.dumps(_dump_value(dict(state)), ensure_ascii=False)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO closed_runs (ticket_id, trace_id, state_json, closed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(ticket_id) DO UPDATE SET
                    trace_id = excluded.trace_id,
                    state_json = excluded.state_json,
                    closed_at = excluded.closed_at
                """,
                (ticket_id, trace_id, payload, now),
            )
            conn.execute("DELETE FROM pending_runs WHERE ticket_id = ?", (ticket_id,))
            conn.commit()
        finally:
            conn.close()


def load_closed(ticket_id: str) -> dict[str, Any] | None:
    """读取结案时留档的图状态；没有则返回 None。"""
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT state_json FROM closed_runs WHERE ticket_id = ?",
                (ticket_id,),
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    return _load_value(json.loads(row[0]))


def delete_pending(ticket_id: str) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("DELETE FROM pending_runs WHERE ticket_id = ?", (ticket_id,))
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
