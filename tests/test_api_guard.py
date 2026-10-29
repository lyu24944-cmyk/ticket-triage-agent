"""审核鉴权、入参上限、业务错误状态码和就绪检查。"""

import threading
import time

from fastapi.testclient import TestClient

from ticket_triage.api.main import app
from ticket_triage.config import settings


def _ticket(ticket_id: str = "guard-1", body: str = "我被重复扣款，需要退款") -> dict:
    return {
        "ticket": {"ticket_id": ticket_id, "subject": "紧急退款", "body": body},
    }


def test_review_routes_require_bearer_token(service_headers) -> None:
    client = TestClient(app)
    created = client.post("/runs", json=_ticket(), headers=service_headers)
    assert created.status_code == 202
    assert created.json()["status"] == "running"
    assert created.json()["trace_id"]

    assert client.get("/runs/pending").status_code == 401
    assert client.get("/runs/pending").json()["errors"] == ["review_token_missing"]
    assert client.get("/tickets").status_code == 401
    assert client.get(f"/traces/{created.json()['trace_id']}").status_code == 401
    denied = client.post("/runs/human", json={"ticket_id": "guard-1", "approved": True, "reviewer": "ada"})
    assert denied.status_code == 401
    assert denied.json()["errors"] == ["review_token_missing"]
    reopened = client.post("/tickets/guard-1/reopen", json={"reviewer": "ada"})
    assert reopened.status_code == 401


def test_missing_review_jwt_is_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "review_api_key", None)
    monkeypatch.setattr(settings, "review_api_key_file", None)
    monkeypatch.setattr(settings, "review_api_key_previous", None)
    monkeypatch.setattr(settings, "review_api_key_previous_file", None)
    monkeypatch.setattr(settings, "review_jwt_secret", None)
    client = TestClient(app)
    response = client.get("/runs/pending", headers={"Authorization": "Bearer x.y.z"})
    assert response.status_code == 401
    assert response.json()["errors"] == ["review_jwt_not_configured"]


def test_service_and_review_keys_are_not_interchangeable(service_headers, review_headers) -> None:
    client = TestClient(app)
    denied = client.post("/runs", json=_ticket("guard-swap"), headers=review_headers)
    assert denied.status_code == 401
    assert denied.json()["errors"] == ["service_auth_failed"]
    created = client.post("/runs", json=_ticket("guard-swap"), headers=service_headers)
    assert created.status_code == 202
    trace_id = created.json()["trace_id"]
    assert client.get(f"/runs/{trace_id}").status_code == 401
    reviewed = client.get(f"/runs/{trace_id}", headers=review_headers)
    assert reviewed.status_code == 401
    assert reviewed.json()["errors"] == ["service_auth_failed"]
    assert client.get("/runs/pending", headers=service_headers).status_code == 401
    assert client.get("/runs/pending", headers=service_headers).json()["errors"] == ["review_token_missing"]


def test_missing_service_key_is_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "service_api_key", None)
    monkeypatch.setattr(settings, "service_api_key_file", None)
    monkeypatch.setattr(settings, "service_api_key_previous", None)
    monkeypatch.setattr(settings, "service_api_key_previous_file", None)
    client = TestClient(app)
    response = client.post("/runs", json=_ticket("guard-no-service"))
    assert response.status_code == 401
    assert response.json()["errors"] == ["service_auth_not_configured"]


def test_client_cannot_skip_human_review(service_headers, finish_run) -> None:
    client = TestClient(app)
    payload = _ticket("guard-policy")
    payload["auto_approve"] = True
    result = finish_run(client, client.post("/runs", json=payload, headers=service_headers))
    assert result.status_code == 200
    assert result.json()["status"] == "awaiting_human"
    assert result.json()["awaiting_human"] is True


def test_closed_ticket_returns_409(monkeypatch, service_headers, finish_run) -> None:
    monkeypatch.setattr(settings, "require_hitl", False)
    client = TestClient(app)
    payload = _ticket("guard-closed")
    first = finish_run(client, client.post("/runs", json=payload, headers=service_headers))
    assert first.status_code == 200
    assert first.json()["status"] == "resolved"
    second = client.post("/runs", json=payload, headers=service_headers)
    assert second.status_code == 409
    assert second.json()["errors"] == ["already_closed"]


def test_missing_pending_run_returns_404(review_headers) -> None:
    client = TestClient(app)
    response = client.post(
        "/runs/human",
        headers=review_headers,
        json={"ticket_id": "missing-1", "approved": True, "reviewer": "ada"},
    )
    assert response.status_code == 404
    assert response.json()["errors"] == ["no_pending_run"]


def test_ticket_fields_are_bounded(service_headers) -> None:
    client = TestClient(app)
    huge = client.post("/runs", json=_ticket(body="退" * 4001), headers=service_headers)
    assert huge.status_code == 422
    bad_id = client.post("/runs", json=_ticket(ticket_id="bad id"), headers=service_headers)
    assert bad_id.status_code == 422
    nested = _ticket("guard-meta")
    nested["ticket"]["metadata"] = {"extra": {"nested": True}}
    assert client.post("/runs", json=nested, headers=service_headers).status_code == 422
    oversized = b'{"pad":"' + (b"a" * 70_000) + b'"}'
    blocked = client.post("/runs", content=oversized, headers={"Content-Type": "application/json"})
    assert blocked.status_code == 413
    assert blocked.json()["errors"] == ["payload_too_large"]
    assert blocked.json()["trace_id"]


