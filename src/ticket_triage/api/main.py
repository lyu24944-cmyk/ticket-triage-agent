"""HTTP API：健康检查、发起运行、人审恢复、审核台。"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from ticket_triage import __version__
from ticket_triage.config import settings
from ticket_triage.graph.workflow import read_trace, reopen_ticket, resume_with_human, run_triage
from ticket_triage.models import HumanDecision, RunRequest, RunResult
from ticket_triage.runtime import list_pending
from ticket_triage.tools import gateway

app = FastAPI(title=settings.app_name, version=__version__)
_REVIEW_PAGE = Path(__file__).with_name("review.html")


@app.get("/health")
def health() -> dict:
    """健康检查。"""
    return {"ok": True, "version": __version__, "llm_provider": settings.llm_provider}


@app.get("/review")
def review_page() -> FileResponse:
    """人工审核台。"""
    return FileResponse(_REVIEW_PAGE, headers={"Cache-Control": "no-cache"})


@app.get("/tickets")
def tickets() -> list[dict]:
    """已结案或已升级的工单，含全部状态历史。"""
    return gateway.list_tickets(["resolved", "escalated"])


@app.post("/tickets/{ticket_id}/reopen", response_model=RunResult)
def reopen(ticket_id: str) -> RunResult:
    """把已处理工单放回待审，保留原来的升级或结案记录。"""
    return reopen_ticket(ticket_id)


@app.get("/runs/pending")
def pending_runs() -> list[dict]:
    """当前停在人工审核的工单。"""
    return list_pending()


@app.post("/runs", response_model=RunResult)
def create_run(req: RunRequest) -> RunResult:
    """创建一次工单分流运行。"""
    return run_triage(req)


@app.post("/runs/human", response_model=RunResult)
def human_decision(decision: HumanDecision) -> RunResult:
    """提交人工审核结果并继续流程。"""
    return resume_with_human(decision)


@app.get("/traces/{trace_id}")
def trace(trace_id: str) -> dict:
    """查看一次运行各节点的耗时与状态。"""
    return read_trace(trace_id)


def run() -> None:
    """入口：启动 uvicorn。"""
    import uvicorn

    uvicorn.run("ticket_triage.api.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    run()
