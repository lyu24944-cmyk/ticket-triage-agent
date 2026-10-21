"""P6：分流在检索步骤使用问答服务的引用，服务不可用时软降级。"""

import httpx

from ticket_triage.config import settings
from ticket_triage.graph.workflow import run_triage
from ticket_triage.models import RunRequest, TicketIn
from ticket_triage.tools import gateway


def test_refund_ticket_cites_rag_document(monkeypatch) -> None:
    monkeypatch.setattr(settings, "rag_base_url", "http://127.0.0.1:8001")

    def fake(query: str, limit: int = 3) -> list[dict[str, str]]:
        assert "refund" in query.lower()
        return [{"id": "kb-billing-refund", "title": "退款政策", "body": "14 天内退款。"}]

    monkeypatch.setattr("ticket_triage.tools.rag_client.search_knowledge_base", fake)
    result = run_triage(
        RunRequest(
            ticket=TicketIn(
                ticket_id="p6-refund",
                subject="Urgent refund request",
                body="I was charged twice, need a refund",
            ),
            auto_approve=True,
        )
    )
    assert result.status == "resolved"
    assert result.draft is not None
    assert result.draft.citations == ["kb-billing-refund"]


def test_smalltalk_has_empty_rag_citations(monkeypatch) -> None:
    monkeypatch.setattr(settings, "rag_base_url", "http://127.0.0.1:8001")

    def fake(query: str, limit: int = 3) -> list[dict[str, str]]:
        return []

    monkeypatch.setattr("ticket_triage.tools.rag_client.search_knowledge_base", fake)
    result = run_triage(
        RunRequest(
            ticket=TicketIn(ticket_id="p6-hi", subject="Hello", body="Just saying hi"),
            auto_approve=True,
        )
    )
    assert result.draft is not None
    assert result.draft.citations == []


def test_rag_outage_soft_degrades_to_draft(monkeypatch) -> None:
    monkeypatch.setattr(settings, "rag_base_url", "http://127.0.0.1:8001")
    monkeypatch.setattr(settings, "max_tool_retries", 0)

    def boom(query: str, limit: int = 3) -> list[dict[str, str]]:
        raise httpx.ConnectError("rag down")

    monkeypatch.setattr(gateway, "search_knowledge_base", boom)
    result = run_triage(
        RunRequest(
            ticket=TicketIn(
                ticket_id="p6-down",
                subject="Urgent refund request",
                body="I was charged twice, need a refund",
            ),
            auto_approve=True,
        )
    )
    assert result.status == "resolved"
    assert result.draft is not None
    assert result.draft.citations == []
    assert any(item.startswith("enrich_failed") for item in result.errors)
