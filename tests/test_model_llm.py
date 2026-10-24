"""真实模型起草：引用只能来自本次检索；模型失败时退回 stub 并记错。"""

import json

import httpx

from ticket_triage.config import settings
from ticket_triage.graph.workflow import run_triage
from ticket_triage.llm import ModelLLM, StubLLM, get_llm
from ticket_triage.llm.model import anthropic_complete, openai_complete
from ticket_triage.models import Category, Classification, Priority, RunRequest, TicketIn

TICKET = TicketIn(ticket_id="m-1", subject="紧急退款", body="我被重复扣款，需要退款")
BILLING = Classification(
    priority=Priority.high,
    category=Category.billing,
    rationale="test",
    needs_escalation=False,
)
HITS = [{"id": "kb-billing-refund", "title": "退款政策", "body": "14 天内退款。REF-14。"}]


def _reply(body: str, citations: list[str]) -> str:
    return json.dumps({"body": body, "citations": citations}, ensure_ascii=False)


def _classification(category: str, priority: str, rationale: str = "测试理由") -> str:
    return json.dumps(
        {"category": category, "priority": priority, "rationale": rationale},
        ensure_ascii=False,
    )


def test_model_classification_uses_allowed_labels() -> None:
    llm = ModelLLM(lambda system, user: _classification("technical", "low", "登录问题"))
    result = llm.classify(TicketIn(ticket_id="m-c", subject="Hello", body="hi"))
    assert result.category == Category.technical
    assert result.priority == Priority.low
    assert result.needs_escalation is False
    assert result.rationale == "登录问题"


def test_billing_high_still_marks_escalation() -> None:
    llm = ModelLLM(lambda system, user: _classification("billing", "high"))
    result = llm.classify(TICKET)
    assert result.needs_escalation is True


def test_unknown_category_is_rejected() -> None:
    llm = ModelLLM(lambda system, user: _classification("shipping", "low"))
    try:
        llm.classify(TICKET)
    except ValueError as exc:
        assert "bad_category" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_classify_failure_falls_back_and_draft_still_uses_model(monkeypatch) -> None:
    calls = {"n": 0}

    def complete(system: str, user: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return "not json"
        return _reply("可在 14 天内退款。", ["kb-billing-refund"])

    monkeypatch.setattr("ticket_triage.graph.nodes.get_llm", lambda: ModelLLM(complete))
    result = run_triage(
        RunRequest(ticket=TICKET.model_copy(update={"ticket_id": "m-cls"}), auto_approve=True)
    )
    assert result.classification is not None
    assert result.classification.category == Category.billing
    assert result.classification.rationale == "stub 启发式分类"
    assert any(item.startswith("classify_model_failed") for item in result.errors)
    assert result.draft is not None
    assert result.draft.body.startswith("可在 14 天内退款")


def test_invented_citation_is_dropped() -> None:
    llm = ModelLLM(lambda system, user: _reply("可在 14 天内退款。", ["kb-billing-refund", "kb-fake"]))
    draft = llm.draft_reply(TICKET, BILLING, HITS)
    assert draft.citations == ["kb-billing-refund"]


def test_no_hits_means_no_citations() -> None:
    llm = ModelLLM(lambda system, user: _reply("我们会跟进。", ["kb-billing-refund"]))
    draft = llm.draft_reply(TICKET, BILLING, [])
    assert draft.citations == []


def test_prompt_lists_only_retrieved_documents() -> None:
    seen: dict[str, str] = {}

    def complete(system: str, user: str) -> str:
        seen["user"] = user
        return _reply("可在 14 天内退款。", ["kb-billing-refund"])

    ModelLLM(complete).draft_reply(TICKET, BILLING, HITS)
    assert "[kb-billing-refund]" in seen["user"]
    assert "kb-tech-reset" not in seen["user"]


def test_fenced_json_is_parsed() -> None:
    raw = "```json\n" + _reply("可在 14 天内退款。", ["kb-billing-refund"]) + "\n```"
    draft = ModelLLM(lambda system, user: raw).draft_reply(TICKET, BILLING, HITS)
    assert draft.body.startswith("可在 14 天内退款")


def test_model_failure_falls_back_to_stub_draft(monkeypatch) -> None:
    def broken(system: str, user: str) -> str:
        return "not json"

    monkeypatch.setattr("ticket_triage.graph.nodes.get_llm", lambda: ModelLLM(broken))
    result = run_triage(RunRequest(ticket=TICKET.model_copy(update={"ticket_id": "m-2"}), auto_approve=True))
    assert result.status == "resolved"
    assert result.draft is not None
    assert result.draft.citations == ["kb-billing-refund"]
    assert any(item.startswith("draft_model_failed") for item in result.errors)


def test_provider_without_key_uses_stub(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    assert isinstance(get_llm(), StubLLM)


def test_anthropic_request_shape(monkeypatch) -> None:
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model", "test-model")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.anthropic.com/v1/messages"
        assert request.headers["x-api-key"] == "test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "test-model"
        assert payload["messages"][0]["role"] == "user"
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    complete = anthropic_complete(httpx.Client(transport=httpx.MockTransport(handler)))
    assert complete("system", "user") == "ok"


def test_openai_request_shape(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "openai_base_url", "https://llm.test/v1/")
    monkeypatch.setattr(settings, "llm_model", "test-model")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://llm.test/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["messages"][0]["role"] == "system"
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    complete = openai_complete(httpx.Client(transport=httpx.MockTransport(handler)))
    assert complete("system", "user") == "ok"


def test_openai_retries_transient_transport_error(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "openai_base_url", "https://llm.test/v1")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("eof", request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    complete = openai_complete(httpx.Client(transport=httpx.MockTransport(handler)))
    assert complete("system", "user") == "ok"
    assert calls["n"] == 2
