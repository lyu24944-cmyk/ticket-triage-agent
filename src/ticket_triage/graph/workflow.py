"""编译并运行分流图；暂停状态写入 SQLite，节点耗时写入追踪。"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

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
    delete_closed,
    delete_pending,
    get_trace,
    load_closed,
    load_pending,
    load_run,
    mark_interrupted_runs,
    record_span,
    runtime_db_path,
    save_pending,
    save_run,
)
from ticket_triage.tools import gateway

logger = logging.getLogger("ticket_triage.trace")


class WorkflowFailure(Exception):
    """图执行失败。响应里只带 trace_id，不把内部异常原文返回给调用方。"""

    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id
        super().__init__(trace_id)

# 仅作热缓存；进程重启后以 SQLite 为准
_PENDING: dict[str, dict[str, Any]] = {}
_GRAPH = None
_TICKET_LOCKS: dict[str, threading.Lock] = {}
_LOCKS = threading.Lock()
_RUNNING: dict[str, str] = {}
_EXECUTOR: ThreadPoolExecutor | None = None
_EXEC_LOCK = threading.Lock()
_RECOVERED_DB: str | None = None


def forget_memory() -> None:
    """模拟进程重启：丢掉内存缓存，不删 SQLite。"""
    global _GRAPH
    _PENDING.clear()
    _GRAPH = None


def _ticket_lock(ticket_id: str) -> threading.Lock:
    with _LOCKS:
        lock = _TICKET_LOCKS.get(ticket_id)
        if lock is None:
            lock = threading.Lock()
            _TICKET_LOCKS[ticket_id] = lock
        return lock


def discard_ticket(ticket_id: str) -> None:
    """删掉一张本地工单及其待审、结案留档。演示脚本用来重跑固定编号。"""
    with _ticket_lock(ticket_id):
        _PENDING.pop(ticket_id, None)
        delete_pending(ticket_id)
        delete_closed(ticket_id)
        gateway.delete_ticket(ticket_id)


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


def _pending_result(ticket_id: str) -> RunResult | None:
    pending = _PENDING.get(ticket_id)
    if pending is None:
        pending = load_pending(ticket_id)
        if pending is not None:
            _PENDING[ticket_id] = pending
    if pending is None:
        return None
    return _result_from_state(ticket_id, pending, pending.get("trace_id"))


def _executor() -> ThreadPoolExecutor:
    global _EXECUTOR, _RECOVERED_DB
    with _EXEC_LOCK:
        db_key = str(runtime_db_path())
        if _RECOVERED_DB != db_key:
            mark_interrupted_runs()
            _RECOVERED_DB = db_key
        if _EXECUTOR is None:
            workers = max(1, int(settings.run_workers))
            _EXECUTOR = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="triage")
        return _EXECUTOR


def drain_jobs() -> None:
    """等后台运行结束。测试在换数据库之前调用。"""
    global _EXECUTOR
    with _EXEC_LOCK:
        pool = _EXECUTOR
        _EXECUTOR = None
    if pool is not None:
        pool.shutdown(wait=True, cancel_futures=False)
    _RUNNING.clear()


def recover_interrupted_runs() -> int:
    """启动时把上次没跑完的 HTTP 运行标成失败。"""
    global _RECOVERED_DB
    with _EXEC_LOCK:
        count = mark_interrupted_runs()
        _RECOVERED_DB = str(runtime_db_path())
        return count


def _failed_result(ticket_id: str, trace_id: str) -> RunResult:
    return RunResult(
        ticket_id=ticket_id,
        status="failed",
        errors=["internal_error"],
        trace_id=trace_id,
    )


def _background_triage(req: RunRequest, trace_id: str) -> None:
    ticket_id = req.ticket.ticket_id
    try:
        try:
            result = run_triage(req, trace_id=trace_id)
        except WorkflowFailure as exc:
            result = _failed_result(ticket_id, exc.trace_id or trace_id)
        except Exception:
            logger.exception("trace_id=%s background run failed", trace_id)
            result = _failed_result(ticket_id, trace_id)
        if result.trace_id != trace_id:
            result = result.model_copy(update={"trace_id": trace_id})
        save_run(result)
        logger.info("trace_id=%s status=%s background done", trace_id, result.status)
    finally:
        with _ticket_lock(ticket_id):
            if _RUNNING.get(ticket_id) == trace_id:
                _RUNNING.pop(ticket_id, None)


def submit_triage(req: RunRequest) -> RunResult:
    """受理一次分流并立刻返回。分类、选工具和起草在后台线程里跑。"""
    pool = _executor()
    ticket_id = req.ticket.ticket_id
    with _ticket_lock(ticket_id):
        existing_pending = _pending_result(ticket_id)
        if existing_pending is not None:
            return existing_pending
        stored = gateway.get_ticket(ticket_id)
        if stored is not None and stored["status"] in {"resolved", "escalated"}:
            return RunResult(
                ticket_id=ticket_id,
                status=stored["status"],
                errors=["already_closed"],
            )
        running = _RUNNING.get(ticket_id)
        if running is not None:
            current = load_run(running)
            if current is not None:
                return current
            return RunResult(ticket_id=ticket_id, status="running", trace_id=running)
        trace_id = str(uuid.uuid4())
        accepted = RunResult(ticket_id=ticket_id, status="running", trace_id=trace_id)
        _RUNNING[ticket_id] = trace_id
        save_run(accepted)
        pool.submit(_background_triage, req, trace_id)
        logger.info("trace_id=%s ticket_id=%s accepted", trace_id, ticket_id)
        return accepted


def get_run(trace_id: str) -> RunResult | None:
    """读取一次 HTTP 运行。还没有这条记录时返回 None。"""
    return load_run(trace_id)


def run_triage(req: RunRequest, *, trace_id: str | None = None) -> RunResult:
    """执行一次分流。同一张待审单直接返回原结果；已结案的不重跑、不改历史。"""
    ticket_id = req.ticket.ticket_id
    with _ticket_lock(ticket_id):
        existing_pending = _pending_result(ticket_id)
        if existing_pending is not None:
            return existing_pending
        stored = gateway.get_ticket(ticket_id)
        if stored is not None and stored["status"] in {"resolved", "escalated"}:
            return RunResult(
                ticket_id=ticket_id,
                status=stored["status"],
                errors=["already_closed"],
            )
        trace_id = trace_id or str(uuid.uuid4())
        try:
            graph = _get_graph()
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
            result = _result_from_state(ticket_id, final, trace_id)
            if result.awaiting_human:
                _PENDING[ticket_id] = final
                save_pending(ticket_id, trace_id, final)
            return result
        except Exception as exc:
            logger.exception("trace_id=%s run failed", trace_id)
            raise WorkflowFailure(trace_id) from exc


def resume_with_human(decision: HumanDecision) -> RunResult:
    """根据人审决策继续 finalize。内存没有时从 SQLite 恢复。"""
    with _ticket_lock(decision.ticket_id):
        pending = _PENDING.pop(decision.ticket_id, None)
        if pending is None:
            pending = load_pending(decision.ticket_id)
        if pending is None:
            return RunResult(
                ticket_id=decision.ticket_id,
                status="not_found",
                errors=["no_pending_run"],
            )
        trace_id = str(pending.get("trace_id") or uuid.uuid4())
        try:
            pending["human_approved"] = decision.approved
            pending["human_edits"] = decision.edits
            pending["reviewer"] = decision.reviewer
            pending["awaiting_human"] = False
            out = _traced("finalize", finalize_node)(pending)
            pending.update(out)
            archive_pending(decision.ticket_id, trace_id, pending)
            return _result_from_state(decision.ticket_id, pending, trace_id)
        except Exception as exc:
            logger.exception("trace_id=%s resume failed", trace_id)
            raise WorkflowFailure(trace_id) from exc


def reopen_ticket(ticket_id: str, reviewer: str) -> RunResult:
    """把已结案或已升级的工单放回待审，并在历史里追加一条，不覆盖旧记录。"""
    with _ticket_lock(ticket_id):
        return _reopen_ticket(ticket_id, reviewer)


def _reopen_ticket(ticket_id: str, reviewer: str) -> RunResult:
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
    try:
        state["trace_id"] = trace_id
        state["awaiting_human"] = True
        state["human_approved"] = None
        state["human_edits"] = None
        state["reviewer"] = reviewer
        state["status"] = "awaiting_human"
        _PENDING[ticket_id] = state
        save_pending(ticket_id, trace_id, state)
        gateway.update_ticket_status(ticket_id, "pending", "重新送审", reviewer=reviewer)
        return _result_from_state(ticket_id, state, trace_id)
    except Exception as exc:
        logger.exception("trace_id=%s reopen failed", trace_id)
        raise WorkflowFailure(trace_id) from exc


def read_trace(trace_id: str) -> dict[str, Any]:
    """读取一次运行的节点追踪。"""
    return get_trace(trace_id)
