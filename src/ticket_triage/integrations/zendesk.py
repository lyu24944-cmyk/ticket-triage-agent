"""模拟 Zendesk Webhook：HMAC-SHA256 验签 + Delivery-Id 幂等。"""

from __future__ import annotations

from typing import Any

from ticket_triage.models import TicketIn


def map_zendesk_payload(payload: dict[str, Any]) -> TicketIn:
    """把 Zendesk 风格 JSON 映射为 TicketIn。

    支持扁平字段或 ``ticket`` 嵌套。
    """
    ticket = payload.get("ticket") if isinstance(payload.get("ticket"), dict) else payload
    ticket_id = str(
        ticket.get("id")
        or ticket.get("ticket_id")
        or payload.get("id")
        or ""
    ).strip()
    subject = str(ticket.get("subject") or ticket.get("title") or "").strip()
    body = str(
        ticket.get("description")
        or ticket.get("body")
        or ticket.get("comment")
        or ""
    ).strip()
    email = ticket.get("requester_email") or ticket.get("customer_email")
    if email is not None:
        email = str(email).strip() or None
    if not ticket_id:
        raise ValueError("missing_ticket_id")
    if not subject:
        raise ValueError("missing_subject")
    if not body:
        raise ValueError("missing_body")
    return TicketIn(
        ticket_id=f"zd-{ticket_id}" if not str(ticket_id).startswith("zd-") else str(ticket_id),
        subject=subject[:200],
        body=body[:4000],
        customer_email=email,
        metadata={"source": "zendesk", "external_id": str(ticket_id)},
    )


def delivery_id(headers: dict[str, str], payload: dict[str, Any]) -> str:
    """幂等键：优先 Delivery-Id，否则 ticket id。"""
    for key in (
        "x-zendesk-delivery-id",
        "X-Zendesk-Delivery-Id",
        "x-delivery-id",
    ):
        raw = headers.get(key) or headers.get(key.lower())
        if raw and str(raw).strip():
            return f"zendesk:{str(raw).strip()}"
    ticket = payload.get("ticket") if isinstance(payload.get("ticket"), dict) else payload
    eid = ticket.get("id") or ticket.get("ticket_id") or payload.get("id")
    if eid:
        return f"zendesk:ticket:{eid}"
    raise ValueError("missing_delivery_id")
