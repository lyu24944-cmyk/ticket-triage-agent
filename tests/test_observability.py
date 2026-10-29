"""Langfuse 没配 Key 时不发请求；配了之后按 prompt_version 记质量和延迟。"""

from ticket_triage.config import Settings, settings
from ticket_triage.graph.workflow import run_triage
from ticket_triage.models import RunRequest, TicketIn
from ticket_triage.observability import prompt_version, record_tool, run_observed, score_eval_case


def test_prompt_version_is_stub_without_model() -> None:
    assert prompt_version() == "stub-act-v1"


def test_missing_keys_do_not_open_a_trace(monkeypatch) -> None:
    called = {"n": 0}

    def boom() -> None:
        called["n"] += 1
        raise AssertionError("没有 Key 不应创建 Langfuse 客户端")

    monkeypatch.setattr("ticket_triage.observability._client", boom)
    result = run_triage(
        RunRequest(
            ticket=TicketIn(ticket_id="lf-off", subject="Hello", body="Just saying hi"),
        )
    )
    assert result.status == "awaiting_human"
    assert called["n"] == 0
    score_eval_case(result.trace_id, {"category_accuracy": 1.0})
    assert called["n"] == 0


def test_trace_records_version_latency_and_eval_scores(monkeypatch) -> None:
    monkeypatch.setattr(settings, "langfuse_public_key", "pk-test")
    monkeypatch.setattr(settings, "langfuse_secret_key", "sk-test")
    seen: dict = {"scores": [], "eval": []}

    class _Span:
        def update(self, **kwargs) -> None:
            seen["output"] = kwargs

        def score_trace(self, **kwargs) -> None:
            seen["scores"].append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> bool:
            return False

    class _Client:
        def start_as_current_observation(self, **kwargs):
            seen["observation"] = kwargs
            return _Span()

        def create_score(self, **kwargs) -> None:
            seen["eval"].append(kwargs)

        def flush(self) -> None:
            seen["flushed"] = True

    class _Attrs:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> bool:
            return False

    monkeypatch.setattr("ticket_triage.observability._client", lambda: _Client())
    monkeypatch.setattr(
        "ticket_triage.observability._prepare_trace",
        lambda trace_id: (_Client(), "stub-act-v1", "lf-trace"),
    )
    monkeypatch.setattr(
        "langfuse.propagate_attributes",
        lambda **kwargs: _Attrs(),
    )
    monkeypatch.setattr("ticket_triage.observability._callback_handler", lambda: object())

    def graph_invoke(state, config=None):
        seen["config"] = config
        return {
            "status": "resolved",
            "errors": [],
            "classification": None,
            "draft": None,
        }

    class _Graph:
        def invoke(self, state, config=None):
            return graph_invoke(state, config)

    class _Ticket:
        ticket_id = "t-1"
        subject = "退款"

    final = run_observed(_Graph(), {"ticket": "x"}, "local-id", _Ticket())
    assert final["status"] == "resolved"
    assert seen["observation"]["version"] == "stub-act-v1"
    assert seen["observation"]["metadata"]["prompt_version"] == "stub-act-v1"
    assert seen["config"]["metadata"]["langfuse_tags"] == ["stub-act-v1"]
    assert {item["name"] for item in seen["scores"]} == {"latency_ms", "no_model_fallback"}
    assert seen["flushed"] is True

    score_eval_case("local-id", {"category_accuracy": 1.0, "tool_decision": None})
    assert seen["eval"][0]["name"] == "category_accuracy"
    assert seen["eval"][0]["value"] == 1.0
    assert all(item["name"] != "tool_decision" for item in seen["eval"])


def test_search_is_recorded_as_retriever(monkeypatch) -> None:
    monkeypatch.setattr(settings, "langfuse_public_key", "pk-test")
    monkeypatch.setattr(settings, "langfuse_secret_key", "sk-test")
    seen: dict = {}

    class _Span:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> bool:
            return False

    class _Client:
        def start_as_current_observation(self, **kwargs):
            seen.update(kwargs)
            return _Span()

    monkeypatch.setattr("ticket_triage.observability._client", lambda: _Client())
    record_tool(
        name="search_knowledge_base",
        arguments={"query": "refund"},
        output={"documents": ["kb-billing-refund"]},
        ok=True,
    )
    assert seen["as_type"] == "retriever"
    assert seen["name"] == "search-knowledge"
    assert seen["metadata"]["tool"] == "search_knowledge_base"


def test_rag_local_overrides_remote_url(monkeypatch) -> None:
    monkeypatch.setenv("RAG_BASE_URL", "local")
    fresh = Settings()
    assert fresh.rag_base_url is None
