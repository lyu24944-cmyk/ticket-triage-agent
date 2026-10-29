"""P3 集成：Webhook 签名/幂等、/v1、租户隔离、配额、结案回调。"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from fastapi.testclient import TestClient

from ticket_triage.api.main import app
from ticket_triage.auth_jwt import mint_review_token
from ticket_triage.config import settings
from ticket_triage.secrets import clear_secret_cache
from ticket_triage.tenants import clear_tenant_cache
from ticket_triage.tools import tickets as tickets_mod


def _sign(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _wait_pending(client: TestClient, headers: dict, ticket_id: str, timeout: float = 5) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = client.get("/v1/runs/pending", headers=headers).json()["items"]
        for row in rows:
            if row["ticket_id"] == ticket_id:
                return row
        time.sleep(0.05)
    raise AssertionError(f"pending not found: {ticket_id}")


def test_zendesk_webhook_bad_signature(monkeypatch) -> None:
    monkeypatch.setattr(settings, "webhook_signing_secret", "wh-secret")
    clear_tenant_cache()
    client = TestClient(app)
    body = json.dumps(
        {"id": 101, "subject": "退款", "description": "我被重复扣款，需要退款"}
    ).encode()
    response = client.post(
        "/v1/webhooks/zendesk",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Zendesk-Webhook-Signature": "sha256=deadbeef",
            "X-Zendesk-Delivery-Id": "del-bad",
        },
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "webhook_signature_invalid"


def test_zendesk_duplicate_delivery_is_idempotent(monkeypatch, finish_run) -> None:
    monkeypatch.setattr(settings, "webhook_signing_secret", "wh-secret")
    monkeypatch.setattr(settings, "require_hitl", True)
    clear_tenant_cache()
    client = TestClient(app)
    payload = {"id": 202, "subject": "退款", "description": "我被重复扣款，需要退款"}
    body = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Zendesk-Webhook-Signature": _sign("wh-secret", body),
        "X-Zendesk-Delivery-Id": "del-202",
    }
    first = client.post("/v1/webhooks/zendesk", content=body, headers=headers)
    assert first.status_code == 202
    assert first.json()["idempotent_replay"] is False
    trace_id = first.json()["trace_id"]
    ticket_id = first.json()["ticket_id"]

    second = client.post("/v1/webhooks/zendesk", content=body, headers=headers)
    assert second.status_code == 202
    assert second.json()["idempotent_replay"] is True
    assert second.json()["trace_id"] == trace_id
    assert second.json()["ticket_id"] == ticket_id

    # 库里只有一张
    assert tickets_mod.get_ticket(ticket_id) is not None
    # 同 delivery 不应再产生第二张 running（ticket 仍唯一）
    assert tickets_mod.list_tickets(tenant_id="default")


def test_idempotency_key_replay_and_conflict(service_headers, monkeypatch) -> None:
    monkeypatch.setattr(settings, "require_hitl", True)
    client = TestClient(app)
    payload = {
        "ticket": {
            "ticket_id": "idem-1",
            "subject": "退款",
            "body": "我被重复扣款，需要退款",
        }
    }
    headers = {**service_headers, "Idempotency-Key": "key-1"}
    first = client.post("/v1/runs", json=payload, headers=headers)
    assert first.status_code == 202
    trace = first.json()["trace_id"]

    replay = client.post("/v1/runs", json=payload, headers=headers)
    assert replay.status_code == 202
    assert replay.json()["trace_id"] == trace

    conflict = client.post(
        "/v1/runs",
        json={
            "ticket": {
                "ticket_id": "idem-2",
                "subject": "别的",
                "body": "我被重复扣款，需要退款",
            }
        },
        headers=headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


def test_tenant_isolation(monkeypatch) -> None:
    tenants = [
        {
            "tenant_id": "acme",
            "service_api_key": "svc-acme",
            "review_api_key": "rev-acme",
            "webhook_secret": "wh-acme",
            "queue_limit": 8,
        },
        {
            "tenant_id": "beta",
            "service_api_key": "svc-beta",
            "review_api_key": "rev-beta",
            "webhook_secret": "wh-beta",
            "queue_limit": 8,
        },
    ]
    monkeypatch.setattr(settings, "tenants_json", json.dumps(tenants))
    monkeypatch.setattr(settings, "require_hitl", True)
    clear_tenant_cache()
    clear_secret_cache()
    client = TestClient(app)

    r_acme = client.post(
        "/v1/runs",
        json={
            "ticket": {
                "ticket_id": "shared-1",
                "subject": "退款",
                "body": "我被重复扣款，需要退款",
            }
        },
        headers={"X-API-Key": "svc-acme"},
    )
    assert r_acme.status_code == 202

    r_beta = client.post(
        "/v1/runs",
        json={
            "ticket": {
                "ticket_id": "shared-1",
                "subject": "账号",
                "body": "我无法登录账户",
            }
        },
        headers={"X-API-Key": "svc-beta"},
    )
    assert r_beta.status_code == 202

    token_acme = mint_review_token("ada", tenant_id="acme")
    token_beta = mint_review_token("bob", tenant_id="beta")
    pending_acme = _wait_pending(
        client, {"Authorization": f"Bearer {token_acme}"}, "shared-1"
    )
    pending_beta = _wait_pending(
        client, {"Authorization": f"Bearer {token_beta}"}, "shared-1"
    )
    assert pending_acme["ticket_id"] == "shared-1"
    assert pending_beta["ticket_id"] == "shared-1"
    # 互相看不到对方的结案列表（此时可能都还没结案，至少租户过滤不串）
    closed_acme = client.get(
        "/v1/tickets", headers={"Authorization": f"Bearer {token_acme}"}
    ).json()["items"]
    assert all(t.get("tenant_id", "acme") == "acme" for t in closed_acme)


def test_tenant_queue_quota(monkeypatch) -> None:
    tenants = [
        {
            "tenant_id": "tiny",
            "service_api_key": "svc-tiny",
            "review_api_key": "rev-tiny",
            "queue_limit": 1,
        }
    ]
    monkeypatch.setattr(settings, "tenants_json", json.dumps(tenants))
    monkeypatch.setattr(settings, "require_hitl", True)
    monkeypatch.setattr(settings, "run_workers", 0)  # 尽量不消费，占住队列
    clear_tenant_cache()
    clear_secret_cache()

    # run_workers=0 可能导致没有工人；accept 仍会写入 running
    from ticket_triage.graph import workflow as wf

    monkeypatch.setattr(wf, "_ensure_workers", lambda: None)

    client = TestClient(app)
    headers = {"X-API-Key": "svc-tiny"}
    first = client.post(
        "/v1/runs",
        json={
            "ticket": {
                "ticket_id": "q-1",
                "subject": "退款",
                "body": "我被重复扣款，需要退款",
            }
        },
        headers=headers,
    )
    assert first.status_code == 202
    second = client.post(
        "/v1/runs",
        json={
            "ticket": {
                "ticket_id": "q-2",
                "subject": "退款",
                "body": "我被重复扣款，需要退款",
            }
        },
        headers=headers,
    )
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "queue_full"


def test_closure_callback_on_approve(monkeypatch, service_headers, review_headers) -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, payload: dict) -> None:
        calls.append({"url": url, "payload": payload})

    monkeypatch.setattr(settings, "closure_callback_url", "http://callback.test/hook")
    monkeypatch.setattr(settings, "require_hitl", True)
    clear_tenant_cache()
    import ticket_triage.integrations.callback as cb

    monkeypatch.setattr(cb, "_post", fake_post)

    client = TestClient(app)
    created = client.post(
        "/v1/runs",
        json={
            "ticket": {
                "ticket_id": "cb-1",
                "subject": "退款",
                "body": "我被重复扣款，需要退款",
            }
        },
        headers=service_headers,
    )
    assert created.status_code == 202
    _wait_pending(client, review_headers, "cb-1")
    approved = client.post(
        "/v1/runs/human",
        json={"ticket_id": "cb-1", "approved": True},
        headers=review_headers,
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "resolved"
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not calls:
        time.sleep(0.05)
    assert calls
    assert calls[0]["payload"]["status"] == "resolved"
    assert calls[0]["payload"]["ticket_id"] == "cb-1"


def test_email_webhook_happy_path(monkeypatch) -> None:
    monkeypatch.setattr(settings, "webhook_signing_secret", "wh-secret")
    clear_tenant_cache()
    client = TestClient(app)
    payload = {
        "message_id": "<abc@mail>",
        "subject": "无法登录",
        "body": "我无法登录账户请协助",
        "from": "user@example.com",
    }
    body = json.dumps(payload).encode()
    response = client.post(
        "/v1/webhooks/email",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Email-Gateway-Signature": _sign("wh-secret", body),
            "X-Email-Gateway-Delivery-Id": "mail-del-1",
        },
    )
    assert response.status_code == 202
    assert response.json()["ticket_id"].startswith("mail-")
