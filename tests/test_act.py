"""act 节点：stub 策略、白名单拒绝、步数上限。"""

from ticket_triage.graph.workflow import run_triage
from ticket_triage.llm import ModelLLM, StubLLM
from ticket_triage.llm.actions import parse_action, stub_next_action
from ticket_triage.models import Category, Classification, Priority, RunRequest, TicketIn
from ticket_triage.tools import gateway


def _run(ticket_id: str, subject: str, body: str):
    return run_triage(
        RunRequest(
            ticket=TicketIn(ticket_id=ticket_id, subject=subject, body=body),
        )
    )


def _names(result) -> list[str]:
    return [call["name"] for call in result.tool_calls]


class _Scripted:
    """按脚本返回动作，分类和起草仍用 stub。"""

    def __init__(self, actions: list[dict]) -> None:
        self._actions = list(actions)
        self._stub = StubLLM()

    def classify(self, ticket):
        return self._stub.classify(ticket)

    def draft_reply(self, ticket, classification, kb_hits, **_kwargs):
        return self._stub.draft_reply(ticket, classification, kb_hits)

    def next_action(self, ticket, classification, tool_calls, **_kwargs):
        if self._actions:
            return self._actions.pop(0)
        return {"action": "finish"}


def test_other_ticket_finishes_without_search(monkeypatch) -> None:
    calls = {"n": 0}

    def boom(*_args, **_kwargs):
        calls["n"] += 1
        raise AssertionError("other 不应检索")

    monkeypatch.setattr("ticket_triage.graph.nodes.gateway.search_knowledge_base", boom)
    result = _run("act-other", "Hello", "Just saying hi")
    assert result.status == "awaiting_human"
    assert _names(result) == ["finish"]
    assert result.draft is not None
    assert result.draft.citations == []
    assert calls["n"] == 0


def test_billing_searches_then_reads_history() -> None:
    result = _run("act-bill", "Urgent refund", "I was charged twice, need a refund")
    assert _names(result) == ["search_knowledge_base", "get_ticket_history", "finish"]
    assert all(call["ok"] for call in result.tool_calls)
    assert result.draft is not None
    assert any(item.startswith("kb-billing") for item in result.draft.citations)


def test_second_search_keeps_earlier_hits(monkeypatch) -> None:
    scripted = _Scripted(
        [
            {
                "action": "tool",
                "name": "search_knowledge_base",
                "arguments": {"query": "refund"},
            },
            {
                "action": "tool",
                "name": "search_knowledge_base",
                "arguments": {"query": "password"},
            },
            {"action": "finish"},
        ]
    )
    monkeypatch.setattr("ticket_triage.graph.nodes.get_llm", lambda: scripted)
    result = _run("act-merge", "Invoice", "refund and password")
    assert result.draft is not None
    assert "kb-billing-refund" in result.draft.citations
    assert "kb-tech-reset" in result.draft.citations


def test_duplicate_search_hits_gateway_once(monkeypatch) -> None:
    calls = {"n": 0}
    real = gateway.search_knowledge_base

    def counting(query: str, limit: int = 3):
        calls["n"] += 1
        return real(query, limit=limit)

    monkeypatch.setattr("ticket_triage.graph.nodes.gateway.search_knowledge_base", counting)
    scripted = _Scripted(
        [
            {
                "action": "tool",
                "name": "search_knowledge_base",
                "arguments": {"query": "refund"},
            },
            {
                "action": "tool",
                "name": "search_knowledge_base",
                "arguments": {"query": "refund"},
            },
            {"action": "finish"},
        ]
    )
    monkeypatch.setattr("ticket_triage.graph.nodes.get_llm", lambda: scripted)
    result = _run("act-dup", "Urgent refund", "need a refund")
    assert calls["n"] == 1
    assert result.tool_calls[1]["error"] == "tool_duplicate:search_knowledge_base"
    assert _names(result)[-1] == "finish"


def test_status_write_is_refused_before_review(monkeypatch) -> None:
    writes = {"n": 0}
    real = gateway.update_ticket_status

    def counting(*args, **kwargs):
        writes["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr("ticket_triage.graph.nodes.gateway.update_ticket_status", counting)
    scripted = _Scripted(
        [
            {
                "action": "tool",
                "name": "update_ticket_status",
                "arguments": {"status": "resolved"},
            }
        ]
        * 6
    )
    monkeypatch.setattr("ticket_triage.graph.nodes.get_llm", lambda: scripted)
    result = _run("act-write", "Urgent refund", "need a refund")
    assert writes["n"] == 0
    assert result.status == "awaiting_human"
    assert len(result.tool_calls) == 4
    assert all(call["error"] == "tool_not_allowed:update_ticket_status" for call in result.tool_calls)
    assert "act_max_steps" in result.errors
    assert gateway.get_ticket("act-write")["status"] == "open"


def test_history_of_another_ticket_is_refused(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("不应读取其他工单")

    monkeypatch.setattr("ticket_triage.graph.nodes.gateway.get_ticket_history", boom)
    scripted = _Scripted(
        [
            {
                "action": "tool",
                "name": "get_ticket_history",
                "arguments": {"ticket_id": "someone-else"},
            },
            {"action": "finish"},
        ]
    )
    monkeypatch.setattr("ticket_triage.graph.nodes.get_llm", lambda: scripted)
    result = _run("act-cross", "Hello", "Just saying hi")
    assert result.tool_calls[0]["error"] == "ticket_id_mismatch"
    assert _names(result)[-1] == "finish"
    assert "act_model_failed" not in " ".join(result.errors)


def test_bad_action_json_falls_back_to_stub(monkeypatch) -> None:
    def complete(system: str, user: str) -> str:
        if "工具调度" in system:
            return "not json"
        if "分类" in system:
            return '{"category":"billing","priority":"high","rationale":"退款"}'
        return '{"body":"14 天内可退款。","citations":["kb-billing-refund"]}'

    monkeypatch.setattr("ticket_triage.graph.nodes.get_llm", lambda: ModelLLM(complete))
    result = _run("act-bad-json", "Urgent refund", "need a refund")
    assert any(item.startswith("act_model_failed") for item in result.errors)
    assert "search_knowledge_base" in _names(result)
    assert result.draft is not None
    assert result.draft.body.startswith("14 天内可退款")


def test_stub_policy_skips_search_for_other() -> None:
    ticket = TicketIn(ticket_id="s", subject="Hi", body="hello")
    classification = Classification(
        priority=Priority.low,
        category=Category.other,
        rationale="test",
    )
    assert stub_next_action(ticket, classification, []) == {"action": "finish"}


def test_parse_action_keeps_disallowed_name() -> None:
    parsed = parse_action(
        {"action": "tool", "name": "update_ticket_status", "arguments": {"status": "resolved"}}
    )
    assert parsed["name"] == "update_ticket_status"
    assert parse_action({"action": "finish"}) == {"action": "finish"}
