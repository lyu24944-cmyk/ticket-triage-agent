"""P0.5：告警码、冷却、打满队列触发 Webhook。"""

from __future__ import annotations

import json
import logging

from ticket_triage.alerts import maybe_alert, reset_alert_state, set_post_hook
from ticket_triage.config import settings
from ticket_triage.graph.workflow import submit_triage
from ticket_triage.models import RunRequest, TicketIn
from ticket_triage.ops_log import format_event, log_event, resolve_alert_code


def test_resolve_alert_codes() -> None:
    assert resolve_alert_code("queue_full") == "queue_full"
    assert (
        resolve_alert_code("node_done", model_failed="classify_model_failed")
        == "classify_model_failed"
    )
    assert resolve_alert_code("node_done", tool_error="enrich_failed") == "enrich_failed"
    assert resolve_alert_code("accepted") == ""


def test_format_event_includes_model_failed_and_alert_code() -> None:
    line = format_event(
        "node_done",
        ticket_id="a1",
        node="classify",
        stub_fallback=True,
        model_failed="classify_model_failed",
        alert_code="classify_model_failed",
    )
    assert "model_failed=classify_model_failed" in line
    assert "alert_code=classify_model_failed" in line


def test_webhook_fires_and_cooldown(monkeypatch) -> None:
    reset_alert_state()
    monkeypatch.setattr(settings, "alert_webhook_url", "http://127.0.0.1:9/hook")
    monkeypatch.setattr(settings, "alert_email_to", None)
    monkeypatch.setattr(settings, "alert_smtp_host", None)
    monkeypatch.setattr(settings, "alert_cooldown_seconds", 60)
    sent: list[dict] = []
    set_post_hook(lambda payload: sent.append(payload))
    assert maybe_alert("queue_full", ticket_id="q1", queue_depth=32) is True
    assert maybe_alert("queue_full", ticket_id="q2", queue_depth=32) is False
    assert len(sent) == 1
    assert sent[0]["alert_code"] == "queue_full"
    assert sent[0]["ticket_id"] == "q1"
    reset_alert_state()


def test_log_event_json_and_alert(monkeypatch, caplog) -> None:
    reset_alert_state()
    monkeypatch.setattr(settings, "ops_log_json", True)
    monkeypatch.setattr(settings, "alert_webhook_url", "http://example.test/hook")
    monkeypatch.setattr(settings, "alert_cooldown_seconds", 0)
    sent: list[dict] = []
    set_post_hook(lambda payload: sent.append(payload))
    with caplog.at_level(logging.INFO, logger="ticket_triage.ops"):
        line = log_event(
            "node_done",
            ticket_id="m1",
            node="act",
            errors=["enrich_failed: timeout"],
        )
    payload = json.loads(line)
    assert payload["alert_code"] == "enrich_failed"
    assert payload["tool_error"] == "enrich_failed"
    assert sent and sent[0]["alert_code"] == "enrich_failed"
    reset_alert_state()
    monkeypatch.setattr(settings, "ops_log_json", False)


def test_queue_full_triggers_alert(monkeypatch) -> None:
    """故意打满队列：ops 记 queue_full，且 Webhook 收到告警。"""
    reset_alert_state()
    monkeypatch.setattr(settings, "run_queue_limit", 1)
    monkeypatch.setattr(settings, "run_workers", 1)
    monkeypatch.setattr(settings, "alert_webhook_url", "http://example.test/hook")
    monkeypatch.setattr(settings, "alert_cooldown_seconds", 0)
    sent: list[dict] = []
    set_post_hook(lambda payload: sent.append(payload))

    release = __import__("threading").Event()

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
    try:
        first = submit_triage(
            RunRequest(
                ticket=TicketIn(
                    ticket_id="alert-q-1",
                    subject="urgent refund",
                    body="I was charged twice and need a refund",
                )
            )
        )
        assert first.status == "running"
        second = submit_triage(
            RunRequest(
                ticket=TicketIn(
                    ticket_id="alert-q-2",
                    subject="urgent refund",
                    body="I was charged twice and need a refund",
                )
            )
        )
        assert second.status == "rejected"
        assert second.errors == ["queue_full"]
    finally:
        release.set()
        workflow.advance_run = original

    assert any(item.get("alert_code") == "queue_full" for item in sent)
    reset_alert_state()
