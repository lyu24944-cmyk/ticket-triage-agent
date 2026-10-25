"""上下文只保留工单、成功的工具结果、本次引用和上一步错误码。"""

from ticket_triage.llm.context import act_user, classify_user, draft_user, estimate_tokens
from ticket_triage.models import Category, Classification, Priority, TicketIn

TICKET = TicketIn(ticket_id="ctx-1", subject="紧急退款", body="我被重复扣款，需要退款")
BILLING = Classification(
    priority=Priority.high,
    category=Category.billing,
    rationale="test",
    needs_escalation=True,
)
HITS = [{"id": "kb-billing-refund", "title": "退款政策", "body": "14 天内退款。REF-14。"}]


def test_classify_keeps_subject_and_body() -> None:
    text = classify_user(TICKET)
    assert "紧急退款" in text
    assert "我被重复扣款，需要退款" in text
    assert "kb-billing-refund" not in text


def test_act_keeps_only_the_latest_error_code() -> None:
    calls = [
        {
            "name": "update_ticket_status",
            "arguments": {},
            "ok": False,
            "error": "tool_not_allowed:update_ticket_status",
        },
        {
            "name": "search_knowledge_base",
            "arguments": {"query": "退款"},
            "ok": False,
            "error": "enrich_failed: Server error 502 for http://127.0.0.1:8001/documents",
        },
    ]
    text = act_user(TICKET, BILLING, calls, HITS, [{"status": "open"}])
    assert "类别：billing" in text
    assert "上一步错误：enrich_failed" in text
    assert "502" not in text
    assert "tool_not_allowed" not in text
    assert "工具结果 search_knowledge_base" not in text
    assert "[kb-billing-refund]" in text


def test_successful_step_hides_older_errors() -> None:
    calls = [
        {
            "name": "search_knowledge_base",
            "arguments": {"query": "退款"},
            "ok": False,
            "error": "enrich_failed: timeout",
        },
        {
            "name": "search_knowledge_base",
            "arguments": {"query": "退款"},
            "ok": True,
            "error": None,
        },
    ]
    text = act_user(TICKET, BILLING, calls, HITS, None)
    assert "上一步错误" not in text
    assert "工具结果 search_knowledge_base：query=退款" in text


def test_over_budget_drops_earliest_tool_before_ticket_and_citation() -> None:
    calls = [
        {"name": "search_knowledge_base", "arguments": {"query": "第一段很长的检索词" * 8}, "ok": True, "error": None},
        {"name": "get_ticket_history", "arguments": {"ticket_id": "ctx-1"}, "ok": True, "error": None},
    ]
    history = [{"status": "open"}]
    full = act_user(TICKET, BILLING, calls, HITS, history, budget=10_000)
    assert "第一段很长的检索词" in full
    kept = act_user(TICKET, BILLING, calls[1:], HITS, history, budget=10_000)
    tight = estimate_tokens(kept)
    text = act_user(TICKET, BILLING, calls, HITS, history, budget=tight)
    assert "第一段很长的检索词" not in text
    assert "工具结果 get_ticket_history：open" in text
    assert "紧急退款" in text
    assert "[kb-billing-refund]" in text
    assert estimate_tokens(text) <= tight


def test_huge_ticket_is_shortened_after_citations() -> None:
    huge = TicketIn(ticket_id="ctx-2", subject="紧急退款", body="扣" * 400)
    text = classify_user(huge, budget=30)
    assert text.startswith("工单主题：紧急退款")
    assert "扣" * 400 not in text
    assert estimate_tokens(text) <= 30


def test_draft_lists_only_this_retrieval() -> None:
    calls = [
        {"name": "get_ticket_history", "arguments": {"ticket_id": "ctx-1"}, "ok": True, "error": None},
        {"name": "finish", "arguments": {}, "ok": True, "error": None},
    ]
    text = draft_user(TICKET, BILLING, HITS, calls, [{"status": "open", "note": "不要把备注送进去"}])
    assert "[kb-billing-refund]" in text
    assert "kb-tech-reset" not in text
    assert "工具结果 get_ticket_history：open" in text
    assert "不要把备注送进去" not in text
    assert "上一步错误" not in text
