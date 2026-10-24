"""可选的 Langfuse 追踪。没配 Key 时不发请求，SQLite 节点追踪照旧。"""

from __future__ import annotations

import logging
from typing import Any

from ticket_triage.config import settings

logger = logging.getLogger("ticket_triage.langfuse")

_CLIENT: Any = None
_CLIENT_KEY: tuple[str, str, str] | None = None


def prompt_version() -> str:
    """和评测报告用同一套版本号，方便在 Langfuse 里按版本筛。"""
    from ticket_triage.llm import ModelLLM, get_llm

    provider = (settings.llm_provider or "stub").lower()
    if isinstance(get_llm(), ModelLLM):
        return f"{provider}-act-v2"
    return "stub-act-v1"


def langfuse_enabled() -> bool:
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key)


def _client() -> Any:
    global _CLIENT, _CLIENT_KEY
    key = (
        settings.langfuse_public_key or "",
        settings.langfuse_secret_key or "",
        settings.langfuse_base_url,
    )
    if _CLIENT is not None and _CLIENT_KEY == key:
        return _CLIENT
    from langfuse import Langfuse

    _CLIENT = Langfuse(
        public_key=key[0],
        secret_key=key[1],
        base_url=key[2],
    )
    _CLIENT_KEY = key
    return _CLIENT


def _callback_handler() -> Any:
    from langfuse.langchain import CallbackHandler

    return CallbackHandler()


def run_observed(graph: Any, initial: dict[str, Any], trace_id: str, ticket: Any) -> dict[str, Any]:
    """跑一次图。配了 Langfuse 时把这次运行记成一条 trace，并带上版本和延迟。"""
    if not langfuse_enabled():
        return graph.invoke(initial)
    try:
        prepared = _prepare_trace(trace_id)
    except Exception:
        logger.exception("langfuse setup failed")
        return graph.invoke(initial)
    return _run_observed(graph, initial, ticket, prepared)


def _prepare_trace(trace_id: str) -> tuple[Any, str, str]:
    from langfuse import Langfuse

    client = _client()
    version = prompt_version()
    lf_trace_id = Langfuse.create_trace_id(seed=trace_id)
    return client, version, lf_trace_id


def _run_observed(
    graph: Any,
    initial: dict[str, Any],
    ticket: Any,
    prepared: tuple[Any, str, str],
) -> dict[str, Any]:
    import time

    client, version, lf_trace_id = prepared
    started = time.perf_counter()
    try:
        return _emit(client, version, lf_trace_id, graph, initial, ticket, started)
    finally:
        try:
            client.flush()
        except Exception:
            logger.exception("langfuse flush failed")


def _emit(
    client: Any,
    version: str,
    lf_trace_id: str,
    graph: Any,
    initial: dict[str, Any],
    ticket: Any,
    started: float,
) -> dict[str, Any]:
    import time

    from langfuse import propagate_attributes

    with client.start_as_current_observation(
        as_type="agent",
        name="ticket-triage",
        trace_context={"trace_id": lf_trace_id},
        input={"ticket_id": ticket.ticket_id, "subject": ticket.subject},
        metadata={"prompt_version": version},
        version=version,
    ) as span:
        with propagate_attributes(
            trace_name="ticket-triage",
            version=version,
            tags=[version],
            metadata={"prompt_version": version},
            session_id=ticket.ticket_id,
            environment="development",
        ):
            final = _invoke_graph(graph, initial, ticket.ticket_id, version)
        _score_run(span, final, (time.perf_counter() - started) * 1000)
    return final


def _invoke_graph(graph: Any, initial: dict[str, Any], ticket_id: str, version: str) -> dict[str, Any]:
    try:
        handler = _callback_handler()
    except Exception:
        logger.exception("langfuse callback unavailable")
        return graph.invoke(initial)
    return graph.invoke(
        initial,
        config={
            "callbacks": [handler],
            "run_name": "ticket-triage",
            "metadata": {
                "langfuse_session_id": ticket_id,
                "langfuse_tags": [version],
            },
        },
    )


def _score_run(span: Any, final: dict[str, Any], latency_ms: float) -> None:
    errors = [str(item) for item in (final.get("errors") or [])]
    fallback = any(
        item.startswith(("classify_model_failed", "draft_model_failed", "act_model_failed"))
        for item in errors
    )
    classification = final.get("classification")
    draft = final.get("draft")
    try:
        span.update(
            output={
                "status": final.get("status"),
                "category": classification.category.value if classification else None,
                "priority": classification.priority.value if classification else None,
                "citations": list(draft.citations) if draft else [],
                "draft_body": draft.body if draft else "",
                "errors": errors,
            }
        )
        span.score_trace(name="latency_ms", value=float(latency_ms), data_type="NUMERIC")
        span.score_trace(
            name="no_model_fallback",
            value=0.0 if fallback else 1.0,
            data_type="NUMERIC",
        )
    except Exception:
        logger.exception("langfuse score failed")


def record_generation(
    *,
    name: str,
    model: str,
    system: str,
    user: str,
    output: str,
    usage: dict[str, int] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """把一次模型调用记成 generation。没有 Key 时直接返回。"""
    if not langfuse_enabled():
        return
    try:
        client = _client()
        with client.start_as_current_observation(
            as_type="generation",
            name=name,
            model=model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            output=output,
            usage_details=usage or None,
            metadata=metadata,
        ):
            return
    except Exception:
        logger.exception("langfuse generation failed")


def record_tool(*, name: str, arguments: dict[str, Any], output: Any, ok: bool) -> None:
    """把一次工具调用记成 retriever 或 tool。名字保持稳定，原始工具名放进 metadata。"""
    if not langfuse_enabled():
        return
    stable = {
        "search_knowledge_base": ("retriever", "search-knowledge"),
        "get_ticket_history": ("tool", "read-ticket-history"),
        "finish": ("tool", "finish-acting"),
        "update_ticket_status": ("tool", "update-ticket-status"),
    }
    as_type, obs_name = stable.get(name, ("tool", "call-tool"))
    try:
        client = _client()
        with client.start_as_current_observation(
            as_type=as_type,
            name=obs_name,
            input=arguments,
            output=output,
            metadata={"tool": name, "ok": ok},
        ):
            return
    except Exception:
        logger.exception("langfuse tool failed")


def score_eval_case(trace_id: str | None, scores: dict[str, float | None]) -> None:
    """把黄金集打分贴到同一次运行上。trace_id 用本地 uuid 做种子，和 run_observed 对齐。"""
    if not trace_id or not langfuse_enabled():
        return
    try:
        from langfuse import Langfuse

        client = _client()
        lf_trace_id = Langfuse.create_trace_id(seed=trace_id)
        for name, value in scores.items():
            if value is None:
                continue
            client.create_score(
                name=name,
                value=float(value),
                trace_id=lf_trace_id,
                data_type="NUMERIC",
            )
        client.flush()
    except Exception:
        logger.exception("langfuse eval score failed")
