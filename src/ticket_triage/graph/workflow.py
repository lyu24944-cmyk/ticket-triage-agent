"""编译并运行分流图；暂停状态写入 SQLite，节点耗时写入追踪。"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
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
from ticket_triage.observability import run_observed, trace_run
from ticket_triage.ops_log import log_event
from ticket_triage.runtime import (
    ClaimLost,
    QueueFull,
    accept_run,
    active_job_count,
    archive_pending,
    checkpoint_runtime,
    claim_next_job,
    clear_claim,
    delete_closed,
    delete_pending,
    get_trace,
    load_closed,
    load_pending,
    load_run,
    mark_interrupted_runs,
    record_span,
    release_running_claims,
    runtime_db_path,
    save_job_checkpoint,
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


class PersistFailure(Exception):
    """检查点或最终结果没写上。放下领取标记，别的工人从上次检查点接着跑。"""

    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id
        super().__init__(trace_id)

# 仅作热缓存；进程重启后以 SQLite 为准
_PENDING: dict[str, dict[str, Any]] = {}
_GRAPH = None
_TICKET_LOCKS: dict[str, threading.Lock] = {}
_LOCKS = threading.Lock()
_RUNNING: dict[str, str] = {}
_EXEC_LOCK = threading.Lock()
_THREADS: list[threading.Thread] = []
_STOP = threading.Event()
_WAKE = threading.Event()
_RUNNER_DB: str | None = None


def forget_memory() -> None:
    """模拟进程重启：丢掉内存缓存，不删 SQLite。"""
    global _GRAPH
    _PENDING.clear()
    _GRAPH = None


def _tenant_of(ticket: Any = None, *, explicit: str | None = None) -> str:
    from ticket_triage.tenants import DEFAULT_TENANT_ID

    if explicit:
        return explicit
    if ticket is None:
        return DEFAULT_TENANT_ID
    if isinstance(ticket, dict):
        return str(ticket.get("tenant_id") or DEFAULT_TENANT_ID)
    return str(getattr(ticket, "tenant_id", None) or DEFAULT_TENANT_ID)


def _scope_key(tenant_id: str, ticket_id: str) -> str:
    return f"{tenant_id}:{ticket_id}"


def _ticket_lock(ticket_id: str, *, tenant_id: str | None = None) -> threading.Lock:
    key = _scope_key(_tenant_of(explicit=tenant_id), ticket_id)
    with _LOCKS:
        lock = _TICKET_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _TICKET_LOCKS[key] = lock
        return lock


def discard_ticket(ticket_id: str, *, tenant_id: str | None = None) -> None:
    """删掉一张本地工单及其待审、结案留档。演示脚本用来重跑固定编号。"""
    tid = _tenant_of(explicit=tenant_id)
    with _ticket_lock(ticket_id, tenant_id=tid):
        _PENDING.pop(_scope_key(tid, ticket_id), None)
        delete_pending(ticket_id, tenant_id=tid)
        delete_closed(ticket_id, tenant_id=tid)
        gateway.delete_ticket(ticket_id, tenant_id=tid)


def _ticket_id(state: TriageState) -> str:
    ticket = state.get("ticket")
    if ticket is None:
        return ""
    return str(getattr(ticket, "ticket_id", "") or "")


def _traced(name: str, fn: Callable[[TriageState], dict]) -> Callable[[TriageState], dict]:
    def wrapped(state: TriageState) -> dict:
        trace_id = state.get("trace_id") or "unknown"
        ticket_id = _ticket_id(state)
        started = time.perf_counter()
        try:
            out = fn(state)
            duration_ms = (time.perf_counter() - started) * 1000
            status = str(out.get("status") or "ok")
            detail = None
            errors = list(out.get("errors") or [])
            if errors:
                detail = "; ".join(str(item) for item in errors)
            record_span(trace_id, name, duration_ms, status, detail)
            log_event(
                "node_done",
                trace_id=trace_id,
                ticket_id=ticket_id,
                node=name,
                queue_depth=active_job_count(),
                errors=errors,
                status=status,
                duration_ms=f"{duration_ms:.2f}",
            )
            return out
        except Exception as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            record_span(trace_id, name, duration_ms, "error", str(exc))
            log_event(
                "node_error",
                level=logging.ERROR,
                trace_id=trace_id,
                ticket_id=ticket_id,
                node=name,
                queue_depth=active_job_count(),
                stub_fallback=False,
                tool_error="",
                status="error",
                duration_ms=f"{duration_ms:.2f}",
            )
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


def _pending_result(ticket_id: str, *, tenant_id: str | None = None) -> RunResult | None:
    tid = _tenant_of(explicit=tenant_id)
    key = _scope_key(tid, ticket_id)
    pending = _PENDING.get(key)
    if pending is None:
        pending = load_pending(ticket_id, tenant_id=tid)
        if pending is not None:
            _PENDING[key] = pending
    if pending is None:
        return None
    return _result_from_state(ticket_id, pending, pending.get("trace_id"))


def _stop_threads_locked() -> None:
    global _THREADS
    threads = list(_THREADS)
    _THREADS = []
    _STOP.set()
    _WAKE.set()
    for thread in threads:
        thread.join(timeout=5)
    _STOP.clear()


def _alive_workers(*, excluding: threading.Thread | None = None) -> list[threading.Thread]:
    return [
        thread
        for thread in _THREADS
        if thread.is_alive() and thread is not excluding
    ]


def _replenish_workers_locked(*, excluding: threading.Thread | None = None) -> None:
    """补齐活着的工人。某个线程退出后，进程里的工人数不能变少。"""
    global _THREADS
    if _STOP.is_set():
        return
    # 正在 finally 里退出的当前线程仍 is_alive，必须排除，否则不会补员。
    _THREADS = _alive_workers(excluding=excluding)
    workers = max(1, int(settings.run_workers))
    while len(_THREADS) < workers:
        index = len(_THREADS)
        thread = threading.Thread(target=_worker_loop, name=f"triage-{index}", daemon=True)
        thread.start()
        _THREADS.append(thread)


def _start_threads_locked() -> None:
    _STOP.clear()
    _replenish_workers_locked()


def _ensure_workers() -> None:
    """本进程的工人从 SQLite 领任务。换了数据目录就先放下上一进程留下的领取标记。"""
    global _RUNNER_DB
    db_key = str(runtime_db_path())
    with _EXEC_LOCK:
        if _RUNNER_DB != db_key:
            _stop_threads_locked()
            mark_interrupted_runs()
            release_running_claims()
            _RUNNER_DB = db_key
        _start_threads_locked()
    _WAKE.set()


def drain_jobs() -> None:
    """等已受理的运行结束，再停掉工人。测试在换数据库之前调用。"""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and active_job_count() > 0:
        _WAKE.set()
        time.sleep(0.01)
    with _EXEC_LOCK:
        _stop_threads_locked()
    _RUNNING.clear()
    checkpoint_runtime()
    from ticket_triage.tools.tickets import checkpoint_store

    checkpoint_store()


def recover_interrupted_runs() -> int:
    """启动时恢复上次没跑完的单。没有请求正文的旧行标成失败。"""
    _ensure_workers()
    return active_job_count()


def _failed_result(ticket_id: str, trace_id: str) -> RunResult:
    return RunResult(
        ticket_id=ticket_id,
        status="failed",
        errors=["internal_error"],
        trace_id=trace_id,
    )


def _node(name: str) -> Callable[[TriageState], dict]:
    nodes = {
        "ingest": ingest_node,
        "classify": classify_node,
        "act": act_node,
        "draft": draft_node,
        "hitl_gate": hitl_gate_node,
        "finalize": finalize_node,
    }
    fn = nodes.get(name)
    if fn is None:
        raise RuntimeError(f"unknown node {name}")
    return fn


def _merge_state(state: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """和 LangGraph 一样：errors 追加，其余字段覆盖。"""
    merged = dict(state)
    for key, value in update.items():
        if key == "errors":
            merged["errors"] = list(merged.get("errors") or []) + list(value or [])
        else:
            merged[key] = value
    return merged


def _successor(node: str, state: dict[str, Any]) -> str | None:
    """下一个节点。和 build_graph 的边一致。"""
    if node == "ingest":
        return "classify"
    if node == "classify":
        return "act"
    if node == "act":
        return _route_after_act(state)
    if node == "draft":
        return "hitl_gate"
    if node == "hitl_gate":
        if state.get("awaiting_human"):
            return None
        return "finalize"
    if node == "finalize":
        return None
    raise RuntimeError(f"unknown node {node}")


def _fresh_state(req: RunRequest, trace_id: str) -> dict[str, Any]:
    from ticket_triage.tenants import DEFAULT_TENANT_ID

    ticket = req.ticket
    if not ticket.tenant_id:
        ticket = ticket.model_copy(update={"tenant_id": DEFAULT_TENANT_ID})
    return {
        "ticket": ticket,
        "auto_approve": not settings.require_hitl,
        "errors": [],
        "tool_retries": 0,
        "tool_calls": [],
        "awaiting_human": False,
        "trace_id": trace_id,
    }


def _step_job(job: dict[str, Any]) -> dict[str, Any]:
    """从上次完成的节点接着跑。每完成一步就落盘，进程退出后不重跑这一步。"""
    state = job["state"]
    node = job["next_node"] or ""
    trace_id = str(job["trace_id"])
    if state is not None and not node:
        return state
    if state is None:
        state = _fresh_state(job["request"], trace_id)
        node = node or "ingest"
    elif node:
        logger.info("trace_id=%s resume node=%s", trace_id, node)
    while node:
        out = _traced(node, _node(node))(state)
        state = _merge_state(state, out)
        node = _successor(node, state) or ""
        try:
            saved = save_job_checkpoint(trace_id, job["claim"], state, node)
        except Exception as exc:
            logger.exception("trace_id=%s checkpoint failed", trace_id)
            raise PersistFailure(trace_id) from exc
        if not saved:
            raise ClaimLost(trace_id)
    return state


def advance_run(job: dict[str, Any]) -> RunResult:
    """把一张已领取的单往下推，直到待审、结案或失败。"""
    trace_id = str(job["trace_id"])
    ticket_id = str(job["ticket_id"])
    try:
        state = trace_run(trace_id, job["request"].ticket, lambda: _step_job(job))
    except (ClaimLost, PersistFailure):
        raise
    except Exception as exc:
        logger.exception("trace_id=%s run failed", trace_id)
        raise WorkflowFailure(trace_id) from exc
    result = _result_from_state(ticket_id, state, trace_id)
    if result.awaiting_human:
        tenant_id = _tenant_of(state.get("ticket") if isinstance(state, dict) else None)
        if job.get("request") is not None:
            tenant_id = _tenant_of(job["request"].ticket, explicit=tenant_id)
        key = _scope_key(tenant_id, ticket_id)
        with _ticket_lock(ticket_id, tenant_id=tenant_id):
            _PENDING[key] = state
            try:
                save_pending(ticket_id, trace_id, state, tenant_id=tenant_id)
            except Exception as exc:
                logger.exception("trace_id=%s save pending failed", trace_id)
                raise PersistFailure(trace_id) from exc
    return result


def _release_claim(trace_id: str, claim: str) -> None:
    try:
        clear_claim(trace_id, claim)
    except Exception:
        logger.exception("trace_id=%s clear_claim failed", trace_id)


def _finish_claimed(job: dict[str, Any]) -> None:
    """跑完一张已领取的单。落盘失败时放下领取标记，不把单挂死。"""
    trace_id = str(job["trace_id"])
    ticket_id = str(job["ticket_id"])
    claim = str(job["claim"])
    try:
        try:
            result = advance_run(job)
        except ClaimLost:
            logger.warning("trace_id=%s claim lost", trace_id)
            return
        except PersistFailure:
            logger.warning("trace_id=%s persist failed; releasing claim", trace_id)
            _release_claim(trace_id, claim)
            return
        except WorkflowFailure as exc:
            result = _failed_result(ticket_id, exc.trace_id or trace_id)
        except Exception:
            logger.exception("trace_id=%s background run failed", trace_id)
            result = _failed_result(ticket_id, trace_id)
        if result.trace_id != trace_id:
            result = result.model_copy(update={"trace_id": trace_id})
        try:
            save_run(result)
        except Exception:
            # 结果没落盘：放下领取标记，别的工人可从检查点再试。
            logger.exception("trace_id=%s persist result failed", trace_id)
            _release_claim(trace_id, claim)
            return
        _release_claim(trace_id, claim)
        log_event(
            "run_done",
            trace_id=trace_id,
            ticket_id=ticket_id,
            queue_depth=active_job_count(),
            errors=list(result.errors or []),
            status=result.status,
        )
    finally:
        with _ticket_lock(ticket_id):
            if _RUNNING.get(ticket_id) == trace_id:
                _RUNNING.pop(ticket_id, None)


def _worker_loop() -> None:
    try:
        while not _STOP.is_set():
            try:
                job = claim_next_job()
            except Exception:
                logger.exception("claim failed")
                _WAKE.wait(0.2)
                continue
            if job is None:
                try:
                    from ticket_triage.integrations.callback import process_due_callbacks

                    process_due_callbacks(limit=5)
                except Exception:
                    logger.exception("closure callback drain failed")
                _WAKE.wait(0.2)
                _WAKE.clear()
                continue
            try:
                _finish_claimed(job)
            except Exception:
                logger.exception("trace_id=%s worker finish crashed", job.get("trace_id"))
                _release_claim(str(job["trace_id"]), str(job["claim"]))
    except BaseException:
        # 未被 Exception 覆盖的退出也走补员；停机时的 _STOP 路径不在这里。
        if not _STOP.is_set():
            logger.exception("worker aborted")
    finally:
        # 非正常退出时立刻补员，进程里的工人数不能变少。
        if not _STOP.is_set():
            logger.error("worker exited unexpectedly; replenishing")
            try:
                with _EXEC_LOCK:
                    _replenish_workers_locked(excluding=threading.current_thread())
            except Exception:
                logger.exception("worker replenish failed")
            _WAKE.set()


def submit_triage(req: RunRequest) -> RunResult:
    """受理一次分流并立刻返回。任务写在 SQLite，工人按受理顺序领取。"""
    from ticket_triage.tenants import context_for

    _ensure_workers()
    ticket_id = req.ticket.ticket_id
    tenant_id = _tenant_of(req.ticket)
    # 服务端以鉴权写入的 tenant_id 为准
    if req.ticket.tenant_id != tenant_id:
        req = req.model_copy(
            update={"ticket": req.ticket.model_copy(update={"tenant_id": tenant_id})}
        )
    ctx = context_for(tenant_id)
    queue_limit = ctx.queue_limit
    key = _scope_key(tenant_id, ticket_id)
    with _ticket_lock(ticket_id, tenant_id=tenant_id):
        existing_pending = _pending_result(ticket_id, tenant_id=tenant_id)
        if existing_pending is not None:
            return existing_pending
        stored = gateway.get_ticket(ticket_id, tenant_id=tenant_id)
        if stored is not None and stored["status"] in {"resolved", "escalated"}:
            return RunResult(
                ticket_id=ticket_id,
                status=stored["status"],
                errors=["already_closed"],
            )
        trace_id = str(uuid.uuid4())
        try:
            accepted = accept_run(
                ticket_id,
                trace_id,
                req.model_dump(mode="json"),
                queue_limit,
                tenant_id=tenant_id,
            )
        except QueueFull:
            log_event(
                "queue_full",
                level=logging.WARNING,
                ticket_id=ticket_id,
                queue_depth=active_job_count(),
                stub_fallback=False,
                tool_error="",
                limit=queue_limit,
                tenant_id=tenant_id,
            )
            return RunResult(ticket_id=ticket_id, status="rejected", errors=["queue_full"])
        _RUNNING[key] = accepted.trace_id or trace_id
    _WAKE.set()
    if accepted.trace_id == trace_id:
        log_event(
            "accepted",
            trace_id=trace_id,
            ticket_id=ticket_id,
            queue_depth=active_job_count(),
            stub_fallback=False,
            tool_error="",
            tenant_id=tenant_id,
        )
    return accepted


def get_run(trace_id: str) -> RunResult | None:
    """读取一次 HTTP 运行。还没有这条记录时返回 None。"""
    return load_run(trace_id)


def run_shadow_triage(req: RunRequest, *, trace_id: str | None = None) -> RunResult:
    """影子评测：分类 → 工具 → 起草，只打分不写状态。

    不入库、不写 pending、不 finalize、不改 tickets 状态。
    只读工具（检索 / 查历史）仍可访问现有库。
    """
    from ticket_triage.graph.nodes import act_node, classify_node, draft_node

    ticket_id = req.ticket.ticket_id
    tenant_id = _tenant_of(req.ticket)
    if not req.ticket.tenant_id:
        req = req.model_copy(
            update={"ticket": req.ticket.model_copy(update={"tenant_id": tenant_id})}
        )
    trace_id = trace_id or str(uuid.uuid4())
    state: dict[str, Any] = _fresh_state(req, trace_id)
    state["status"] = "shadow"
    try:
        state = _merge_state(state, _traced("classify", classify_node)(state))
        while True:
            state = _merge_state(state, _traced("act", act_node)(state))
            if _route_after_act(state) != "act":
                break
        state = _merge_state(state, _traced("draft", draft_node)(state))
        state["awaiting_human"] = False
        state["status"] = "shadow_scored"
        return _result_from_state(ticket_id, state, trace_id)
    except Exception as exc:
        logger.exception("trace_id=%s shadow run failed", trace_id)
        raise WorkflowFailure(trace_id) from exc


def run_triage(
    req: RunRequest,
    *,
    trace_id: str | None = None,
    hold_running: bool = False,
) -> RunResult:
    """执行一次分流。同一张待审单直接返回原结果；已结案的不重跑、不改历史。

    分类和起草在工单锁外运行。锁只包住「有没有在跑」的判断，同一张单的再次提交可以立刻返回。
    """
    ticket_id = req.ticket.ticket_id
    tenant_id = _tenant_of(req.ticket)
    if not req.ticket.tenant_id:
        req = req.model_copy(
            update={"ticket": req.ticket.model_copy(update={"tenant_id": tenant_id})}
        )
    key = _scope_key(tenant_id, ticket_id)
    owned = False
    with _ticket_lock(ticket_id, tenant_id=tenant_id):
        existing_pending = _pending_result(ticket_id, tenant_id=tenant_id)
        if existing_pending is not None:
            return existing_pending
        stored = gateway.get_ticket(ticket_id, tenant_id=tenant_id)
        if stored is not None and stored["status"] in {"resolved", "escalated"}:
            return RunResult(
                ticket_id=ticket_id,
                status=stored["status"],
                errors=["already_closed"],
            )
        trace_id = trace_id or str(uuid.uuid4())
        running = _RUNNING.get(key)
        if running is not None and running != trace_id:
            current = load_run(running)
            if current is not None:
                return current
            return RunResult(ticket_id=ticket_id, status="running", trace_id=running)
        _RUNNING[key] = trace_id
        owned = True
    try:
        graph = _get_graph()
        initial: TriageState = _fresh_state(req, trace_id)
        final = run_observed(graph, initial, trace_id, req.ticket)
        final["trace_id"] = trace_id
        result = _result_from_state(ticket_id, final, trace_id)
        with _ticket_lock(ticket_id, tenant_id=tenant_id):
            if result.awaiting_human:
                _PENDING[key] = final
                save_pending(ticket_id, trace_id, final, tenant_id=tenant_id)
        return result
    except Exception as exc:
        logger.exception("trace_id=%s run failed", trace_id)
        raise WorkflowFailure(trace_id) from exc
    finally:
        if owned and not hold_running:
            with _ticket_lock(ticket_id, tenant_id=tenant_id):
                if _RUNNING.get(key) == trace_id:
                    _RUNNING.pop(key, None)


def resume_with_human(
    decision: HumanDecision,
    *,
    tenant_id: str | None = None,
) -> RunResult:
    """根据人审决策继续 finalize。内存没有时从 SQLite 恢复。"""
    tid = _tenant_of(explicit=tenant_id)
    key = _scope_key(tid, decision.ticket_id)
    with _ticket_lock(decision.ticket_id, tenant_id=tid):
        pending = _PENDING.pop(key, None)
        if pending is None:
            pending = load_pending(decision.ticket_id, tenant_id=tid)
        if pending is None:
            return RunResult(
                ticket_id=decision.ticket_id,
                status="not_found",
                errors=["no_pending_run"],
            )
        # 确保 finalize 带上租户
        ticket = pending.get("ticket")
        if ticket is not None and not getattr(ticket, "tenant_id", None):
            pending["ticket"] = ticket.model_copy(update={"tenant_id": tid})
        trace_id = str(pending.get("trace_id") or uuid.uuid4())
        try:
            pending["human_approved"] = decision.approved
            pending["human_edits"] = decision.edits
            pending["reviewer"] = decision.reviewer
            pending["awaiting_human"] = False
            out = _traced("finalize", finalize_node)(pending)
            pending.update(out)
            archive_pending(decision.ticket_id, trace_id, pending, tenant_id=tid)
            result = _result_from_state(decision.ticket_id, pending, trace_id)
            save_run(result)
            return result
        except Exception as exc:
            logger.exception("trace_id=%s resume failed", trace_id)
            raise WorkflowFailure(trace_id) from exc


def reopen_ticket(
    ticket_id: str,
    reviewer: str,
    *,
    tenant_id: str | None = None,
) -> RunResult:
    """把已结案或已升级的工单放回待审，并在历史里追加一条，不覆盖旧记录。"""
    tid = _tenant_of(explicit=tenant_id)
    with _ticket_lock(ticket_id, tenant_id=tid):
        return _reopen_ticket(ticket_id, reviewer, tenant_id=tid)


def _reopen_ticket(ticket_id: str, reviewer: str, *, tenant_id: str) -> RunResult:
    key = _scope_key(tenant_id, ticket_id)
    if key in _PENDING or load_pending(ticket_id, tenant_id=tenant_id) is not None:
        return RunResult(ticket_id=ticket_id, status="already_pending", errors=["already_pending"])
    ticket = gateway.get_ticket(ticket_id, tenant_id=tenant_id)
    if ticket is None:
        return RunResult(ticket_id=ticket_id, status="not_found", errors=["ticket_not_found"])
    if ticket["status"] not in {"resolved", "escalated"}:
        return RunResult(ticket_id=ticket_id, status="not_closed", errors=["not_closed"])
    state = load_closed(ticket_id, tenant_id=tenant_id)
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
                tenant_id=tenant_id,
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
        t = state.get("ticket")
        if t is not None and not getattr(t, "tenant_id", None):
            state["ticket"] = t.model_copy(update={"tenant_id": tenant_id})
    trace_id = str(state.get("trace_id") or uuid.uuid4())
    try:
        state["trace_id"] = trace_id
        state["awaiting_human"] = True
        state["human_approved"] = None
        state["human_edits"] = None
        state["reviewer"] = reviewer
        state["status"] = "awaiting_human"
        _PENDING[key] = state
        save_pending(ticket_id, trace_id, state, tenant_id=tenant_id)
        gateway.update_ticket_status(
            ticket_id, "pending", "重新送审", reviewer=reviewer, tenant_id=tenant_id
        )
        result = _result_from_state(ticket_id, state, trace_id)
        save_run(result)
        return result
    except Exception as exc:
        logger.exception("trace_id=%s reopen failed", trace_id)
        raise WorkflowFailure(trace_id) from exc


def read_trace(trace_id: str) -> dict[str, Any]:
    """读取一次运行的节点追踪。"""
    return get_trace(trace_id)
