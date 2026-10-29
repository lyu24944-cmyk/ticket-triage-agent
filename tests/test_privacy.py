"""送模型前脱敏与保留期限。证明敏感内容不会进提示词。"""

from datetime import datetime, timedelta, timezone

from ticket_triage.config import settings
from ticket_triage.llm.context import act_user, classify_user, draft_user
from ticket_triage.models import Category, Classification, HumanDecision, Priority, RunRequest, TicketIn
from ticket_triage.privacy import redact_text, ticket_for_model
from ticket_triage.runtime import load_run, save_pending, stage_running_job
from ticket_triage.tools import gateway
from ticket_triage.tools.tickets import get_ticket, purge_expired_tickets, seed_ticket


def _ticket() -> TicketIn:
    return TicketIn(
        ticket_id="priv-1",
        subject="urgent refund for alice@example.com",
        body=(
            "Charged twice. Call me at 138-1234-5678. "
            "Card 4111 1111 1111 1111. "
            "api_key=sk-abc123456789xyz. "
            "Bearer tok_secret_value_99"
        ),
        customer_email="alice@example.com",
        metadata={"ssn": "should-not-prompt"},
    )


def test_redact_text_strips_email_phone_card_secret() -> None:
    text = redact_text(_ticket().body)
    assert "alice@example.com" not in text
    assert "138-1234-5678" not in text
    assert "4111 1111 1111 1111" not in text
    assert "sk-abc123456789xyz" not in text
    assert "tok_secret_value_99" not in text
    assert "[EMAIL]" in text or "[PHONE]" in text
    assert "[CARD]" in text
    assert "[SECRET]" in text


def test_prompts_do_not_contain_raw_secrets_or_email_metadata() -> None:
    ticket = _ticket()
    classification = Classification(
        priority=Priority.high,
        category=Category.billing,
        rationale="billing",
    )
    history = [
        {"status": "open", "note": "已入库", "reviewer": "ada"},
        {"status": "escalated", "note": "secret note", "reviewer": "bea"},
    ]
    tool_calls = [
        {
            "name": "get_ticket_history",
            "arguments": {"ticket_id": ticket.ticket_id},
            "ok": True,
            "error": None,
        }
    ]
    classify = classify_user(ticket)
    act = act_user(ticket, classification, tool_calls, history=history)
    draft = draft_user(ticket, classification, [], tool_calls=tool_calls, history=history)
    for prompt in (classify, act, draft):
        assert "alice@example.com" not in prompt
        assert "138-1234-5678" not in prompt
        assert "4111 1111 1111 1111" not in prompt
        assert "sk-abc123456789xyz" not in prompt
        assert "should-not-prompt" not in prompt
        assert "reviewer" not in prompt
        assert "ada" not in prompt
        assert "bea" not in prompt
        assert "secret note" not in prompt
    assert "get_ticket_history：open,escalated" in act
    assert "get_ticket_history：open,escalated" in draft
    safe = ticket_for_model(ticket)
    assert safe.customer_email is None
    assert safe.metadata == {}


def test_reviewer_only_on_human_decision_not_run_request() -> None:
    assert "reviewer" not in RunRequest.model_fields
    assert "reviewer" in HumanDecision.model_fields


def test_sqlite_keeps_original_until_purge(monkeypatch) -> None:
    ticket = _ticket()
    seed_ticket(ticket.ticket_id, ticket.subject, ticket.body)
    stored = get_ticket(ticket.ticket_id)
    assert stored is not None
    assert "alice@example.com" in stored["subject"]
    assert "4111 1111 1111 1111" in stored["body"]

    monkeypatch.setattr(settings, "ticket_retention_days", 30)
    old = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    import sqlite3
    from ticket_triage.tools.tickets import _db_path

    conn = sqlite3.connect(_db_path())
    conn.execute("UPDATE tickets SET updated_at = ? WHERE ticket_id = ?", (old, ticket.ticket_id))
    conn.commit()
    conn.close()

    save_pending(
        ticket.ticket_id,
        "trace-priv-1",
        {"ticket": ticket, "trace_id": "trace-priv-1", "awaiting_human": True},
    )
    stage_running_job(
        trace_id="trace-priv-job",
        ticket_id=ticket.ticket_id,
        request={"ticket": ticket.model_dump(mode="json")},
        state=None,
        next_node="ingest",
    )
    purged = purge_expired_tickets(now=datetime.now(timezone.utc))
    assert ticket.ticket_id in purged
    assert get_ticket(ticket.ticket_id) is None
    assert load_run("trace-priv-job") is None
    assert gateway.get_ticket(ticket.ticket_id) is None
