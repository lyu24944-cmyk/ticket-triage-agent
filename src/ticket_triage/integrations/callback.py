"""结案 / 升级后的出站回调：HMAC 签名 + 有限次重试。

失败不回滚工单状态。签名口径与进线 Webhook 一致：HMAC-SHA256(secret, raw_body) hex，
头为 ``X-Ticket-Triage-Signature: sha256=<hex>``；幂等键
``X-Ticket-Triage-Delivery-Id``。重试落在 runtime SQLite，进程重启后仍可继续。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from ticket_triage.config import settings
from ticket_triage.tenants import context_for

logger = logging.getLogger("ticket_triage.callback")

SIGNATURE_HEADER = "X-Ticket-Triage-Signature"
DELIVERY_HEADER = "X-Ticket-Triage-Delivery-Id"
_LOCK = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def sign_body(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    if not signature_header or not secret:
        return False
    provided = signature_header.strip()
    if provided.lower().startswith("sha256="):
        provided = provided[7:].strip()
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


def notify_closure(
    *,
    tenant_id: str,
    ticket_id: str,
    status: str,
    trace_id: str | None = None,
) -> None:
    ctx = context_for(tenant_id)
    url = ctx.callback_url or settings.closure_callback_url
    if not url:
        return
    payload = {
        "tenant_id": tenant_id,
        "ticket_id": ticket_id,
        "status": status,
        "trace_id": trace_id,
        "event": "ticket.closed",
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    delivery_id = f"closure:{tenant_id}:{ticket_id}:{status}:{trace_id or uuid.uuid4().hex}"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ticket-triage-agent/p3",
        DELIVERY_HEADER: delivery_id,
    }
    secret = ctx.webhook_secret or settings.webhook_signing_secret
    if secret:
        headers[SIGNATURE_HEADER] = sign_body(secret, body)

    max_attempts = max(1, int(settings.closure_callback_max_attempts))
    row_id = _enqueue(
        delivery_id=delivery_id,
        url=url,
        body=body,
        headers=headers,
        max_attempts=max_attempts,
    )
    thread = threading.Thread(
        target=_attempt_row,
        kwargs={"row_id": row_id, "force": True},
        name=f"closure-cb-{ticket_id}",
        daemon=True,
    )
    thread.start()


def process_due_callbacks(*, limit: int = 8) -> int:
    """领取到期的 pending 回调并尝试投递。返回尝试次数。"""
    due = _list_due_ids(limit=limit)
    for row_id in due:
        _attempt_row(row_id, force=False)
    return len(due)


def _enqueue(
    *,
    delivery_id: str,
    url: str,
    body: bytes,
    headers: dict[str, str],
    max_attempts: int,
) -> int:
    from ticket_triage.runtime import _LOCK as runtime_lock, _connect

    now = _iso(_now())
    with runtime_lock:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO closure_callbacks
                    (delivery_id, url, body, headers_json, attempts, max_attempts,
                     next_attempt_at, status, last_error, created_at, updated_at)
                VALUES (?, ?, ?, ?, 0, ?, ?, 'pending', '', ?, ?)
                ON CONFLICT(delivery_id) DO NOTHING
                """,
                (
                    delivery_id,
                    url,
                    body.decode("utf-8"),
                    json.dumps(headers, ensure_ascii=False),
                    max_attempts,
                    now,
                    now,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id FROM closure_callbacks WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()


def _list_due_ids(*, limit: int) -> list[int]:
    from ticket_triage.runtime import _LOCK as runtime_lock, _connect

    now = _iso(_now())
    with runtime_lock:
        conn = _connect()
        try:
            # 卡住的 sending（进程被杀）超过 2 分钟重新领取
            stale = _iso(_now() - timedelta(seconds=120))
            conn.execute(
                """
                UPDATE closure_callbacks
                SET status = 'pending', updated_at = ?
                WHERE status = 'sending' AND updated_at < ?
                """,
                (now, stale),
            )
            conn.commit()
            rows = conn.execute(
                """
                SELECT id FROM closure_callbacks
                WHERE status = 'pending' AND next_attempt_at <= ?
                ORDER BY next_attempt_at ASC, id ASC
                LIMIT ?
                """,
                (now, max(1, limit)),
            ).fetchall()
            return [int(row[0]) for row in rows]
        finally:
            conn.close()


def _claim_for_attempt(row_id: int, *, force: bool) -> dict[str, Any] | None:
    """原子地把 pending 标成 sending，避免并发双投。"""
    from ticket_triage.runtime import _LOCK as runtime_lock, _connect

    now = _iso(_now())
    with runtime_lock:
        conn = _connect()
        try:
            if force:
                cur = conn.execute(
                    """
                    UPDATE closure_callbacks
                    SET status = 'sending', updated_at = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (now, row_id),
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE closure_callbacks
                    SET status = 'sending', updated_at = ?
                    WHERE id = ? AND status = 'pending' AND next_attempt_at <= ?
                    """,
                    (now, row_id, now),
                )
            if cur.rowcount != 1:
                conn.rollback()
                return None
            row = conn.execute(
                """
                SELECT id, delivery_id, url, body, headers_json, attempts, max_attempts
                FROM closure_callbacks WHERE id = ?
                """,
                (row_id,),
            ).fetchone()
            conn.commit()
        finally:
            conn.close()
    if not row:
        return None
    return {
        "id": int(row[0]),
        "delivery_id": row[1],
        "url": row[2],
        "body": row[3].encode("utf-8"),
        "headers": json.loads(row[4]),
        "attempts": int(row[5]),
        "max_attempts": int(row[6]),
    }


def _mark_done(row_id: int) -> None:
    from ticket_triage.runtime import _LOCK as runtime_lock, _connect

    now = _iso(_now())
    with runtime_lock:
        conn = _connect()
        try:
            conn.execute(
                """
                UPDATE closure_callbacks
                SET status = 'done', updated_at = ?, last_error = ''
                WHERE id = ?
                """,
                (now, row_id),
            )
            conn.commit()
        finally:
            conn.close()


def _mark_failure(row_id: int, *, attempts: int, max_attempts: int, error: str) -> None:
    from ticket_triage.runtime import _LOCK as runtime_lock, _connect

    now_dt = _now()
    if attempts >= max_attempts:
        status = "dead"
        next_at = _iso(now_dt)
    else:
        status = "pending"
        backoff = float(settings.closure_callback_backoff_seconds)
        delay = backoff * (2 ** max(0, attempts - 1))
        next_at = _iso(now_dt + timedelta(seconds=delay))
    with runtime_lock:
        conn = _connect()
        try:
            conn.execute(
                """
                UPDATE closure_callbacks
                SET attempts = ?, status = ?, next_attempt_at = ?,
                    last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (attempts, status, next_at, error[:500], _iso(now_dt), row_id),
            )
            conn.commit()
        finally:
            conn.close()
    if status == "dead":
        logger.error(
            "closure_callback_dead delivery_id row_id=%s err=%s",
            row_id,
            error,
        )
        try:
            from ticket_triage.ops_log import log_event

            log_event(
                "closure_callback_dead",
                level=logging.ERROR,
                node="callback",
                stub_fallback=False,
                tool_error="closure_callback_failed",
            )
        except Exception:
            logger.exception("ops log for dead callback failed")


def _http_post(url: str, body: bytes, headers: dict[str, str]) -> None:
    req = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )
    timeout = float(settings.closure_callback_timeout_seconds)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


def _attempt_row(row_id: int, *, force: bool = False) -> bool:
    if row_id <= 0:
        return False
    with _LOCK:
        row = _claim_for_attempt(row_id, force=force)
        if row is None:
            return False
        attempts = int(row["attempts"]) + 1
        try:
            _http_post(row["url"], row["body"], dict(row["headers"]))
            _mark_done(row_id)
            logger.info(
                "closure_callback_ok delivery_id=%s attempts=%s",
                row["delivery_id"],
                attempts,
            )
            return True
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            _mark_failure(
                row_id,
                attempts=attempts,
                max_attempts=int(row["max_attempts"]),
                error=str(exc),
            )
            logger.warning(
                "closure_callback_failed delivery_id=%s attempts=%s err=%s",
                row["delivery_id"],
                attempts,
                exc,
            )
            return False


# 兼容旧测试：同步投递一次（无入库）。新路径请用 notify_closure。
def _post(url: str, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ticket-triage-agent/p3",
    }
    secret = settings.webhook_signing_secret
    if secret:
        headers[SIGNATURE_HEADER] = sign_body(secret, body)
    headers[DELIVERY_HEADER] = f"legacy:{payload.get('ticket_id') or uuid.uuid4().hex}"
    try:
        _http_post(url, body, headers)
        logger.info(
            "closure_callback_ok ticket_id=%s status=%s",
            payload.get("ticket_id"),
            payload.get("status"),
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning(
            "closure_callback_failed ticket_id=%s err=%s",
            payload.get("ticket_id"),
            exc,
        )