def test_unhandled_error_hides_detail_and_returns_trace_id(monkeypatch, service_headers, finish_run) -> None:
    def boom(_job):
        raise RuntimeError("database password leaked")

    monkeypatch.setattr("ticket_triage.graph.workflow.advance_run", boom)
    client = TestClient(app)
    accepted = client.post("/runs", json=_ticket("guard-boom"), headers=service_headers)
    assert accepted.status_code == 202
    response = finish_run(client, accepted)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["errors"] == ["internal_error"]
    assert body["trace_id"] == accepted.json()["trace_id"]
    assert "password" not in response.text


def test_workflow_failure_uses_the_run_trace(monkeypatch, service_headers, finish_run) -> None:
    def explode(_state):
        raise RuntimeError("ingest broke")

    monkeypatch.setattr("ticket_triage.graph.workflow.ingest_node", explode)
    client = TestClient(app)
    accepted = client.post("/runs", json=_ticket("guard-trace"), headers=service_headers)
    response = finish_run(client, accepted)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["errors"] == ["internal_error"]
    assert "ingest broke" not in response.text
    from ticket_triage.graph.workflow import read_trace

    spans = read_trace(body["trace_id"])["spans"]
    assert spans[-1]["node"] == "ingest"
    assert spans[-1]["status"] == "error"


def test_health_checks_databases_and_skips_rag() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "llm_provider" not in body
    assert body["checks"]["runtime_db"]["ok"] is True
    assert body["checks"]["tickets_db"]["ok"] is True
    assert body["checks"]["rag"] == {"ok": True, "skipped": True}
    assert body["checks"]["single_process"]["ok"] is True
    assert body["checks"]["queue"]["ok"] is True
    assert body["checks"]["queue"]["workers"] == 2
    assert body["checks"]["queue"]["limit"] == 32
    assert body["checks"]["queue"]["active"] == 0


def test_unknown_run_returns_404(service_headers) -> None:
    client = TestClient(app)
    response = client.get("/runs/missing-trace", headers=service_headers)
    assert response.status_code == 404
    assert response.json()["errors"] == ["run_not_found"]


def test_second_post_reuses_the_running_trace(service_headers, finish_run) -> None:
    release = threading.Event()

    def blocked(job):
        release.wait(5)
        from ticket_triage.models import RunResult

        return RunResult(
            ticket_id=job["ticket_id"],
            status="awaiting_human",
            awaiting_human=True,
            trace_id=job["trace_id"],
        )

    from ticket_triage.graph import workflow

    original = workflow.advance_run
    workflow.advance_run = blocked
    client = TestClient(app)
    try:
        first = client.post("/runs", json=_ticket("guard-dup"), headers=service_headers)
        assert first.status_code == 202
        second = client.post("/runs", json=_ticket("guard-dup"), headers=service_headers)
        assert second.status_code == 202
        assert second.json()["trace_id"] == first.json()["trace_id"]
    finally:
        release.set()
        workflow.advance_run = original
    settled = finish_run(client, first)
    assert settled.json()["status"] == "awaiting_human"


def test_second_submit_returns_while_the_graph_is_still_running() -> None:
    """重复提交只查运行标记，不等分类和起草结束。"""
    from ticket_triage.graph import workflow
    from ticket_triage.models import RunRequest, TicketIn

    started = threading.Event()
    release = threading.Event()
    original = workflow.advance_run

    def slow(job):
        started.set()
        release.wait(3)
        return original(job)

    req = RunRequest(
        ticket=TicketIn(ticket_id="guard-lock", subject="紧急退款", body="我被重复扣款，需要退款"),
    )
    holder: dict = {}

    def first() -> None:
        holder["first"] = workflow.submit_triage(req)

    worker = threading.Thread(target=first)
    workflow.advance_run = slow
    second = None
    try:
        worker.start()
        assert started.wait(5)
        began = time.perf_counter()
        second = workflow.submit_triage(req)
        elapsed = time.perf_counter() - began
        assert elapsed < 1
        assert second.status == "running"
        assert second.trace_id
    finally:
        release.set()
        worker.join(5)
        workflow.advance_run = original
    assert holder["first"].trace_id == second.trace_id


def test_restart_marks_unfinished_run_failed() -> None:
    from ticket_triage.models import RunResult
    from ticket_triage.runtime import load_run, mark_interrupted_runs, save_run

    save_run(RunResult(ticket_id="left-1", status="running", trace_id="trace-left"))
    assert mark_interrupted_runs() == 1
    loaded = load_run("trace-left")
    assert loaded is not None
    assert loaded.status == "failed"
    assert loaded.errors == ["run_interrupted"]
    assert mark_interrupted_runs() == 0


def test_health_fails_when_rag_is_down(monkeypatch) -> None:
    monkeypatch.setattr(settings, "rag_base_url", "http://127.0.0.1:9")
    monkeypatch.setattr(settings, "rag_timeout_seconds", 0.2)
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 503
    rag = response.json()["checks"]["rag"]
    assert rag["ok"] is False
    assert rag["skipped"] is False
