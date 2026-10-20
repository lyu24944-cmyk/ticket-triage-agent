"""HTTP API：健康检查、发起运行、人审恢复。"""

from fastapi import FastAPI

from ticket_triage import __version__
from ticket_triage.config import settings
from ticket_triage.graph.workflow import resume_with_human, run_triage
from ticket_triage.models import HumanDecision, RunRequest, RunResult

app = FastAPI(title=settings.app_name, version=__version__)


@app.get("/health")
def health() -> dict:
    """健康检查。"""
    return {"ok": True, "version": __version__, "llm_provider": settings.llm_provider}


@app.post("/runs", response_model=RunResult)
def create_run(req: RunRequest) -> RunResult:
    """创建一次工单分流运行。"""
    return run_triage(req)


@app.post("/runs/human", response_model=RunResult)
def human_decision(decision: HumanDecision) -> RunResult:
    """提交人工审核结果并继续流程。"""
    return resume_with_human(decision)


def run() -> None:
    """入口：启动 uvicorn。"""
    import uvicorn

    uvicorn.run("ticket_triage.api.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    run()
