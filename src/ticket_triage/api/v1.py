"""对外 API /v1：版本化错误信封、幂等、分页、Webhook 进线。"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse

from ticket_triage.api.errors import v1_error
from ticket_triage.api.handlers import (
    create_run,
    human_decide,
    list_closed_tickets,
    list_pending_runs,
    read_run_result,
    read_trace_for,
    reopen,
    request_hash,
    respond_legacy,
)
from ticket_triage.audit import record_audit
from ticket_triage.auth_jwt import (
    ReviewPrincipal,
    ReviewTokenError,
    mint_review_token,
    oidc_enabled,
    review_auth_mode,
    review_jwt_secret,
    verify_review_token,
)
from ticket_triage.dsar import retention_status
from ticket_triage.integrations import email_gateway, zendesk
from ticket_triage.models import (
    TICKET_ID_PATTERN,
    HumanDecision,
    ReopenRequest,
    ReviewTokenRequest,
    RunRequest,
)
from ticket_triage.runtime import lookup_idempotency, save_idempotency
from ticket_triage.tenants import (
    TenantContext,
    find_tenant_by_webhook_body_hmac,
    resolve_review_key,
    resolve_service_key,
)

router = APIRouter(prefix="/v1")


class V1Failure(Exception):
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self.payload = payload
        super().__init__(status_code)


def _fail(code: str, *, trace_id: str | None = None, message: str | None = None) -> None:
    status, body = v1_error(code, trace_id=trace_id, message=message)
    raise V1Failure(status, body)


def require_service_tenant(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> TenantContext:
    from ticket_triage.secrets import service_api_keys
    from ticket_triage.tenants import load_tenants

    ctx = resolve_service_key(x_api_key)
    if ctx is not None:
        return ctx
    configured = bool(service_api_keys()) or any(
        t.service_api_key for t in load_tenants().values()
    )
    if not configured:
        _fail("service_auth_not_configured")
    _fail("service_auth_failed")


def require_mint_tenant(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> TenantContext:
    from ticket_triage.secrets import review_api_keys
    from ticket_triage.tenants import load_tenants

    ctx = resolve_review_key(x_api_key)
    if ctx is not None:
        return ctx
    configured = bool(review_api_keys()) or any(
        t.review_api_key for t in load_tenants().values()
    )
    if not configured:
        _fail("review_auth_not_configured")
    _fail("review_auth_failed")


def require_reviewer(
    authorization: Annotated[str | None, Header()] = None,
) -> ReviewPrincipal:
    if not oidc_enabled() and not review_jwt_secret():
        _fail("review_jwt_not_configured")
    raw = (authorization or "").strip()
    if not raw.lower().startswith("bearer "):
        _fail("review_token_missing")
    token = raw[7:].strip()
    if not token:
        _fail("review_token_missing")
    try:
        return verify_review_token(token)
    except ReviewTokenError as exc:
        code = str(exc) or "review_token_invalid"
        if code not in {
            "review_token_expired",
            "review_token_missing",
            "review_jwt_not_configured",
            "review_mint_disabled",
            "review_oidc_not_configured",
            "review_oidc_jwks",
        }:
            code = "review_token_invalid"
        _fail(code)


def _run_status_envelope(status_code: int, payload: dict, *, trace_id: str | None) -> JSONResponse:
    if status_code >= 400 and "error" not in payload and payload.get("errors"):
        code = payload["errors"][0]
        status, body = v1_error(code, trace_id=trace_id or payload.get("trace_id"))
        if "ticket_id" in payload:
            body["ticket_id"] = payload["ticket_id"]
        if "status" in payload:
            body["status"] = payload["status"]
        return JSONResponse(status_code=status, content=body)
    return JSONResponse(status_code=status_code, content=payload)


@router.get("/auth/review/mode")
def review_mode() -> dict:
    from ticket_triage.config import settings

    return {
        "mode": review_auth_mode(),
        "oidc_issuer": (settings.review_oidc_issuer or "").rstrip("/") or None,
        "oidc_audience": settings.review_oidc_audience if oidc_enabled() else None,
    }


@router.post("/auth/review/token")
def issue_review_token(
    body: ReviewTokenRequest,
    tenant: TenantContext = Depends(require_mint_tenant),
) -> dict:
    if oidc_enabled():
        _fail("review_mint_disabled")
    if not review_jwt_secret():
        _fail("review_jwt_not_configured")
    try:
        token = mint_review_token(
            body.sub, ttl_seconds=body.ttl_seconds, tenant_id=tenant.tenant_id
        )
    except ReviewTokenError as exc:
        _fail(str(exc) or "review_token_invalid")
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


@router.get("/privacy/retention")
def privacy_retention(principal: ReviewPrincipal = Depends(require_reviewer)) -> dict:
    _ = principal
    return retention_status()


@router.get("/privacy/tickets/{ticket_id}", response_model=None)
def privacy_export(
    ticket_id: Annotated[str, PathParam(min_length=1, max_length=64, pattern=TICKET_ID_PATTERN)],
    principal: ReviewPrincipal = Depends(require_reviewer),
):
    from ticket_triage.tools import tickets as tickets_mod

    row = tickets_mod.get_ticket(ticket_id, tenant_id=principal.tenant_id)
    if row is None:
        status, body = v1_error("ticket_not_found")
        return JSONResponse(status_code=status, content=body)
    payload = export_ticket(ticket_id)  # uses default tenant in dsar — patch below
    # 直接用租户感知结果
    from ticket_triage.runtime import load_pending

    payload = {
        "ticket_id": ticket_id,
        "tenant_id": principal.tenant_id,
        "ticket": row,
        "pending_run": load_pending(ticket_id, tenant_id=principal.tenant_id),
    }
    record_audit(
        actor=principal.subject,
        action="privacy.export",
        ticket_id=ticket_id,
        detail={"tenant_id": principal.tenant_id},
    )
    return payload


@router.delete("/privacy/tickets/{ticket_id}")
def privacy_erase(
    ticket_id: Annotated[str, PathParam(min_length=1, max_length=64, pattern=TICKET_ID_PATTERN)],
    principal: ReviewPrincipal = Depends(require_reviewer),
) -> dict:
    # dsar.erase_ticket 仍按 ticket_id；补租户删除
    from ticket_triage.runtime import purge_ticket_runtime
    from ticket_triage.tools import tickets as tickets_mod
    import hashlib

    before = tickets_mod.get_ticket(ticket_id, tenant_id=principal.tenant_id)
    body_fp = subject_fp = None
    if before is not None:
        body_fp = hashlib.sha256(str(before.get("body") or "").encode()).hexdigest()
        subject_fp = hashlib.sha256(str(before.get("subject") or "").encode()).hexdigest()
    tickets_mod.delete_ticket(ticket_id, tenant_id=principal.tenant_id)
    purged = purge_ticket_runtime(ticket_id, tenant_id=principal.tenant_id)
    after = tickets_mod.get_ticket(ticket_id, tenant_id=principal.tenant_id)
    proof = {
        "existed": before is not None,
        "subject_sha256": subject_fp,
        "body_sha256": body_fp,
        "runtime_rows_touched": purged,
        "ticket_gone": after is None,
        "tenant_id": principal.tenant_id,
    }
    audit_id = record_audit(
        actor=principal.subject,
        action="privacy.erase",
        ticket_id=ticket_id,
        detail=proof,
    )
    return {"ticket_id": ticket_id, "deleted": after is None, "audit_id": audit_id, "proof": proof}


@router.get("/audit/events")
def audit_events(
    principal: ReviewPrincipal = Depends(require_reviewer),
    ticket_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    from ticket_triage.audit import list_audit

    record_audit(actor=principal.subject, action="audit.list", ticket_id=ticket_id or "")
    return list_audit(ticket_id=ticket_id, limit=limit)


@router.get("/tickets")
def tickets(
    principal: ReviewPrincipal = Depends(require_reviewer),
    q: str | None = Query(default=None, max_length=200),
    cursor: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    return list_closed_tickets(
        principal, q=q, cursor=cursor, limit=limit, paginate=True
    )


@router.post("/tickets/{ticket_id}/reopen", response_model=None)
def reopen_route(
    ticket_id: Annotated[str, PathParam(min_length=1, max_length=64, pattern=TICKET_ID_PATTERN)],
    body: ReopenRequest,
    principal: ReviewPrincipal = Depends(require_reviewer),
):
    result = reopen(ticket_id, body, principal)
    responded = respond_legacy(result)
    if isinstance(responded, JSONResponse):
        data = json.loads(responded.body.decode())
        return _run_status_envelope(responded.status_code, data, trace_id=result.trace_id)
    return JSONResponse(status_code=200, content=result.model_dump(mode="json"))


@router.get("/runs/pending")
def pending_runs(
    principal: ReviewPrincipal = Depends(require_reviewer),
    q: str | None = Query(default=None, max_length=200),
    cursor: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    return list_pending_runs(
        principal, q=q, cursor=cursor, limit=limit, paginate=True
    )


@router.post("/runs", response_model=None)
def create_run_route(
    req: RunRequest,
    request: Request,
    tenant: TenantContext = Depends(require_service_tenant),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    key = (idempotency_key or "").strip() or None
    if key and len(key) > 128:
        _fail("validation_error", message="Idempotency-Key too long")
    status_code, payload = create_run(req, tenant, idempotency_key=key)
    trace_id = getattr(request.state, "trace_id", None) or payload.get("trace_id")
    return _run_status_envelope(status_code, payload, trace_id=trace_id)


@router.get("/runs/{trace_id}", response_model=None)
def read_run(
    trace_id: Annotated[str, PathParam(min_length=1, max_length=80)],
    _: TenantContext = Depends(require_service_tenant),
):
    result = read_run_result(trace_id)
    if result is None:
        status, body = v1_error("run_not_found", trace_id=trace_id)
        body["status"] = "not_found"
        return JSONResponse(status_code=status, content=body)
    return result


@router.post("/runs/human", response_model=None)
def human_decision(
    decision: HumanDecision,
    principal: ReviewPrincipal = Depends(require_reviewer),
):
    result = human_decide(decision, principal)
    responded = respond_legacy(result)
    if isinstance(responded, JSONResponse):
        data = json.loads(responded.body.decode())
        return _run_status_envelope(responded.status_code, data, trace_id=result.trace_id)
    return JSONResponse(status_code=200, content=result.model_dump(mode="json"))


@router.get("/traces/{trace_id}")
def trace(
    trace_id: str,
    principal: ReviewPrincipal = Depends(require_reviewer),
) -> dict:
    return read_trace_for(principal, trace_id)


def _ingest_webhook(
    *,
    request: Request,
    signature_header: str | None,
    map_fn,
    delivery_fn,
    source: str,
) -> JSONResponse:
    raw = getattr(request.state, "_raw_body", None)
    if raw is None:
        # middleware 可能没缓存；再读会空。调用方应在路由里先读。
        status, body = v1_error("validation_error", message="empty body")
        return JSONResponse(status_code=status, content=body)
    tenant = find_tenant_by_webhook_body_hmac(raw, signature_header)
    if tenant is None:
        status, body = v1_error(
            "webhook_signature_invalid",
            trace_id=getattr(request.state, "trace_id", None),
        )
        return JSONResponse(status_code=status, content=body)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        status, body = v1_error("validation_error", message="invalid JSON")
        return JSONResponse(status_code=status, content=body)
    if not isinstance(payload, dict):
        status, body = v1_error("validation_error", message="JSON object required")
        return JSONResponse(status_code=status, content=body)
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        ticket = map_fn(payload)
        idem_key = delivery_fn(headers, payload)
    except ValueError as exc:
        status, body = v1_error("validation_error", message=str(exc))
        return JSONResponse(status_code=status, content=body)
    ticket = ticket.model_copy(update={"tenant_id": tenant.tenant_id})
    req = RunRequest(ticket=ticket)
    # webhook 幂等
    hit = lookup_idempotency(tenant.tenant_id, idem_key)
    body_hash = request_hash(req.model_dump(mode="json"))
    if hit is not None:
        status_code, stored = hit
        if stored["request_hash"] != body_hash:
            status, err = v1_error("idempotency_conflict")
            return JSONResponse(status_code=status, content=err)
        replay = dict(stored["body"])
        replay["idempotent_replay"] = True
        replay["source"] = source
        return JSONResponse(status_code=status_code, content=replay)
    status_code, result_body = create_run(req, tenant, idempotency_key=None)
    # 显式用 delivery id 存幂等（create_run 未带 key 时）
    if status_code in {200, 202}:
        save_idempotency(tenant.tenant_id, idem_key, body_hash, status_code, result_body)
    result_body = dict(result_body)
    result_body["source"] = source
    result_body["idempotent_replay"] = False
    return _run_status_envelope(
        status_code, result_body, trace_id=result_body.get("trace_id")
    )


@router.post("/webhooks/zendesk", response_model=None)
async def webhook_zendesk(
    request: Request,
    x_zendesk_webhook_signature: Annotated[
        str | None, Header(alias="X-Zendesk-Webhook-Signature")
    ] = None,
):
    raw = await request.body()
    request.state._raw_body = raw
    return _ingest_webhook(
        request=request,
        signature_header=x_zendesk_webhook_signature,
        map_fn=zendesk.map_zendesk_payload,
        delivery_fn=zendesk.delivery_id,
        source="zendesk",
    )


@router.post("/webhooks/email", response_model=None)
async def webhook_email(
    request: Request,
    x_email_gateway_signature: Annotated[
        str | None, Header(alias="X-Email-Gateway-Signature")
    ] = None,
):
    raw = await request.body()
    request.state._raw_body = raw
    return _ingest_webhook(
        request=request,
        signature_header=x_email_gateway_signature,
        map_fn=email_gateway.map_email_payload,
        delivery_fn=email_gateway.delivery_id,
        source="email_gateway",
    )


@router.get("/errors")
def error_catalog() -> dict[str, Any]:
    """正式错误码表（文档机读）。"""
    from ticket_triage.api.errors import ERROR_CATALOG

    return {
        code: {"http_status": status, "message": message}
        for code, (status, message) in sorted(ERROR_CATALOG.items())
    }
