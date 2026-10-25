"""HTTP API：健康检查、发起运行、人审恢复、审核台。"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Request
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, JSONResponse

from ticket_triage import __version__
from ticket_triage.config import settings
from ticket_triage.graph.workflow import (
    WorkflowFailure,
    drain_jobs,
    get_run,
    read_trace,
    recover_interrupted_runs,
    reopen_ticket,
    resume_with_human,
    submit_triage,
)
from ticket_triage.health import readiness
from ticket_triage.models import (
    TICKET_ID_PATTERN,
    HumanDecision,
    ReopenRequest,
    RunRequest,
    RunResult,
)
from ticket_triage.process_lock import acquire_single_process_lock
from ticket_triage.runtime import list_pending
from ticket_triage.tools import gateway

logger = logging.getLogger("ticket_triage.api")
_REVIEW_PAGE = Path(__file__).with_name("review.html")
_MAX_BODY_BYTES = 64_000
_CLIENT_STATUS = {
    "already_closed": 409,
    "already_pending": 409,
    "not_closed": 409,
    "no_pending_run": 404,
    "ticket_not_found": 404,
}


class ApiFailure(Exception):
    """预期中的 HTTP 失败，响应体是 {"errors": [...]}。"""

    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self.payload = payload
        super().__init__(status_code)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    acquire_single_process_lock()
    recover_interrupted_runs()
    yield
    drain_jobs()


app = FastAPI(title=settings.app_name, version=__version__, lifespan=_lifespan)


@app.middleware("http")
async def request_guard(request: Request, call_next):
    incoming = request.headers.get("x-request-id", "").strip()
    request.state.trace_id = incoming[:80] if incoming else str(uuid.uuid4())
    if request.method in {"POST", "PUT", "PATCH"}:
        body = await request.body()
        if len(body) > _MAX_BODY_BYTES:
            response = JSONResponse(
                status_code=413,
                content={"errors": ["payload_too_large"], "trace_id": request.state.trace_id},
            )
            response.headers["X-Trace-Id"] = request.state.trace_id
            return response
    response = await call_next(request)
    response.headers["X-Trace-Id"] = request.state.trace_id
    return response


@app.exception_handler(ApiFailure)
async def api_failure(_request: Request, exc: ApiFailure) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.payload)


@app.exception_handler(WorkflowFailure)
async def workflow_failure(request: Request, exc: WorkflowFailure) -> JSONResponse:
    request.state.trace_id = exc.trace_id
    logger.error("trace_id=%s workflow failed", exc.trace_id)
    return JSONResponse(
        status_code=500,
        content={"errors": ["internal_error"], "trace_id": exc.trace_id},
    )


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", None) or str(uuid.uuid4())
    request.state.trace_id = trace_id
    logger.exception("trace_id=%s unhandled", trace_id)
    return JSONResponse(
        status_code=500,
        content={"errors": ["internal_error"], "trace_id": trace_id},
    )


def _same_secret(provided: str, expected: str) -> bool:
    return secrets.compare_digest(
        hashlib.sha256(provided.encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    )


def require_review_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """审核读接口和写接口共用一把密钥。"""
    expected = settings.review_api_key
    if not expected:
        raise ApiFailure(401, {"errors": ["review_auth_not_configured"]})
    if not _same_secret(x_api_key or "", expected):
        raise ApiFailure(401, {"errors": ["review_auth_failed"]})


def _respond(result: RunResult) -> RunResult | JSONResponse:
    for error in result.errors:
        code = _CLIENT_STATUS.get(error)
        if code is not None:
            return JSONResponse(status_code=code, content=result.model_dump(mode="json"))
    return result


@app.get("/health")
def health() -> JSONResponse:
    """就绪检查。数据库不可写、锁没拿到、或问答服务不可用时返回 503。"""
    status, body = readiness()
    return JSONResponse(status_code=status, content=body)


@app.get("/review")
def review_page() -> FileResponse:
    """人工审核台。页面本身不含工单；数据接口需要审核密钥。"""
    return FileResponse(_REVIEW_PAGE, headers={"Cache-Control": "no-cache"})


@app.get("/tickets", dependencies=[Depends(require_review_key)])
def tickets() -> list[dict]:
    """已结案或已升级的工单，含全部状态历史。"""
    return gateway.list_tickets(["resolved", "escalated"])


@app.post("/tickets/{ticket_id}/reopen", response_model=RunResult)
def reopen(
    ticket_id: Annotated[
        str,
        PathParam(min_length=1, max_length=64, pattern=TICKET_ID_PATTERN),
    ],
    body: ReopenRequest,
    _: None = Depends(require_review_key),
) -> RunResult | JSONResponse:
    """把已处理工单放回待审，保留原来的升级或结案记录。"""
    return _respond(reopen_ticket(ticket_id, body.reviewer))


@app.get("/runs/pending", dependencies=[Depends(require_review_key)])
def pending_runs() -> list[dict]:
    """当前停在人工审核的工单。"""
    return list_pending()


@app.post("/runs")
def create_run(req: RunRequest) -> JSONResponse:
    """受理一次分流。新运行返回 202，分类和起草在后台进行。"""
    result = submit_triage(req)
    responded = _respond(result)
    if isinstance(responded, JSONResponse):
        return responded
    status_code = 202 if result.status == "running" else 200
    return JSONResponse(status_code=status_code, content=result.model_dump(mode="json"))


@app.get("/runs/{trace_id}", response_model=RunResult)
def read_run(
    trace_id: Annotated[str, PathParam(min_length=1, max_length=80)],
) -> RunResult | JSONResponse:
    """轮询一次运行。trace_id 即查询凭证，不列出其他工单。"""
    result = get_run(trace_id)
    if result is None:
        return JSONResponse(
            status_code=404,
            content={"trace_id": trace_id, "status": "not_found", "errors": ["run_not_found"]},
        )
    return result


@app.post("/runs/human", response_model=RunResult)
def human_decision(
    decision: HumanDecision,
    _: None = Depends(require_review_key),
) -> RunResult | JSONResponse:
    """提交人工审核结果并继续流程。"""
    return _respond(resume_with_human(decision))


@app.get("/traces/{trace_id}", dependencies=[Depends(require_review_key)])
def trace(trace_id: str) -> dict:
    """查看一次运行各节点的耗时与状态。"""
    return read_trace(trace_id)


def run() -> None:
    """入口：单进程启动 uvicorn。"""
    import uvicorn

    uvicorn.run(
        "ticket_triage.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        workers=1,
    )


if __name__ == "__main__":
    run()
