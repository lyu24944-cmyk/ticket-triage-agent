"""出站结案回调：HMAC 签名 + 有限次重试，不回滚状态。"""

from __future__ import annotations

import json
import time

from ticket_triage.config import settings
from ticket_triage.integrations import callback as cb
from ticket_triage.ops_log import resolve_alert_code
from ticket_triage.runtime import _LOCK, _connect
from ticket_triage.tenants import clear_tenant_cache


def _pending_rows() -> list[tuple]:
    with _LOCK:
        conn = _connect()
        try:
            return conn.execute(
                "SELECT status, attempts, delivery_id FROM closure_callbacks ORDER BY id"
            ).fetchall()
        finally:
            conn.close()


def test_sign_and_verify_roundtrip() -> None:
    body = b'{"event":"ticket.closed"}'
    header = cb.sign_body("wh-secret", body)
    assert header.startswith("sha256=")
    assert cb.verify_signature("wh-secret", body, header)
    assert not cb.verify_signature("other", body, header)


def test_notify_signs_and_succeeds(monkeypatch) -> None:
    seen: list[dict] = []

    def fake_http(url: str, body: bytes, headers: dict[str, str]) -> None:
        seen.append({"url": url, "body": body, "headers": dict(headers)})

    monkeypatch.setattr(settings, "closure_callback_url", "http://callback.test/hook")
    monkeypatch.setattr(settings, "webhook_signing_secret", "wh-secret")
    monkeypatch.setattr(settings, "closure_callback_max_attempts", 3)
    clear_tenant_cache()
    monkeypatch.setattr(cb, "_http_post", fake_http)

    cb.notify_closure(
        tenant_id="default",
        ticket_id="t-cb-ok",
        status="resolved",
        trace_id="tr-1",
    )
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not seen:
        time.sleep(0.02)
    assert seen
    headers = seen[0]["headers"]
    assert cb.DELIVERY_HEADER in headers
    assert cb.verify_signature(
        "wh-secret", seen[0]["body"], headers[cb.SIGNATURE_HEADER]
    )
    payload = json.loads(seen[0]["body"])
    assert payload["ticket_id"] == "t-cb-ok"
    assert payload["status"] == "resolved"
    rows = _pending_rows()
    assert rows and rows[0][0] == "done"


def test_notify_retries_then_succeeds(monkeypatch) -> None:
    attempts = {"n": 0}

    def flaky(url: str, body: bytes, headers: dict[str, str]) -> None:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise TimeoutError("downstream down")

    monkeypatch.setattr(settings, "closure_callback_url", "http://callback.test/hook")
    monkeypatch.setattr(settings, "webhook_signing_secret", "wh-secret")
    monkeypatch.setattr(settings, "closure_callback_max_attempts", 4)
    monkeypatch.setattr(settings, "closure_callback_backoff_seconds", 0.05)
    clear_tenant_cache()
    monkeypatch.setattr(cb, "_http_post", flaky)

    cb.notify_closure(
        tenant_id="default",
        ticket_id="t-cb-retry",
        status="escalated",
        trace_id="tr-2",
    )
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        cb.process_due_callbacks(limit=5)
        rows = _pending_rows()
        if rows and rows[-1][0] == "done":
            break
        time.sleep(0.05)
    assert attempts["n"] >= 3
    assert _pending_rows()[-1][0] == "done"


def test_notify_marks_dead_after_max_attempts(monkeypatch) -> None:
    def always_fail(url: str, body: bytes, headers: dict[str, str]) -> None:
        raise TimeoutError("still down")

    monkeypatch.setattr(settings, "closure_callback_url", "http://callback.test/hook")
    monkeypatch.setattr(settings, "webhook_signing_secret", "wh-secret")
    monkeypatch.setattr(settings, "closure_callback_max_attempts", 2)
    monkeypatch.setattr(settings, "closure_callback_backoff_seconds", 0.01)
    clear_tenant_cache()
    monkeypatch.setattr(cb, "_http_post", always_fail)

    cb.notify_closure(
        tenant_id="default",
        ticket_id="t-cb-dead",
        status="resolved",
        trace_id="tr-3",
    )
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        cb.process_due_callbacks(limit=5)
        rows = _pending_rows()
        if rows and rows[-1][0] == "dead":
            break
        time.sleep(0.05)
    assert _pending_rows()[-1][0] == "dead"
    assert resolve_alert_code(
        "closure_callback_dead", tool_error="closure_callback_failed"
    ) == "closure_callback_failed"


def test_no_url_is_noop(monkeypatch) -> None:
    monkeypatch.setattr(settings, "closure_callback_url", None)
    clear_tenant_cache()
    called = []

    def boom(*_a, **_k):
        called.append(1)
        raise AssertionError("should not post")

    monkeypatch.setattr(cb, "_http_post", boom)
    cb.notify_closure(tenant_id="default", ticket_id="t-none", status="resolved")
    time.sleep(0.05)
    assert called == []
    assert _pending_rows() == []
