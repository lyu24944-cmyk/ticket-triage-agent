"""同一张工单再提交时，不覆盖待审草稿，也不抹掉已结案历史。"""

from ticket_triage.graph.workflow import forget_memory, run_triage
from ticket_triage.models import RunRequest, TicketIn
from ticket_triage.tools.tickets import get_ticket, seed_ticket


def _request(ticket_id: str, *, auto_approve: bool) -> RunRequest:
    return RunRequest(
        ticket=TicketIn(ticket_id=ticket_id, subject="紧急退款", body="我被重复扣款，需要退款"),
        auto_approve=auto_approve,
    )


def test_second_submit_keeps_pending_draft_and_history() -> None:
    first = run_triage(_request("mem-pending", auto_approve=False))
    assert first.awaiting_human is True
    history = [item["status"] for item in get_ticket("mem-pending")["history"]]
    assert history == ["open"]

    forget_memory()
    second = run_triage(
        RunRequest(
            ticket=TicketIn(ticket_id="mem-pending", subject="换成别的主题", body="别的正文"),
            auto_approve=True,
        )
    )
    assert second.awaiting_human is True
    assert second.trace_id == first.trace_id
    assert second.draft is not None and first.draft is not None
    assert second.draft.body == first.draft.body
    assert [item["status"] for item in get_ticket("mem-pending")["history"]] == ["open"]
    assert get_ticket("mem-pending")["subject"] == "紧急退款"


def test_submit_after_resolve_keeps_resolved_history() -> None:
    first = run_triage(_request("mem-closed", auto_approve=True))
    assert first.status == "resolved"
    before = get_ticket("mem-closed")["history"]

    second = run_triage(_request("mem-closed", auto_approve=True))
    assert second.status == "resolved"
    assert second.errors == ["already_closed"]
    assert second.trace_id is None
    assert get_ticket("mem-closed")["history"] == before
    assert [item["status"] for item in before] == ["open", "resolved"]


def test_seed_existing_ticket_does_not_reset_history() -> None:
    seed_ticket("mem-seed", "紧急退款", "需要退款")
    from ticket_triage.tools.tickets import update_ticket_status

    update_ticket_status("mem-seed", "escalated", "已升级")
    seed_ticket("mem-seed", "新主题", "新正文")
    stored = get_ticket("mem-seed")
    assert stored["status"] == "escalated"
    assert stored["subject"] == "紧急退款"
    assert [item["status"] for item in stored["history"]] == ["open", "escalated"]
