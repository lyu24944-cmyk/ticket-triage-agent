"""编译并运行分流图；暂停状态写入 SQLite，节点耗时写入追踪。"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from ticket_triage.config import settings
from ticket_triage.graph.nodes import (
    classify_node,
    draft_node,
    enrich_node,
    finalize_node,
    hitl_gate_node,
    ingest_node,
)
from ticket_triage.graph.state import TriageState
from ticket_triage.models import HumanDecision, RunRequest, RunResult
from ticket_triage.runtime import delete_pending, get_trace, load_pending, record_span, save_pending

logger = logging.getLogger("ticket_triage.trace")

# 仅作热缓存；进程重启后以 SQLite 为准
_PENDING: dict[str, dict[str, Any]] = {}
_GRAPH = None


def forget_memory() -> None:
    """模拟进程重启：丢掉内存缓存，不删 SQLite。"""
    global _GRAPH
    _PENDING.clear()
    _GRAPH = None


def _traced(name: str, fn: Callable[[TriageState], dict]) -> Callable[[TriageState], dict]:
    def wrapped(state: TriageState) -> dict:
        trace_id = state.get("trace_id") or "unknown"
        started = time.perf_counter()
        try:
            out = fn(state)
            duration_ms = (time.perf_counter() - started) * 1000
            status = str(out.get("status") or "ok")
            detail = None
            errors = out.get("errors") or []
            if errors:
                detail = "; ".join(str(item) for item in errors)
            record_span(trace_id, name, duration_ms, status, detail)
            logger.info(
                "trace_id=%s node=%s status=%s duration_ms=%.2f",
                trace_id,
                name,
                status,
                duration_ms,
            )
            return out
        except Exception as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            record_span(trace_id, name, duration_ms, "error", str(exc))
            logger.exception("trace_id=%s node=%s failed", trace_id, name)
            raise

    return wrapped


def _route_after_enrich(state: TriageState) -> str:
    """enrich 后路由：可重试、或软降级进入起草。"""
    if state.get("status") == "enrich_error":
        if int(state.get("tool_retries") or 0) <= settings.max_tool_retries:
            return "enrich"
        return "draft"
    return "draft"


def _route_after_hitl(state: TriageState) -> str:
    """人审关卡后：暂停结束，或进入 finalize。"""
    if state.get("awaiting_human"):
        return END
    return "finalize"


def build_graph():
    """构建并编译 LangGraph。"""
    g: StateGraph = StateGraph(TriageState)
    g.add_node("ingest", _traced("ingest", ingest_node))
    g.add_node("classify", _traced("classify", classify_node))
    g.add_node("enrich", _traced("enrich", enrich_node))
    g.add_node("draft", _traced("draft", draft_node))
    g.add_node("hitl_gate", _traced("hitl_gate", hitl_gate_node))
    g.add_node("finalize", _traced("finalize", finalize_node))

    g.add_edge(START, "ingest")
    g.add_edge("ingest", "classify")
    g.add_edge("classify", "enrich")
    g.add_conditional_edges("enrich", _route_after_enrich, {"enrich": "enrich", "draft": "draft"})
    g.add_edge("draft", "hitl_gate")
    g.add_conditional_edges("hitl_gate", _route_after_hitl, {"finalize": "finalize", END: END})
    g.add_edge("finalize", END)
    return g.compile()


def _get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


def _result_from_state(ticket_id: str, state: dict[str, Any], trace_id: str | None) -> RunResult:
    return RunResult(
        ticket_id=ticket_id,
        status=state.get("status") or "unknown",
        classification=state.get("classification"),
        draft=state.get("draft"),
        awaiting_human=bool(state.get("awaiting_human")),
        errors=list(state.get("errors") or []),
        trace_id=trace_id,
    )


def run_triage(req: RunRequest) -> RunResult:
    """执行一次分流；若需人审则把状态写入 SQLite。"""
    graph = _get_graph()
    trace_id = str(uuid.uuid4())
    initial: TriageState = {
        "ticket": req.ticket,
        "auto_approve": req.auto_approve,
        "errors": [],
        "tool_retries": 0,
        "awaiting_human": False,
        "trace_id": trace_id,
    }
    final = graph.invoke(initial)
    final["trace_id"] = trace_id
    result = _result_from_state(req.ticket.ticket_id, final, trace_id)
    if result.awaiting_human:
        _PENDING[req.ticket.ticket_id] = final
        save_pending(req.ticket.ticket_id, trace_id, final)
    return result


def resume_with_human(decision: HumanDecision) -> RunResult:
    """根据人审决策继续 finalize。内存没有时从 SQLite 恢复。"""
    pending = _PENDING.pop(decision.ticket_id, None)
    if pending is None:
        pending = load_pending(decision.ticket_id)
    if pending is None:
        return RunResult(
            ticket_id=decision.ticket_id,
            status="not_found",
            errors=["no_pending_run"],
        )
    trace_id = pending.get("trace_id")
    pending["human_approved"] = decision.approved
    pending["human_edits"] = decision.edits
    pending["awaiting_human"] = False
    out = _traced("finalize", finalize_node)(pending)
    pending.update(out)
    delete_pending(decision.ticket_id)
    return _result_from_state(decision.ticket_id, pending, trace_id)


def read_trace(trace_id: str) -> dict[str, Any]:
    """读取一次运行的节点追踪。"""
    return get_trace(trace_id)
