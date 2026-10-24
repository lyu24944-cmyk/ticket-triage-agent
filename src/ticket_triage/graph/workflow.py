"""编译并运行分流图；暂停状态写入 SQLite，节点耗时写入追踪。"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from ticket_triage.config import settings
from ticket_triage.graph.nodes import (
    act_node,
    classify_node,
    draft_node,
    finalize_node,
    hitl_gate_node,
    ingest_node,
)
from ticket_triage.graph.state import TriageState
from ticket_triage.models import DraftReply, HumanDecision, RunRequest, RunResult, TicketIn
from ticket_triage.observability import run_observed
from ticket_triage.runtime import (
    archive_pending,
    get_trace,
    load_closed,
    load_pending,
    record_span,
    save_pending,
)
from ticket_triage.tools import gateway

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


def _route_after_act(state: TriageState) -> str:
    """act 后路由：工具失败可重试，未结束则再走一步，否则起草。"""
    if state.get("status") == "enrich_error":
        if int(state.get("tool_retries") or 0) <= settings.max_tool_retries:
            return "act"
        return "draft"
    if state.get("status") == "acting":
        return "act"
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
    g.add_node("act", _traced("act", act_node))
    g.add_node("draft", _traced("draft", draft_node))
    g.add_node("hitl_gate", _traced("hitl_gate", hitl_gate_node))
    g.add_node("finalize", _traced("finalize", finalize_node))

    g.add_edge(START, "ingest")
    g.add_edge("ingest", "classify")
    g.add_edge("classify", "act")
    g.add_conditional_edges(
        "act",
        _route_after_act,
        {"act": "act", "draft": "draft"},
    )
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
        tool_calls=list(state.get("tool_calls") or []),
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
        "tool_calls": [],
        "awaiting_human": False,
        "trace_id": trace_id,
    }
    final = run_observed(graph, initial, trace_id, req.ticket)
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
    archive_pending(decision.ticket_id, str(trace_id or ""), pending)
    return _result_from_state(decision.ticket_id, pending, trace_id)


def reopen_ticket(ticket_id: str) -> RunResult:
    """把已结案或已升级的工单放回待审，并在历史里追加一条，不覆盖旧记录。"""
    if ticket_id in _PENDING or load_pending(ticket_id) is not None:
        return RunResult(ticket_id=ticket_id, status="already_pending", errors=["already_pending"])
    ticket = gateway.get_ticket(ticket_id)
    if ticket is None:
        return RunResult(ticket_id=ticket_id, status="not_found", errors=["ticket_not_found"])
    if ticket["status"] not in {"resolved", "escalated"}:
        return RunResult(ticket_id=ticket_id, status="not_closed", errors=["not_closed"])
    state = load_closed(ticket_id)
    if state is None:
        note = ""
        for item in reversed(ticket["history"]):
            if item.get("status") in {"resolved", "escalated"} and item.get("note"):
                note = str(item["note"])
                break
        state = {
            "ticket": TicketIn(
                ticket_id=ticket["ticket_id"],
                subject=ticket["subject"],
                body=ticket["body"],
            ),
            "draft": DraftReply(
                body=note or "上次草稿未留档。请根据工单原文修改后通过，或再次驳回。",
                citations=[],
                confidence=0.0,
            ),
            "kb_hits": [],
            "errors": [],
            "tool_retries": 0,
            "auto_approve": False,
        }
    else:
        state = dict(state)
    trace_id = str(state.get("trace_id") or uuid.uuid4())
    state["trace_id"] = trace_id
    state["awaiting_human"] = True
    state["human_approved"] = None
    state["human_edits"] = None
    state["status"] = "awaiting_human"
    _PENDING[ticket_id] = state
    save_pending(ticket_id, trace_id, state)
    gateway.update_ticket_status(ticket_id, "pending", "重新送审")
    return _result_from_state(ticket_id, state, trace_id)


def read_trace(trace_id: str) -> dict[str, Any]:
    """读取一次运行的节点追踪。"""
    return get_trace(trace_id)
