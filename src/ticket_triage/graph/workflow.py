"""编译并运行分流图；管理 HITL 暂停/恢复。"""

from __future__ import annotations

import uuid
from typing import Any

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


def _route_after_enrich(state: TriageState) -> str:
    """enrich 后路由：可重试、或软降级进入起草。"""
    if state.get("status") == "enrich_error":
        if int(state.get("tool_retries") or 0) <= settings.max_tool_retries:
            return "enrich"
        return "draft"  # 软降级：无 KB 也起草
    return "draft"


def _route_after_hitl(state: TriageState) -> str:
    """人审关卡后：暂停结束，或进入 finalize。"""
    if state.get("awaiting_human"):
        return END
    return "finalize"


def build_graph():
    """构建并编译 LangGraph。"""
    g: StateGraph = StateGraph(TriageState)
    g.add_node("ingest", ingest_node)
    g.add_node("classify", classify_node)
    g.add_node("enrich", enrich_node)
    g.add_node("draft", draft_node)
    g.add_node("hitl_gate", hitl_gate_node)
    g.add_node("finalize", finalize_node)

    g.add_edge(START, "ingest")
    g.add_edge("ingest", "classify")
    g.add_edge("classify", "enrich")
    g.add_conditional_edges("enrich", _route_after_enrich, {"enrich": "enrich", "draft": "draft"})
    g.add_edge("draft", "hitl_gate")
    g.add_conditional_edges("hitl_gate", _route_after_hitl, {"finalize": "finalize", END: END})
    g.add_edge("finalize", END)
    return g.compile()


_GRAPH = None
# P1：内存中保存待人审运行；后续可换成 checkpointer
_PENDING: dict[str, dict[str, Any]] = {}


def _get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


def run_triage(req: RunRequest) -> RunResult:
    """执行一次分流；若需人审则写入待办。"""
    graph = _get_graph()
    trace_id = str(uuid.uuid4())
    initial: TriageState = {
        "ticket": req.ticket,
        "auto_approve": req.auto_approve,
        "errors": [],
        "tool_retries": 0,
        "awaiting_human": False,
    }
    final = graph.invoke(initial)
    result = RunResult(
        ticket_id=req.ticket.ticket_id,
        status=final.get("status") or "unknown",
        classification=final.get("classification"),
        draft=final.get("draft"),
        awaiting_human=bool(final.get("awaiting_human")),
        errors=list(final.get("errors") or []),
        trace_id=trace_id,
    )
    if result.awaiting_human:
        _PENDING[req.ticket.ticket_id] = final
    return result


def resume_with_human(decision: HumanDecision) -> RunResult:
    """根据人审决策继续 finalize。"""
    pending = _PENDING.pop(decision.ticket_id, None)
    if pending is None:
        return RunResult(
            ticket_id=decision.ticket_id,
            status="not_found",
            errors=["no_pending_run"],
        )
    pending["human_approved"] = decision.approved
    pending["human_edits"] = decision.edits
    pending["awaiting_human"] = False
    out = finalize_node(pending)
    pending.update(out)
    return RunResult(
        ticket_id=decision.ticket_id,
        status=pending.get("status") or "unknown",
        classification=pending.get("classification"),
        draft=pending.get("draft"),
        awaiting_human=False,
        errors=list(pending.get("errors") or []),
    )
