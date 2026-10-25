"""审核鉴权、入参上限、业务错误状态码和就绪检查。"""

import threading

from fastapi.testclient import TestClient

from ticket_triage.api.main import app
from ticket_triage.config import settings


def _ticket(ticket_id: str = "guard-1", body: str = "我被重复扣款，需要退款") -> dict:
    return {
        "auto_approve": False,
        "ticket": {"ticket_id": ticket_id, "subject": "紧急退款", "body": body},
    }


def test_review_routes_require_api_key() -> None:
    client = TestClient(app)
    created = client.post("/runs", json=_ticket())
    assert created.status_code == 202
    assert created.json()["status"] == "running"
    assert created.json()["trace_id"]

    assert client.get("/runs/pending").status_code == 401
    assert client.get("/tickets").status_code == 401
    assert client.get(f"/traces/{created.json()['trace_id']}").status_code == 401
    denied = client.post("/runs/human", json={"ticket_id": "guard-1", "approved": True, "reviewer": "ada"})
    assert denied.status_code == 401
    assert denied.json()["errors"] == ["review_auth_failed"]
    reopened = client.post("/tickets/guard-1/reopen", json={"reviewer": "ada"})
    assert reopened.status_code == 401


def test_missing_review_key_is_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "review_api_key", None)
    client = TestClient(app)
    response = client.get("/runs/pending")
    assert response.status_code == 401
    assert response.json()["errors"] == ["review_auth_not_configured"]


def test_closed_ticket_returns_409(review_headers, finish_run) -> None:
    client = TestClient(app)
    payload = _ticket("guard-closed")
    payload["auto_approve"] = True
    first = finish_run(client, client.post("/runs", json=payload))
    assert first.status_code == 200
    assert first.json()["status"] == "resolved"
    second = client.post("/runs", json=payload)
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


def test_ticket_fields_are_bounded() -> None:
    client = TestClient(app)
    huge = client.post("/runs", json=_ticket(body="退" * 4001))
    assert huge.status_code == 422
    bad_id = client.post("/runs", json=_ticket(ticket_id="bad id"))
    assert bad_id.status_code == 422
    nested = _ticket("guard-meta")
    nested["ticket"]["metadata"] = {"extra": {"nested": True}}
    assert client.post("/runs", json=nested).status_code == 422
    oversized = b'{"pad":"' + (b"a" * 70_000) + b'"}'
    blocked = client.post("/runs", content=oversized, headers={"Content-Type": "application/json"})
    assert blocked.status_code == 413
    assert blocked.json()["errors"] == ["payload_too_large"]
    assert blocked.json()["trace_id"]


def test_unhandled_error_hides_detail_and_returns_trace_id(monkeypatch, finish_run) -> None:
    def boom(_req, **_kwargs):
        raise RuntimeError("database password leaked")

    monkeypatch.setattr("ticket_triage.graph.workflow.run_triage", boom)
    client = TestClient(app)
    accepted = client.post("/runs", json=_ticket("guard-boom"))
    assert accepted.status_code == 202
    response = finish_run(client, accepted)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["errors"] == ["internal_error"]
    assert body["trace_id"] == accepted.json()["trace_id"]
    assert "password" not in response.text


def test_workflow_failure_uses_the_run_trace(monkeypatch, finish_run) -> None:
    def explode(_state):
        raise RuntimeError("ingest broke")

    monkeypatch.setattr("ticket_triage.graph.workflow.ingest_node", explode)
    client = TestClient(app)
    accepted = client.post("/runs", json=_ticket("guard-trace"))
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


def test_unknown_run_returns_404() -> None:
    client = TestClient(app)
    response = client.get("/runs/missing-trace")
    assert response.status_code == 404
    assert response.json()["errors"] == ["run_not_found"]


def test_second_post_reuses_the_running_trace(finish_run) -> None:
    release = threading.Event()

    def blocked(req, trace_id=None):
        release.wait(5)
        from ticket_triage.models import RunResult

        return RunResult(
            ticket_id=req.ticket.ticket_id,
            status="awaiting_human",
            awaiting_human=True,
            trace_id=trace_id,
        )

    from ticket_triage.graph import workflow

    original = workflow.run_triage
    workflow.run_triage = blocked
    client = TestClient(app)
    try:
        first = client.post("/runs", json=_ticket("guard-dup"))
        assert first.status_code == 202
        second = client.post("/runs", json=_ticket("guard-dup"))
        assert second.status_code == 202
        assert second.json()["trace_id"] == first.json()["trace_id"]
    finally:
        release.set()
        workflow.run_triage = original
    settled = finish_run(client, first)
    assert settled.json()["status"] == "awaiting_human"


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
