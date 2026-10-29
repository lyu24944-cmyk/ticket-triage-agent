"""HTTP API：健康检查、发起运行、人审恢复、审核台；正式路径见 /v1。"""

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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ticket_triage import __version__
from ticket_triage.api import handlers
from ticket_triage.api.v1 import V1Failure, router as v1_router
from ticket_triage.audit import list_audit, record_audit
from ticket_triage.auth_jwt import (
    ReviewPrincipal,
    ReviewTokenError,
    mint_review_token,
    oidc_enabled,
    review_auth_mode,
    review_jwt_secret,
    verify_review_token,
)
from ticket_triage.config import settings
from ticket_triage.dsar import retention_status
from ticket_triage.graph.workflow import WorkflowFailure, drain_jobs, recover_interrupted_runs
from ticket_triage.health import readiness
from ticket_triage.limits import MAX_BODY_BYTES
from ticket_triage.models import (
    TICKET_ID_PATTERN,
    HumanDecision,
    ReopenRequest,
    ReviewTokenRequest,
    RunRequest,
    RunResult,
)
from ticket_triage.ops_log import configure_ops_logging
from ticket_triage.process_lock import acquire_single_process_lock
from ticket_triage.secret_log import install_secret_redact_filter
from ticket_triage.secrets import review_api_keys, service_api_keys
from ticket_triage.tenants import (
    DEFAULT_TENANT_ID,
    TenantContext,
    resolve_review_key,
    resolve_service_key,
)

