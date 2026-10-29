"""测试夹具：隔离 SQLite 路径；审核用人员 JWT。"""

from __future__ import annotations

import time

import pytest

from ticket_triage.auth_jwt import mint_review_token
from ticket_triage.config import settings
from ticket_triage.graph.workflow import drain_jobs, forget_memory
from ticket_triage.process_lock import acquire_single_process_lock

REVIEW_API_KEY = "test-review-key"
SERVICE_API_KEY = "test-service-key"
REVIEW_JWT_SECRET = "test-review-jwt-secret"
REVIEWER_SUB = "ada"


@pytest.fixture(autouse=True)
def _isolate_ticket_db(tmp_path, monkeypatch):
    db = tmp_path / "tickets.db"
    monkeypatch.setattr(settings, "ticket_db_path", str(db))
    monkeypatch.setattr(settings, "runtime_db_path", str(tmp_path / "runtime.db"))
    monkeypatch.setattr(settings, "use_mcp", False)
    monkeypatch.setattr(settings, "rag_base_url", None)
    monkeypatch.setattr(settings, "llm_provider", "stub")
    monkeypatch.setattr(settings, "review_api_key", REVIEW_API_KEY)
    monkeypatch.setattr(settings, "service_api_key", SERVICE_API_KEY)
    monkeypatch.setattr(settings, "review_jwt_secret", REVIEW_JWT_SECRET)
    monkeypatch.setattr(settings, "review_oidc_issuer", None)
    monkeypatch.setattr(settings, "review_oidc_jwks_url", None)
    monkeypatch.setattr(settings, "data_at_rest_key", None)
    monkeypatch.setattr(settings, "langfuse_public_key", None)
    monkeypatch.setattr(settings, "langfuse_secret_key", None)
    monkeypatch.setattr(settings, "tenants_json", None)
    monkeypatch.setattr(settings, "webhook_signing_secret", "test-webhook-secret")
    monkeypatch.setattr(settings, "closure_callback_url", None)
    from ticket_triage.secrets import clear_secret_cache
    from ticket_triage.tenants import clear_tenant_cache

    clear_secret_cache()
    clear_tenant_cache()
    forget_memory()
    acquire_single_process_lock()
    yield
    drain_jobs()
    clear_tenant_cache()
    clear_secret_cache()


def _finish_run(client, response, timeout: float = 5):
    """新运行是 202。轮询到不再是 running 再把最终响应交给断言。"""
    if response.status_code != 202:
        return response
    trace_id = response.json()["trace_id"]
    deadline = time.monotonic() + timeout
    last = response
    while time.monotonic() < deadline:
        last = client.get(f"/runs/{trace_id}", headers={"X-API-Key": SERVICE_API_KEY})
        if last.status_code != 200 or last.json().get("status") != "running":
            return last
        time.sleep(0.02)
    raise AssertionError(f"运行没有结束: {last.text}")


@pytest.fixture
def finish_run():
    return _finish_run


@pytest.fixture
def review_headers() -> dict[str, str]:
    token = mint_review_token(REVIEWER_SUB)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mint_headers() -> dict[str, str]:
    return {"X-API-Key": REVIEW_API_KEY}


@pytest.fixture
def service_headers() -> dict[str, str]:
    return {"X-API-Key": SERVICE_API_KEY}
