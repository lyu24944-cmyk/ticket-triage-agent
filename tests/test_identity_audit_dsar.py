"""P1：人员 JWT、审计、DSAR 删除无正文残留。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from ticket_triage.api.main import app
from ticket_triage.audit import list_audit
from ticket_triage.dsar import assert_no_body_residue
from ticket_triage.tools.tickets import get_ticket


def _ticket(ticket_id: str) -> dict:
    return {
        "ticket": {
            "ticket_id": ticket_id,
            "subject": "紧急退款",
            "body": "我被重复扣款，需要退款 DSAR-SECRET-BODY",
        },
    }


def test_review_requires_bearer_not_api_key(service_headers, mint_headers, finish_run) -> None:
    client = TestClient(app)
    created = finish_run(client, client.post("/runs", json=_ticket("id-1"), headers=service_headers))
    assert created.json()["status"] == "awaiting_human"
    # 机器密钥不能人审
    denied = client.get("/runs/pending", headers=service_headers)
    assert denied.status_code == 401
    assert denied.json()["errors"] == ["review_token_missing"]
    # 旧式只带 REVIEW_API_KEY 也不行
    legacy = client.get("/runs/pending", headers=mint_headers)
    assert legacy.status_code == 401
    assert legacy.json()["errors"] == ["review_token_missing"]


def test_mint_jwt_then_approve_is_audited(service_headers, mint_headers, finish_run) -> None:
    client = TestClient(app)
    minted = client.post(
        "/auth/review/token",
        headers=mint_headers,
        json={"sub": "ada", "ttl_seconds": 600},
    )
    assert minted.status_code == 200
    token = minted.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    created = finish_run(client, client.post("/runs", json=_ticket("id-audit"), headers=service_headers))
    assert created.json()["awaiting_human"] is True
    pending = client.get("/runs/pending", headers=headers)
    assert pending.status_code == 200
    assert any(row["ticket_id"] == "id-audit" for row in pending.json())

    # 请求体伪造 reviewer 无效，以 JWT sub 为准
    done = client.post(
        "/runs/human",
        headers=headers,
        json={"ticket_id": "id-audit", "approved": True, "reviewer": "eve"},
    )
    assert done.status_code == 200
    assert done.json()["status"] == "resolved"
    stored = get_ticket("id-audit")
    assert stored is not None
    assert stored["history"][-1]["reviewer"] == "ada"

    events = list_audit(ticket_id="id-audit", limit=20)
    actions = {item["action"] for item in events}
    assert "review.read_drafts" in actions or any(
        item["action"] == "review.read_drafts" for item in list_audit(limit=50)
    )
    assert any(item["action"] == "review.approve" and item["actor"] == "ada" for item in events)
    assert any(item["trace_id"] == done.json()["trace_id"] for item in events if item["action"] == "review.approve")


def test_dsar_erase_removes_body(service_headers, review_headers, finish_run) -> None:
    client = TestClient(app)
    finish_run(client, client.post("/runs", json=_ticket("id-dsar"), headers=service_headers))
    assert get_ticket("id-dsar") is not None
    erased = client.delete("/privacy/tickets/id-dsar", headers=review_headers)
    assert erased.status_code == 200
    body = erased.json()
    assert body["deleted"] is True
    assert body["proof"]["ticket_gone"] is True
    assert body["proof"]["body_sha256"]
    assert get_ticket("id-dsar") is None
    assert assert_no_body_residue("id-dsar") == []


def test_encryption_roundtrip(monkeypatch, service_headers, finish_run) -> None:
    from ticket_triage.config import settings
    from ticket_triage.tools import tickets as tickets_mod

    monkeypatch.setattr(settings, "data_at_rest_key", "unit-test-at-rest-key")
    client = TestClient(app)
    finish_run(client, client.post("/runs", json=_ticket("id-enc"), headers=service_headers))
    # 库内应是密文前缀
    raw = None
    with tickets_mod._LOCK:
        conn = tickets_mod._connect()
        try:
            raw = conn.execute(
                "SELECT body FROM tickets WHERE ticket_id = ?",
                ("id-enc",),
            ).fetchone()[0]
        finally:
            conn.close()
    assert str(raw).startswith("enc:v1:")
    assert "DSAR-SECRET-BODY" not in str(raw)
    plain = get_ticket("id-enc")
    assert plain is not None
    assert "DSAR-SECRET-BODY" in plain["body"]
