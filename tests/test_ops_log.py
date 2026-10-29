"""值班日志字段格式。不作为评测门禁，只锁 key=value 形状。"""

import logging

from ticket_triage.graph.workflow import submit_triage
from ticket_triage.models import RunRequest, TicketIn
from ticket_triage.ops_log import (
    format_event,
    stub_fallback_from_errors,
    tool_error_from_errors,
)


def test_format_event_always_emits_fixed_keys() -> None:
    line = format_event(
        "node_done",
        trace_id="t-1",
        ticket_id="k-1",
        node="classify",
        queue_depth=3,
        stub_fallback=True,
        tool_error="",
        status="classified",
    )
    assert "event=node_done" in line
    assert "trace_id=t-1" in line
    assert "ticket_id=k-1" in line
    assert "node=classify" in line
    assert "queue_depth=3" in line
    assert "stub_fallback=1" in line
    assert "tool_error=-" in line
    assert "model_failed=-" in line
    assert "alert_code=-" in line
    assert "status=classified" in line


def test_stub_and_tool_codes_from_errors() -> None:
    assert stub_fallback_from_errors(["classify_model_failed: timeout"]) is True
    assert stub_fallback_from_errors(["act_model_failed: bad json"]) is True
    assert stub_fallback_from_errors(["draft_model_failed: boom"]) is True
    assert stub_fallback_from_errors(["enrich_failed: timeout"]) is False
    assert tool_error_from_errors(["enrich_failed: timeout"]) == "enrich_failed"
    assert tool_error_from_errors(["classify_model_failed: x"]) == ""


def test_queue_full_event_shape() -> None:
    line = format_event(
        "queue_full",
        ticket_id="q-1",
        queue_depth=32,
        stub_fallback=False,
        tool_error="",
        limit=32,
    )
    assert line.startswith("event=queue_full")
    assert "ticket_id=q-1" in line
    assert "queue_depth=32" in line
    assert "limit=32" in line


def test_http_run_emits_ops_fields(caplog) -> None:
    """受理跑通时日志带固定键；此测试不检查退回比例，不作评测门禁。"""
    with caplog.at_level(logging.INFO, logger="ticket_triage.ops"):
        accepted = submit_triage(
            RunRequest(
                ticket=TicketIn(
                    ticket_id="ops-log-1",
                    subject="urgent refund",
                    body="I was charged twice and need a refund",
                )
            )
        )
        from ticket_triage.runtime import load_run
        import time

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            loaded = load_run(accepted.trace_id or "")
            if loaded is not None and loaded.status != "running":
                break
            time.sleep(0.02)
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "event=accepted" in text
    assert "event=node_done" in text
    assert "trace_id=" in text
    assert "ticket_id=ops-log-1" in text
    assert "node=classify" in text
    assert "queue_depth=" in text
    assert "stub_fallback=" in text
    assert "tool_error=" in text
