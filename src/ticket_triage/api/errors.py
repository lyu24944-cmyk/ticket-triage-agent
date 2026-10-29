"""正式错误码表（/v1 信封）。旧路径仍用 {"errors": [...]}。"""

from __future__ import annotations

from typing import Any

# code → (http_status, default_message)
ERROR_CATALOG: dict[str, tuple[int, str]] = {
    "service_auth_not_configured": (401, "Service API key is not configured"),
    "service_auth_failed": (401, "Invalid service API key"),
    "review_auth_not_configured": (401, "Review mint key is not configured"),
    "review_auth_failed": (401, "Invalid review mint key"),
    "review_jwt_not_configured": (401, "Review JWT is not configured"),
    "review_mint_disabled": (401, "Review mint is disabled; use an IdP OIDC token"),
    "review_oidc_not_configured": (401, "Review OIDC is not configured"),
    "review_oidc_jwks": (401, "Review OIDC JWKS could not be loaded"),
    "review_token_missing": (401, "Bearer review token required"),
    "review_token_invalid": (401, "Review token is invalid"),
    "review_token_expired": (401, "Review token expired"),
    "webhook_signature_invalid": (401, "Webhook signature verification failed"),
    "webhook_secret_not_configured": (401, "Webhook signing secret is not configured"),
    "payload_too_large": (413, "Request body too large"),
    "validation_error": (422, "Request validation failed"),
    "ticket_not_found": (404, "Ticket not found"),
    "run_not_found": (404, "Run not found"),
    "no_pending_run": (404, "No pending run for ticket"),
    "already_closed": (409, "Ticket is already closed"),
    "already_pending": (409, "Ticket is already awaiting review"),
    "not_closed": (409, "Ticket is not in a closed state"),
    "idempotency_conflict": (409, "Idempotency-Key reused with a different body"),
    "queue_full": (429, "Tenant run queue is full"),
    "internal_error": (500, "Internal server error"),
}


def v1_error(
    code: str,
    *,
    trace_id: str | None = None,
    message: str | None = None,
    status_code: int | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    catalog = ERROR_CATALOG.get(code, (400, code))
    status = status_code if status_code is not None else catalog[0]
    body: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message or catalog[1],
        },
        "trace_id": trace_id,
    }
    if extra:
        body["error"]["details"] = extra
    return status, body
