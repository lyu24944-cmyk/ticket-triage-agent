"""共享业务处理：旧路径与 /v1 共用，避免双份逻辑。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi.responses import JSONResponse

from ticket_triage.audit import record_audit
from ticket_triage.auth_jwt import ReviewPrincipal
from ticket_triage.graph.workflow import (
    get_run,
    read_trace,
    reopen_ticket,
    resume_with_human,
    submit_triage,
)
from ticket_triage.models import HumanDecision, ReopenRequest, RunRequest, RunResult
from ticket_triage.runtime import list_pending, lookup_idempotency, save_idempotency
from ticket_triage.tenants import DEFAULT_TENANT_ID, TenantContext
from ticket_triage.tools import gateway

_CLIENT_STATUS = {
    "already_closed": 409,
    "already_pending": 409,
    "not_closed": 409,
    "no_pending_run": 404,
    "ticket_not_found": 404,
    "queue_full": 429,
}


def request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def respond_legacy(result: RunResult) -> RunResult | JSONResponse:
    for error in result.errors:
        code = _CLIENT_STATUS.get(error)
        if code is not None:
            return JSONResponse(status_code=code, content=result.model_dump(mode="json"))
    return result


def create_run(
    req: RunRequest,
    tenant: TenantContext,
    *,
    idempotency_key: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """返回 (status_code, body)。"""
    stamped = req.model_copy(
        update={
            "ticket": req.ticket.model_copy(update={"tenant_id": tenant.tenant_id}),
        }
    )
    body_for_hash = stamped.model_dump(mode="json")
    if idempotency_key:
        hit = lookup_idempotency(tenant.tenant_id, idempotency_key)
        if hit is not None:
            status_code, stored = hit
            if stored["request_hash"] != request_hash(body_for_hash):
                return 409, {
                    "errors": ["idempotency_conflict"],
                    "error": {
                        "code": "idempotency_conflict",
                        "message": "Idempotency-Key reused with a different body",
                    },
                }
            return status_code, stored["body"]

    result = submit_triage(stamped)
    status_code = 202 if result.status == "running" else 200
    for error in result.errors:
        mapped = _CLIENT_STATUS.get(error)
        if mapped is not None:
            status_code = mapped
            break
    payload = result.model_dump(mode="json")
    if idempotency_key and status_code in {200, 202}:
        save_idempotency(
            tenant.tenant_id,
            idempotency_key,
            request_hash(body_for_hash),
            status_code,
            payload,
        )
    return status_code, payload


def list_closed_tickets(
    principal: ReviewPrincipal,
    *,
    q: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
    paginate: bool = False,
) -> dict[str, Any] | list[dict]:
    tenant_id = principal.tenant_id or DEFAULT_TENANT_ID
    fetch_limit = (limit + 1) if (paginate and limit is not None) else limit
    rows = gateway.list_tickets(
        ["resolved", "escalated"],
        tenant_id=tenant_id,
        q=q,
        limit=fetch_limit,
        cursor=cursor,
    )
    record_audit(
        actor=principal.subject,
        action="review.list_closed",
        detail={"count": len(rows), "tenant_id": tenant_id},
    )
    if not paginate:
        return rows
    next_cursor = None
    page_limit = limit or 50
    if len(rows) > page_limit:
        rows = rows[:page_limit]
        last = rows[-1]
        next_cursor = f"{last.get('updated_at')}|{last.get('ticket_id')}"
    return {"items": rows, "next_cursor": next_cursor}


def list_pending_runs(
    principal: ReviewPrincipal,
    *,
    q: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
    paginate: bool = False,
) -> dict[str, Any] | list[dict]:
    tenant_id = principal.tenant_id or DEFAULT_TENANT_ID
    fetch_limit = (limit + 1) if (paginate and limit is not None) else limit
    rows = list_pending(
        tenant_id=tenant_id, q=q, limit=fetch_limit, cursor=cursor
    )
    record_audit(
        actor=principal.subject,
        action="review.read_drafts",
        detail={
            "count": len(rows),
            "tenant_id": tenant_id,
            "ticket_ids": [str(item.get("ticket_id") or "") for item in rows[:50]],
        },
    )
    if not paginate:
        return rows
    next_cursor = None
    page_limit = limit or 50
    if len(rows) > page_limit:
        rows = rows[:page_limit]
        last = rows[-1]
        next_cursor = f"{last.get('updated_at')}|{last.get('ticket_id')}"
    return {"items": rows, "next_cursor": next_cursor}


def human_decide(
    decision: HumanDecision,
    principal: ReviewPrincipal,
) -> RunResult:
    bound = decision.model_copy(update={"reviewer": principal.subject})
    result = resume_with_human(bound, tenant_id=principal.tenant_id or DEFAULT_TENANT_ID)
    action = "review.approve" if decision.approved else "review.reject"
    record_audit(
        actor=principal.subject,
        action=action,
        ticket_id=decision.ticket_id,
        trace_id=result.trace_id or "",
        detail={"status": result.status, "tenant_id": principal.tenant_id},
    )
    return result


def reopen(
    ticket_id: str,
    body: ReopenRequest,
    principal: ReviewPrincipal,
) -> RunResult:
    result = reopen_ticket(
        ticket_id,
        principal.subject,
        tenant_id=principal.tenant_id or DEFAULT_TENANT_ID,
    )
    record_audit(
        actor=principal.subject,
        action="review.reopen",
        ticket_id=ticket_id,
        trace_id=result.trace_id or "",
        detail={"ignored_body_reviewer": body.reviewer, "tenant_id": principal.tenant_id},
    )
    return result


def read_run_result(trace_id: str) -> RunResult | None:
    return get_run(trace_id)


def read_trace_for(principal: ReviewPrincipal, trace_id: str) -> dict:
    record_audit(
        actor=principal.subject,
        action="review.read_trace",
        trace_id=trace_id,
        detail={"tenant_id": principal.tenant_id},
    )
    return read_trace(trace_id)
