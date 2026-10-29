"""OIDC 替换 mint：无 IdP 令牌不能人审；验签与 sub 口径不变。"""

from __future__ import annotations

from typing import Any

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from ticket_triage.api.main import app
from ticket_triage.auth_jwt import (
    clear_oidc_jwks_cache,
    jwk_from_private_key,
    mint_oidc_test_token,
    mint_review_token,
    review_auth_mode,
)
from ticket_triage.config import settings
from ticket_triage.tools.tickets import get_ticket


def _ticket(ticket_id: str) -> dict:
    return {
        "ticket": {
            "ticket_id": ticket_id,
            "subject": "紧急退款",
            "body": "我被重复扣款，需要退款",
        },
    }


def _enable_oidc(monkeypatch, private_key, *, issuer: str = "https://idp.test") -> dict[str, Any]:
    jwk = jwk_from_private_key(private_key, kid="test-kid")
    jwks = {"keys": [jwk]}

    def fake_get(url: str, timeout: float = 5.0):  # noqa: ARG001
        class _Resp:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return jwks

        return _Resp()

    monkeypatch.setattr(settings, "review_oidc_issuer", issuer)
    monkeypatch.setattr(settings, "review_oidc_audience", "ticket-triage-review")
    monkeypatch.setattr(settings, "review_oidc_jwks_url", f"{issuer}/.well-known/jwks.json")
    monkeypatch.setattr("ticket_triage.auth_jwt.httpx.get", fake_get)
    clear_oidc_jwks_cache()
    return jwks


def test_review_mode_switches_with_oidc(monkeypatch) -> None:
    assert review_auth_mode() == "mint"
    client = TestClient(app)
    assert client.get("/auth/review/mode").json()["mode"] == "mint"

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _enable_oidc(monkeypatch, key)
    assert review_auth_mode() == "oidc"
    body = client.get("/auth/review/mode").json()
    assert body["mode"] == "oidc"
    assert body["oidc_issuer"] == "https://idp.test"


def test_oidc_disables_mint_and_rejects_without_idp_token(
    monkeypatch, service_headers, mint_headers, finish_run
) -> None:
    from ticket_triage.auth_jwt import ReviewTokenError, oidc_enabled

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _enable_oidc(monkeypatch, key)
    client = TestClient(app)

    minted = client.post(
        "/auth/review/token",
        headers=mint_headers,
        json={"sub": "ada", "ttl_seconds": 600},
    )
    assert minted.status_code == 401
    assert minted.json()["errors"] == ["review_mint_disabled"]
    assert oidc_enabled()
    try:
        mint_review_token("ada")
        assert False, "mint should be disabled"
    except ReviewTokenError as exc:
        assert str(exc) == "review_mint_disabled"

    created = finish_run(
        client, client.post("/runs", json=_ticket("oidc-1"), headers=service_headers)
    )
    assert created.json()["status"] == "awaiting_human"
    denied = client.get("/runs/pending")
    assert denied.status_code == 401
    assert denied.json()["errors"] == ["review_token_missing"]
    # 伪造 / 非 IdP 令牌也不能人审
    bad = client.get("/runs/pending", headers={"Authorization": "Bearer not-an-idp-jwt"})
    assert bad.status_code == 401


def test_oidc_token_sub_is_reviewer(monkeypatch, service_headers, finish_run) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _enable_oidc(monkeypatch, key)
    token = mint_oidc_test_token(
        private_key=key, subject="ada", kid="test-kid", tenant_id="default"
    )
    headers = {"Authorization": f"Bearer {token}"}
    client = TestClient(app)

    created = finish_run(
        client, client.post("/runs", json=_ticket("oidc-2"), headers=service_headers)
    )
    assert created.json()["awaiting_human"] is True
    pending = client.get("/runs/pending", headers=headers)
    assert pending.status_code == 200
    assert any(row["ticket_id"] == "oidc-2" for row in pending.json())

    done = client.post(
        "/runs/human",
        headers=headers,
        json={"ticket_id": "oidc-2", "approved": True, "reviewer": "eve"},
    )
    assert done.status_code == 200
    assert done.json()["status"] == "resolved"
    stored = get_ticket("oidc-2")
    assert stored is not None
    assert stored["history"][-1]["reviewer"] == "ada"


def test_v1_oidc_mint_disabled(monkeypatch, mint_headers) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _enable_oidc(monkeypatch, key)
    client = TestClient(app)
    resp = client.post(
        "/v1/auth/review/token",
        headers=mint_headers,
        json={"sub": "ada"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "review_mint_disabled"
    mode = client.get("/v1/auth/review/mode").json()
    assert mode["mode"] == "oidc"
