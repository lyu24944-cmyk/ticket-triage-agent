"""模拟邮件网关 Webhook：HMAC-SHA256 验签 + message_id 幂等。"""

from __future__ import annotations

from typing import Any

from ticket_triage.models import TicketIn


def map_email_payload(payload: dict[str, Any]) -> TicketIn:
    message_id = str(payload.get("message_id") or payload.get("id") or "").strip()
    subject = str(payload.get("subject") or "").strip()
    body = str(payload.get("body") or payload.get("text") or "").strip()
    email = payload.get("from") or payload.get("customer_email")
    if email is not None:
        email = str(email).strip() or None
    if not message_id:
        raise ValueError("missing_message_id")
    if not subject:
        raise ValueError("missing_subject")
    if not body:
        raise ValueError("missing_body")
    # 邮件 message-id 可能含 <>@；压成安全 ticket_id
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in message_id)
    safe = safe.strip("-._")[:60] or "mail"
    return TicketIn(
        ticket_id=f"mail-{safe}",
        subject=subject[:200],
        body=body[:4000],
        customer_email=email,
        metadata={"source": "email_gateway", "message_id": message_id},
    )


def delivery_id(headers: dict[str, str], payload: dict[str, Any]) -> str:
    for key in ("x-email-gateway-delivery-id", "X-Email-Gateway-Delivery-Id"):
        raw = headers.get(key) or headers.get(key.lower())
        if raw and str(raw).strip():
            return f"email:{str(raw).strip()}"
    mid = payload.get("message_id") or payload.get("id")
    if mid:
        return f"email:msg:{mid}"
    raise ValueError("missing_delivery_id")
