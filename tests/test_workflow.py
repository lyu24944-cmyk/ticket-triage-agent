"""工作流单元测试（无需 API Key）。"""

from ticket_triage.graph.workflow import resume_with_human, run_triage
from ticket_triage.models import HumanDecision, RunRequest, TicketIn


def test_billing_ticket_awaits_human():
    """账单类紧急工单应进入人审，并带 KB 引用。"""
    req = RunRequest(
        ticket=TicketIn(
            ticket_id="t-100",
            subject="Urgent refund request",
            body="I was charged twice, need a refund now.",
        ),
        auto_approve=False,
    )
    result = run_triage(req)
    assert result.status == "awaiting_human"
    assert result.awaiting_human is True
    assert result.classification is not None
    assert result.classification.category.value == "billing"
    assert result.draft is not None
    assert result.draft.citations


def test_auto_approve_resolves():
    """自动通过时应直接结案。"""
    req = RunRequest(
        ticket=TicketIn(
            ticket_id="t-101",
            subject="Password reset help",
            body="I cannot login and need a password reset.",
        ),
        auto_approve=True,
    )
    result = run_triage(req)
    assert result.status == "resolved"
    assert result.awaiting_human is False


def test_human_reject_escalates():
    """人审驳回应升级而非静默丢弃。"""
    run_triage(
        RunRequest(
            ticket=TicketIn(
                ticket_id="t-102",
                subject="Billing issue",
                body="Wrong invoice charge",
            ),
            auto_approve=False,
        )
    )
    result = resume_with_human(
        HumanDecision(ticket_id="t-102", approved=False, note="needs senior review")
    )
    assert result.status == "escalated"
