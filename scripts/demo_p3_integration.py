"""P3 验收：模拟 Zendesk 进线 → 待审 → 结案回调；重复投递不双单。

用法（仓库根目录）::

    .\\.venv\\Scripts\\python.exe scripts/demo_p3_integration.py
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from ticket_triage.api.main import app  # noqa: E402
from ticket_triage.auth_jwt import mint_review_token  # noqa: E402
from ticket_triage.config import settings  # noqa: E402
from ticket_triage.graph.workflow import forget_memory  # noqa: E402
from ticket_triage.secrets import clear_secret_cache  # noqa: E402
from ticket_triage.tenants import clear_tenant_cache  # noqa: E402
from ticket_triage.tools import tickets as tickets_mod  # noqa: E402


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="p3-demo-"))
    settings.ticket_db_path = str(tmp / "tickets.db")
    settings.runtime_db_path = str(tmp / "runtime.db")
    settings.service_api_key = "demo-service"
    settings.review_api_key = "demo-review"
    settings.review_jwt_secret = "demo-jwt"
    settings.webhook_signing_secret = "demo-webhook"
    settings.tenants_json = None
    settings.require_hitl = True
    settings.llm_provider = "stub"
    settings.closure_callback_url = "http://127.0.0.1:9/cb"
    clear_secret_cache()
    clear_tenant_cache()
    forget_memory()

    callbacks: list[dict] = []

    def capture(url: str, body: bytes, headers: dict[str, str]) -> None:
        payload = json.loads(body.decode("utf-8"))
        callbacks.append(payload)
        print(f"[callback] {payload} headers={sorted(headers)}")

    import ticket_triage.integrations.callback as cb

    cb._http_post = capture  # type: ignore[method-assign]

    client = TestClient(app)
    payload = {
        "id": 9001,
        "subject": "紧急退款",
        "description": "我被重复扣款，需要退款",
        "requester_email": "c@example.com",
    }
    body = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Zendesk-Webhook-Signature": _sign(settings.webhook_signing_secret, body),
        "X-Zendesk-Delivery-Id": "demo-delivery-9001",
    }
    first = client.post("/v1/webhooks/zendesk", content=body, headers=headers)
    print("inbound", first.status_code, first.json())
    assert first.status_code == 202, first.text
    ticket_id = first.json()["ticket_id"]
    trace_id = first.json()["trace_id"]

    second = client.post("/v1/webhooks/zendesk", content=body, headers=headers)
    print("replay", second.status_code, second.json().get("idempotent_replay"))
    assert second.json()["idempotent_replay"] is True
    assert second.json()["trace_id"] == trace_id

    service_headers = {"X-API-Key": "demo-service"}
    deadline = time.monotonic() + 10
    status = "running"
    while time.monotonic() < deadline:
        polled = client.get(f"/v1/runs/{trace_id}", headers=service_headers)
        status = polled.json().get("status")
        if status == "awaiting_human":
            break
        if status in {"failed", "resolved", "escalated", "rejected"}:
            break
        time.sleep(0.05)
    print("run_status", status)
    assert status == "awaiting_human", polled.text

    token = mint_review_token("demo-reviewer")
    review_headers = {"Authorization": f"Bearer {token}"}
    items = client.get("/v1/runs/pending", headers=review_headers).json()["items"]
    pending = next((i for i in items if i["ticket_id"] == ticket_id), None)
    assert pending, items
    print("pending", pending["ticket_id"], pending.get("category"))

    closed = client.post(
        "/v1/runs/human",
        json={"ticket_id": ticket_id, "approved": True},
        headers=review_headers,
    )
    print("closed", closed.status_code, closed.json().get("status"))
    assert closed.json()["status"] == "resolved"

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not callbacks:
        time.sleep(0.05)
    assert callbacks and callbacks[0]["status"] == "resolved"
    assert tickets_mod.get_ticket(ticket_id)["status"] == "resolved"
    print("OK: inbound → pending → resolved; duplicate delivery did not create a second run")
    print(f"demo db: {tmp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
