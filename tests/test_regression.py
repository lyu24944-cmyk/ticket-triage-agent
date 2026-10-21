"""P3 回归：空查询与无关文本不得伪造知识库引用。"""

from ticket_triage.graph.workflow import run_triage
from ticket_triage.models import RunRequest, TicketIn
from ticket_triage.tools.kb import search_knowledge_base


def test_empty_query_returns_no_articles():
    assert search_knowledge_base("") == []
    assert search_knowledge_base("   ") == []


def test_unrelated_text_returns_no_articles():
    assert search_knowledge_base("今天天气不错") == []


def test_chinese_refund_cites_billing_article():
    result = run_triage(
        RunRequest(
            ticket=TicketIn(
                ticket_id="reg-zh-refund",
                subject="紧急退款",
                body="我被重复扣款，需要退款",
            ),
            auto_approve=True,
        )
    )
    assert result.draft is not None
    assert any(c.startswith("kb-billing") for c in result.draft.citations)


def test_smalltalk_has_no_citation():
    result = run_triage(
        RunRequest(
            ticket=TicketIn(
                ticket_id="reg-smalltalk",
                subject="Hello",
                body="Just saying hi",
            ),
            auto_approve=True,
        )
    )
    assert result.draft is not None
    assert result.draft.citations == []
