"""结案 / 升级后的出站回调。失败只打日志，不回滚工单状态。"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from typing import Any

from ticket_triage.config import settings
from ticket_triage.tenants import context_for

logger = logging.getLogger("ticket_triage.callback")


def notify_closure(
    *,
    tenant_id: str,
    ticket_id: str,
    status: str,
    trace_id: str | None = None,
) -> None:
    ctx = context_for(tenant_id)
    url = ctx.callback_url or settings.closure_callback_url
    if not url:
        return
    payload = {
        "tenant_id": tenant_id,
        "ticket_id": ticket_id,
        "status": status,
        "trace_id": trace_id,
        "event": "ticket.closed",
    }
    thread = threading.Thread(
        target=_post,
        args=(url, payload),
        name=f"closure-cb-{ticket_id}",
        daemon=True,
    )
    thread.start()


def _post(url: str, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "ticket-triage-agent/p3"},
        method="POST",
    )
    timeout = float(settings.closure_callback_timeout_seconds)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
        logger.info(
            "closure_callback_ok ticket_id=%s status=%s",
            payload.get("ticket_id"),
            payload.get("status"),
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning(
            "closure_callback_failed ticket_id=%s err=%s",
            payload.get("ticket_id"),
            exc,
        )
