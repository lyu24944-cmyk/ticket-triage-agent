"""P5：重启后仍能人审，并且能按节点回放追踪。"""

from ticket_triage.graph.workflow import forget_memory, read_trace, resume_with_human, run_triage
from ticket_triage.models import HumanDecision, RunRequest, TicketIn


def test_hitl_survives_process_restart():
    created = run_triage(
        RunRequest(
            ticket=TicketIn(
                ticket_id="p5-1",
                subject="紧急退款",
                body="我被重复扣款，需要退款",
            ),
            auto_approve=False,
        )
    )
    assert created.status == "awaiting_human"
    assert created.trace_id

    forget_memory()

    done = resume_with_human(HumanDecision(ticket_id="p5-1", approved=True))
    assert done.status == "resolved"
    assert done.trace_id == created.trace_id

    trace = read_trace(created.trace_id)
    names = [span["node"] for span in trace["spans"]]
    assert "classify" in names
    assert "finalize" in names
    assert trace["total_ms"] >= 0
    assert trace["over_budget"] is False
