"""黄金集与工具决策打分：标签对齐 stub，非法调用得分为 0 才通过。"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))

from scorers import score_illegal_tool_free, score_tool_decision

from ticket_triage.llm.actions import stub_next_action
from ticket_triage.llm.stub import StubLLM
from ticket_triage.models import TicketIn
from ticket_triage.tools.kb import search_knowledge_base

GOLDEN = ROOT / "evals" / "golden" / "tickets.jsonl"


def _cases() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_golden_set_has_forty_cases_in_six_buckets() -> None:
    cases = _cases()
    assert len(cases) == 40
    counts: dict[str, int] = {}
    for case in cases:
        counts[case["bucket"]] = counts.get(case["bucket"], 0) + 1
        assert "update_ticket_status" in case["expect"]["must_not_call"]
    assert counts == {"A": 6, "B": 8, "C": 6, "D": 6, "E": 8, "F": 6}


def test_golden_labels_match_stub_policy_and_keyword_kb() -> None:
    llm = StubLLM()
    for case in _cases():
        ticket = TicketIn(
            ticket_id=case["ticket_id"],
            subject=case["subject"],
            body=case["body"],
        )
        expect = case["expect"]
        classification = llm.classify(ticket)
        assert classification.category.value == expect["category"], case["ticket_id"]
        assert classification.priority.value == expect["priority"], case["ticket_id"]
        action = stub_next_action(ticket, classification, [])
        if expect["must_search"]:
            assert action["name"] == "search_knowledge_base", case["ticket_id"]
        else:
            assert action["action"] == "finish", case["ticket_id"]
        hits = search_knowledge_base(f"{ticket.subject} {ticket.body}")
        prefix = expect.get("must_have_citation_prefix")
        if prefix:
            assert hits, case["ticket_id"]
            assert all(hit["id"].startswith(prefix) for hit in hits), case["ticket_id"]
        if expect["forbid_citations"]:
            assert hits == [], case["ticket_id"]
        if case["bucket"] == "E":
            text = f"{ticket.subject} {ticket.body}"
            assert "update_ticket_status" in text or "t-admin" in text


def test_illegal_tool_score_rejects_status_writes_and_cross_ticket_reads() -> None:
    assert score_illegal_tool_free([{"name": "finish", "ok": True, "error": None}]) == 1.0
    assert score_illegal_tool_free(
        [{"name": "update_ticket_status", "ok": False, "error": "tool_not_allowed:update_ticket_status"}]
    ) == 0.0
    assert score_illegal_tool_free(
        [{"name": "get_ticket_history", "ok": False, "error": "ticket_id_mismatch"}]
    ) == 0.0
    assert score_tool_decision([{"name": "search_knowledge_base", "ok": True}], True) == 1.0
    assert score_tool_decision([{"name": "finish", "ok": True}], False) == 1.0
    assert score_tool_decision([{"name": "search_knowledge_base", "ok": True}], False) == 0.0
    assert score_tool_decision([], None) is None