logger = logging.getLogger("ticket_triage.api")
_REVIEW_STATIC = Path(__file__).resolve().parent / "static" / "review"
_REVIEW_LEGACY = Path(__file__).with_name("review.html")
_REVIEW_MISSING = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/><title>审核台未构建</title></head>
<body style="font:15px/1.5 sans-serif;padding:2rem;max-width:40rem">
<h1>审核台前端尚未构建</h1>
<p>在仓库根目录执行：</p>
<pre>cd web
npm install
npm run build</pre>
<p>或运行 <code>scripts/build_review.ps1</code>。构建产物会写到
<code>src/ticket_triage/api/static/review/</code>，然后刷新本页。</p>
</body></html>
"""

install_secret_redact_filter()
configure_ops_logging()


class ApiFailure(Exception):
    """预期中的 HTTP 失败，响应体是 {"errors": [...]}。"""

    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self.payload = payload
        super().__init__(status_code)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    configure_ops_logging()
    acquire_single_process_lock()
    recover_interrupted_runs()
    from ticket_triage.tools.tickets import purge_expired_tickets

    purged = purge_expired_tickets()
    if purged:
        logger.info("purged_expired_tickets count=%s", len(purged))
    yield
    drain_jobs()


app = FastAPI(title=settings.app_name, version=__version__, lifespan=_lifespan)
app.include_router(v1_router)


@app.middleware("http")
async def request_guard(request: Request, call_next):
    incoming = request.headers.get("x-request-id", "").strip()
    request.state.trace_id = incoming[:80] if incoming else str(uuid.uuid4())
    if request.method in {"POST", "PUT", "PATCH"}:
        body = await request.body()
        request.state._raw_body = body
        if len(body) > MAX_BODY_BYTES:
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


@app.exception_handler(V1Failure)
async def v1_failure(_request: Request, exc: V1Failure) -> JSONResponse:
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


def require_service_tenant(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> TenantContext:
    """受理密钥 → 租户。兼容单租户 SERVICE_API_KEY 与 TENANTS_JSON。"""
    ctx = resolve_service_key(x_api_key)
    if ctx is not None:
        return ctx
    keys = service_api_keys()
    if not keys:
        raise ApiFailure(401, {"errors": ["service_auth_not_configured"]})
    raise ApiFailure(401, {"errors": ["service_auth_failed"]})


def require_mint_tenant(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> TenantContext:
    ctx = resolve_review_key(x_api_key)
    if ctx is not None:
        return ctx
    keys = review_api_keys()
    if not keys:
        raise ApiFailure(401, {"errors": ["review_auth_not_configured"]})
    raise ApiFailure(401, {"errors": ["review_auth_failed"]})


def require_service_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> TenantContext:
    return require_service_tenant(x_api_key)


def require_mint_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> TenantContext:
    return require_mint_tenant(x_api_key)


def require_reviewer(
    authorization: Annotated[str | None, Header()] = None,
) -> ReviewPrincipal:
    """审核读/写接口：必须持有有效人员 JWT（Bearer）。OIDC 开启时只接受 IdP 令牌。"""
    if not oidc_enabled() and not review_jwt_secret():
        raise ApiFailure(401, {"errors": ["review_jwt_not_configured"]})
    raw = (authorization or "").strip()
    if not raw.lower().startswith("bearer "):
        raise ApiFailure(401, {"errors": ["review_token_missing"]})
    token = raw[7:].strip()
    if not token:
        raise ApiFailure(401, {"errors": ["review_token_missing"]})
    try:
        return verify_review_token(token)
    except ReviewTokenError as exc:
        code = str(exc) or "review_token_invalid"
        raise ApiFailure(401, {"errors": [code]}) from exc


@app.get("/health")
def health() -> JSONResponse:
    """就绪检查。数据库不可写、锁没拿到、或问答服务不可用时返回 503。"""
    status, body = readiness()
    return JSONResponse(status_code=status, content=body)


def _review_index() -> Path | None:
    built = _REVIEW_STATIC / "index.html"
    if built.is_file():
        return built
    if _REVIEW_LEGACY.is_file():
        return _REVIEW_LEGACY
    return None


@app.get("/review")
@app.get("/review/")
def review_page():
    """人工审核台（Vite 构建产物；未构建时提示或回退旧 HTML）。"""
    index = _review_index()
    if index is None:
        return HTMLResponse(_REVIEW_MISSING, status_code=503)
    return FileResponse(index, headers={"Cache-Control": "no-cache"})


@app.get("/review/assets/{asset_path:path}")
def review_asset(asset_path: str):
    """Vite 构建的 JS/CSS。路径限制在 static/review/assets 内。"""
    root = (_REVIEW_STATIC / "assets").resolve()
    target = (root / asset_path).resolve()
    if root not in target.parents and target != root:
        return JSONResponse(status_code=404, content={"errors": ["not_found"]})
    if not target.is_file():
        return JSONResponse(status_code=404, content={"errors": ["not_found"]})
    return FileResponse(target)


@app.get("/auth/review/mode")
def review_mode() -> dict:
    """审核台用来切换 mint / 粘贴 IdP 令牌。不含密钥。"""
    return {
        "mode": review_auth_mode(),
        "oidc_issuer": (settings.review_oidc_issuer or "").rstrip("/") or None,
        "oidc_audience": settings.review_oidc_audience if oidc_enabled() else None,
    }


@app.post("/auth/review/token")
def issue_review_token(
    body: ReviewTokenRequest,
    tenant: TenantContext = Depends(require_mint_key),
) -> dict:
    """用 REVIEW_API_KEY（引导）签发短时人员 JWT。OIDC 开启后关闭本接口。"""
    if oidc_enabled():
        raise ApiFailure(401, {"errors": ["review_mint_disabled"]})
    if not review_jwt_secret():
        raise ApiFailure(401, {"errors": ["review_jwt_not_configured"]})
    try:
        token = mint_review_token(
            body.sub, ttl_seconds=body.ttl_seconds, tenant_id=tenant.tenant_id
        )
    except ReviewTokenError as exc:
        raise ApiFailure(401, {"errors": [str(exc)]}) from exc
    principal = verify_review_token(token)
    record_audit(
        actor=body.sub,
        action="auth.mint_review_token",
        detail={
            "expires_at": principal.expires_at,
            "jti": principal.token_id,
            "tenant_id": tenant.tenant_id,
        },
    )
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_at": principal.expires_at,
        "sub": principal.subject,
        "tenant_id": tenant.tenant_id,
    }


@app.get("/privacy/retention", dependencies=[Depends(require_reviewer)])
def privacy_retention() -> dict:
    """保留策略与是否开启静态加密（可证明配置）。"""
    return retention_status()


@app.get("/privacy/tickets/{ticket_id}", response_model=None)
def privacy_export(
    ticket_id: Annotated[
        str,
        PathParam(min_length=1, max_length=64, pattern=TICKET_ID_PATTERN),
    ],
    principal: ReviewPrincipal = Depends(require_reviewer),
):
    """DSAR 导出：按 ticket_id 导出工单与 pending。"""
    from ticket_triage.runtime import load_pending
    from ticket_triage.tools import tickets as tickets_mod

    row = tickets_mod.get_ticket(
        ticket_id, tenant_id=principal.tenant_id or DEFAULT_TENANT_ID
    )
    if row is None:
        return JSONResponse(status_code=404, content={"errors": ["ticket_not_found"]})
    payload = {
        "ticket_id": ticket_id,
        "ticket": row,
        "pending_run": load_pending(
            ticket_id, tenant_id=principal.tenant_id or DEFAULT_TENANT_ID
        ),
    }
    record_audit(
        actor=principal.subject,
        action="privacy.export",
        ticket_id=ticket_id,
    )
    return payload


@app.delete("/privacy/tickets/{ticket_id}")
def privacy_erase(
    ticket_id: Annotated[
        str,
        PathParam(min_length=1, max_length=64, pattern=TICKET_ID_PATTERN),
    ],
    principal: ReviewPrincipal = Depends(require_reviewer),
) -> dict:
    """DSAR 删除：硬删正文与 runtime 留存，审计留指纹证明。"""
    tid = principal.tenant_id or DEFAULT_TENANT_ID
    # 兼容旧 erase：先按租户删
    from ticket_triage.runtime import purge_ticket_runtime
    from ticket_triage.tools import tickets as tickets_mod
    import hashlib

    before = tickets_mod.get_ticket(ticket_id, tenant_id=tid)
    body_fp = subject_fp = None
    if before is not None:
        body_fp = hashlib.sha256(str(before.get("body") or "").encode()).hexdigest()
        subject_fp = hashlib.sha256(str(before.get("subject") or "").encode()).hexdigest()
    tickets_mod.delete_ticket(ticket_id, tenant_id=tid)
    purged = purge_ticket_runtime(ticket_id, tenant_id=tid)
    after = tickets_mod.get_ticket(ticket_id, tenant_id=tid)
    proof = {
        "existed": before is not None,
        "subject_sha256": subject_fp,
        "body_sha256": body_fp,
        "runtime_rows_touched": purged,
        "ticket_gone": after is None,
        "tenant_id": tid,
    }
    audit_id = record_audit(
        actor=principal.subject,
        action="privacy.erase",
        ticket_id=ticket_id,
        detail=proof,
    )
    return {"ticket_id": ticket_id, "deleted": after is None, "audit_id": audit_id, "proof": proof}


@app.get("/audit/events")
def audit_events(
    principal: ReviewPrincipal = Depends(require_reviewer),
    ticket_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """查询审计（人员令牌）。"""
    record_audit(actor=principal.subject, action="audit.list", ticket_id=ticket_id or "")
    return list_audit(ticket_id=ticket_id, limit=limit)


@app.get("/tickets")
def tickets(principal: ReviewPrincipal = Depends(require_reviewer)) -> list[dict]:
    """已结案或已升级的工单，含全部状态历史。"""
    rows = handlers.list_closed_tickets(principal)
    assert isinstance(rows, list)
    return rows


@app.post("/tickets/{ticket_id}/reopen", response_model=RunResult)
def reopen(
    ticket_id: Annotated[
        str,
        PathParam(min_length=1, max_length=64, pattern=TICKET_ID_PATTERN),
    ],
    body: ReopenRequest,
    principal: ReviewPrincipal = Depends(require_reviewer),
) -> RunResult | JSONResponse:
    """把已处理工单放回待审，保留原来的升级或结案记录。"""
    return handlers.respond_legacy(handlers.reopen(ticket_id, body, principal))


@app.get("/runs/pending")
def pending_runs(principal: ReviewPrincipal = Depends(require_reviewer)) -> list[dict]:
    """当前停在人工审核的工单（读草稿）。"""
    rows = handlers.list_pending_runs(principal)
    assert isinstance(rows, list)
    return rows


@app.post("/runs")
def create_run(
    req: RunRequest,
    tenant: TenantContext = Depends(require_service_key),
) -> JSONResponse:
    """受理一次分流。新运行返回 202。队列满时返回 429。"""
    status_code, payload = handlers.create_run(req, tenant)
    return JSONResponse(status_code=status_code, content=payload)


@app.get("/runs/{trace_id}", response_model=RunResult)
def read_run(
    trace_id: Annotated[str, PathParam(min_length=1, max_length=80)],
    _: TenantContext = Depends(require_service_key),
) -> RunResult | JSONResponse:
    """轮询一次运行。调用方要带受理密钥，trace_id 本身不是凭证。"""
    result = handlers.read_run_result(trace_id)
    if result is None:
        return JSONResponse(
            status_code=404,
            content={"trace_id": trace_id, "status": "not_found", "errors": ["run_not_found"]},
        )
    return result


@app.post("/runs/human", response_model=RunResult)
def human_decision(
    decision: HumanDecision,
    principal: ReviewPrincipal = Depends(require_reviewer),
) -> RunResult | JSONResponse:
    """提交人工审核结果并继续流程。审核人 = JWT sub。"""
    return handlers.respond_legacy(handlers.human_decide(decision, principal))


@app.get("/traces/{trace_id}")
def trace(
    trace_id: str,
    principal: ReviewPrincipal = Depends(require_reviewer),
) -> dict:
    """查看一次运行各节点的耗时与状态。"""
    return handlers.read_trace_for(principal, trace_id)


def run() -> None:
    """入口：单进程启动 uvicorn。

    默认只绑 127.0.0.1（本机或经本机网关反代）。容器内由 Dockerfile
    显式传 --host 0.0.0.0，且不得把 8000 直接暴露到公网，见 docs/GATEWAY.md。
    """
    import os

    import uvicorn

    host = os.environ.get("API_BIND_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.environ.get("API_BIND_PORT", "8000"))
    uvicorn.run(
        "ticket_triage.api.main:app",
        host=host,
        port=port,
        reload=False,
        workers=1,
    )


if __name__ == "__main__":
    run()
