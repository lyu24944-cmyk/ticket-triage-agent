"""进行中的单落在 SQLite 里，进程退出后从最后完成的节点继续。"""

import sqlite3
import threading
import time

from ticket_triage.config import settings
from ticket_triage.graph.workflow import read_trace, recover_interrupted_runs, submit_triage
from ticket_triage.models import Category, Classification, Priority, RunRequest, TicketIn
from ticket_triage.runtime import (
    load_run,
    mark_interrupted_runs,
    read_job,
    runtime_db_path,
    stage_running_job,
)
from ticket_triage.tools import gateway


def _request(ticket_id: str) -> RunRequest:
    return RunRequest(
        ticket=TicketIn(
            ticket_id=ticket_id,
            subject="紧急退款",
            body="我被重复扣款，需要退款",
        )
    )


def _wait(trace_id: str, timeout: float = 5):
    deadline = time.monotonic() + timeout
    loaded = load_run(trace_id)
    while time.monotonic() < deadline:
        loaded = load_run(trace_id)
        if loaded is not None and loaded.status != "running":
            return loaded
        time.sleep(0.02)
    raise AssertionError(f"运行没有结束: {loaded}")


def test_legacy_running_row_without_request_still_fails() -> None:
    path = runtime_db_path()
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE run_jobs (
            trace_id TEXT PRIMARY KEY,
            ticket_id TEXT NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO run_jobs (trace_id, ticket_id, status, result_json, updated_at) VALUES (?, ?, ?, ?, ?)",
        (
            "trace-old",
            "old-1",
            "running",
            '{"ticket_id":"old-1","status":"running","errors":[],"awaiting_human":false,"tool_calls":[],"trace_id":"trace-old"}',
            "2020-01-01T00:00:00Z",
        ),
    )
    conn.commit()
    conn.close()

    assert mark_interrupted_runs() == 1
    loaded = load_run("trace-old")
    assert loaded is not None
    assert loaded.status == "failed"
    assert loaded.errors == ["run_interrupted"]
    conn = sqlite3.connect(path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(run_jobs)")}
    versions = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    conn.close()
    assert {
        "request_json",
        "checkpoint_json",
        "next_node",
        "claim_token",
        "created_at",
    } <= columns
    assert versions == {1, 2, 3}


def test_checkpoint_is_saved_before_the_next_node(monkeypatch) -> None:
    from ticket_triage.graph import workflow

    seen: dict = {}
    real = workflow.classify_node

    def classify(state):
        row = read_job(state["trace_id"])
        assert row is not None
        seen["next"] = row["next_node"]
        seen["status"] = row["state"]["status"]
        return real(state)

    monkeypatch.setattr(workflow, "classify_node", classify)
    accepted = submit_triage(_request("chk-1"))
    loaded = _wait(accepted.trace_id or "")
    assert loaded.status == "awaiting_human"
    assert seen == {"next": "classify", "status": "ingested"}


def test_restart_resumes_from_draft_and_skips_finished_nodes(monkeypatch) -> None:
    from ticket_triage.graph import workflow

    ticket_id = "resume-draft"
    trace_id = "trace-resume-draft"
    req = _request(ticket_id)
    gateway.seed_ticket(ticket_id, req.ticket.subject, req.ticket.body)
    state = {
        "ticket": req.ticket,
        "classification": Classification(
            priority=Priority.high,
            category=Category.billing,
            rationale="已分类",
        ),
        "kb_hits": [{"id": "kb-billing-refund", "title": "退款", "body": "重复扣款可以退。"}],
        "tool_calls": [{"name": "finish", "arguments": {}, "ok": True, "error": None}],
        "errors": [],
        "tool_retries": 0,
        "awaiting_human": False,
        "auto_approve": False,
        "trace_id": trace_id,
        "status": "enriched",
    }

    def already_done(_state):
        raise AssertionError("finished node ran again")

    monkeypatch.setattr(workflow, "ingest_node", already_done)
    monkeypatch.setattr(workflow, "classify_node", already_done)
    monkeypatch.setattr(workflow, "act_node", already_done)
    stage_running_job(
        trace_id=trace_id,
        ticket_id=ticket_id,
        request=req.model_dump(mode="json"),
        state=state,
        next_node="draft",
        claim_token="dead-worker",
    )
    assert mark_interrupted_runs() == 0
    held = load_run(trace_id)
    assert held is not None and held.status == "running"
    recover_interrupted_runs()
    loaded = _wait(trace_id)
    assert loaded.status == "awaiting_human"
    assert loaded.draft is not None
    assert loaded.draft.citations == ["kb-billing-refund"]
    assert [span["node"] for span in read_trace(trace_id)["spans"]] == ["draft", "hitl_gate"]


def test_queue_full_rejects_a_new_ticket(monkeypatch, service_headers) -> None:
    from fastapi.testclient import TestClient

    from ticket_triage.api.main import app

    monkeypatch.setattr(settings, "run_queue_limit", 1)
    release = threading.Event()
    started = threading.Event()

    def blocked(job):
        started.set()
        release.wait(5)
        from ticket_triage.models import RunResult

        return RunResult(
            ticket_id=job["ticket_id"],
            status="awaiting_human",
            awaiting_human=True,
            trace_id=job["trace_id"],
        )

    monkeypatch.setattr("ticket_triage.graph.workflow.advance_run", blocked)
    client = TestClient(app)
    try:
        first = client.post(
            "/runs",
            json=_request("q-full-1").model_dump(mode="json"),
            headers=service_headers,
        )
        assert first.status_code == 202
        assert started.wait(5)
        second = client.post(
            "/runs",
            json=_request("q-full-2").model_dump(mode="json"),
            headers=service_headers,
        )
        assert second.status_code == 429
        assert second.json()["errors"] == ["queue_full"]
        again = client.post(
            "/runs",
            json=_request("q-full-1").model_dump(mode="json"),
            headers=service_headers,
        )
        assert again.status_code == 202
        assert again.json()["trace_id"] == first.json()["trace_id"]
    finally:
        release.set()


def test_jobs_run_in_accept_order(monkeypatch) -> None:
    from ticket_triage.graph import workflow

    monkeypatch.setattr(settings, "run_workers", 1)
    order: list[str] = []
    release = threading.Event()
    entered = threading.Event()
    original = workflow.advance_run

    def wrapped(job):
        order.append(job["ticket_id"])
        if len(order) == 1:
            entered.set()
            release.wait(5)
        return original(job)

    monkeypatch.setattr(workflow, "advance_run", wrapped)
    try:
        first = submit_triage(_request("q-order-1"))
        assert entered.wait(5)
        second = submit_triage(_request("q-order-2"))
    finally:
        release.set()
    assert _wait(first.trace_id or "").status == "awaiting_human"
    assert _wait(second.trace_id or "").status == "awaiting_human"
    assert order == ["q-order-1", "q-order-2"]


def test_same_second_jobs_claim_in_insert_order(monkeypatch) -> None:
    """同一秒受理的多张单按入库顺序领，不按随机 trace_id。"""
    from ticket_triage.graph import workflow
    from ticket_triage.runtime import claim_next_job, runtime_db_path

    monkeypatch.setattr(settings, "run_workers", 1)
    workflow.drain_jobs()
    fixed = "2026-10-27T07:00:00.000Z"
    # trace_id 故意逆序，旧逻辑 ORDER BY created_at, trace_id 会领成 t3→t0
    tickets = [
        ("zzzz-t0", "t0"),
        ("yyyy-t1", "t1"),
        ("xxxx-t2", "t2"),
        ("wwww-t3", "t3"),
    ]
    for trace_id, ticket_id in tickets:
        stage_running_job(
            trace_id=trace_id,
            ticket_id=ticket_id,
            request=_request(ticket_id).model_dump(mode="json"),
            state=None,
            next_node="ingest",
        )
    conn = sqlite3.connect(runtime_db_path())
    conn.execute("UPDATE run_jobs SET created_at = ?", (fixed,))
    conn.commit()
    conn.close()

    # 领完不放回，否则同一张会再被领到
    order: list[str] = []
    for _ in tickets:
        job = claim_next_job()
        assert job is not None
        order.append(str(job["ticket_id"]))
    assert order == ["t0", "t1", "t2", "t3"]
    assert claim_next_job() is None


def test_persist_failure_releases_claim_for_retry(monkeypatch) -> None:
    """保存最终结果失败时放下领取标记，单仍可被再领，工人线程不退出。"""
    from ticket_triage.graph import workflow
    from ticket_triage.runtime import read_job

    monkeypatch.setattr(settings, "run_workers", 1)
    calls = {"n": 0}
    real_save = workflow.save_run

    def flaky(result):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        return real_save(result)

    monkeypatch.setattr(workflow, "save_run", flaky)
    accepted = submit_triage(_request("persist-fail-1"))
    trace_id = accepted.trace_id or ""
    loaded = _wait(trace_id, timeout=8)
    assert loaded.status == "awaiting_human"
    assert calls["n"] >= 2
    row = read_job(trace_id)
    assert row is not None
    assert row["claim"] == ""


def test_checkpoint_failure_releases_claim_for_retry(monkeypatch) -> None:
    """写检查点抛错时放下领取标记，不标 failed，别的工人从上次检查点接着跑。"""
    from ticket_triage.graph import workflow
    from ticket_triage.runtime import read_job

    monkeypatch.setattr(settings, "run_workers", 1)
    calls = {"n": 0}
    real_checkpoint = workflow.save_job_checkpoint

    def flaky(trace_id, claim, state, next_node):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        return real_checkpoint(trace_id, claim, state, next_node)

    monkeypatch.setattr(workflow, "save_job_checkpoint", flaky)
    accepted = submit_triage(_request("ckpt-fail-1"))
    trace_id = accepted.trace_id or ""
    loaded = _wait(trace_id, timeout=8)
    assert loaded.status == "awaiting_human"
    assert loaded.errors == []
    assert calls["n"] >= 2
    row = read_job(trace_id)
    assert row is not None
    assert row["claim"] == ""


def test_dead_worker_is_replaced(monkeypatch) -> None:
    """工人线程退出后，进程里的活工人数补回 RUN_WORKERS。"""
    from ticket_triage.graph import workflow

    monkeypatch.setattr(settings, "run_workers", 2)
    workflow.drain_jobs()
    recover_interrupted_runs()
    with workflow._EXEC_LOCK:
        assert len(workflow._alive_workers()) == 2

    killed = threading.Event()
    real_claim = workflow.claim_next_job

    def claim():
        if not killed.is_set():
            killed.set()
            # BaseException 不被工人循环的 except Exception 吃掉，走 finally 补员。
            raise BaseException("worker suicide")
        return real_claim()

    monkeypatch.setattr(workflow, "claim_next_job", claim)
    workflow._WAKE.set()
    deadline = time.monotonic() + 3
    alive = 0
    while time.monotonic() < deadline:
        with workflow._EXEC_LOCK:
            alive = len(workflow._alive_workers())
        if killed.is_set() and alive == 2:
            break
        time.sleep(0.02)
        workflow._WAKE.set()
    with workflow._EXEC_LOCK:
        alive = len(workflow._alive_workers())
    assert killed.is_set()
    assert alive == 2


def test_finalize_retry_does_not_append_another_history_row() -> None:
    from ticket_triage.graph.nodes import finalize_node, ingest_node
    from ticket_triage.models import DraftReply

    ticket = TicketIn(ticket_id="fin-1", subject="紧急退款", body="我被重复扣款，需要退款")
    state = {
        "ticket": ticket,
        "draft": DraftReply(body="可以退款。", citations=["kb-billing-refund"], confidence=0.7),
        "human_approved": True,
        "reviewer": "ada",
    }
    ingest_node(state)
    assert finalize_node(state)["status"] == "resolved"
    assert finalize_node(state)["status"] == "resolved"
    history = gateway.get_ticket("fin-1")["history"]
    assert [item["status"] for item in history] == ["open", "resolved"]
