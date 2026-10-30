"""值班告警：queue_full / *_model_failed / enrich_failed → Webhook（邮件/IM 网关）或 SMTP。

不上完整 APM。同一 alert_code 在冷却期内只发一次，避免打满队列时刷屏。
"""

from __future__ import annotations

import json
import logging
import smtplib
import threading
import time
from email.message import EmailMessage
from typing import Any
from urllib import error, request

from ticket_triage.config import settings

logger = logging.getLogger("ticket_triage.alerts")

ALERT_CODES = frozenset(
    {
        "queue_full",
        "classify_model_failed",
        "act_model_failed",
        "draft_model_failed",
        "enrich_failed",
        "closure_callback_failed",
    }
)

_lock = threading.Lock()
_last_sent: dict[str, float] = {}
# 测试可替换
_post_hook: Any = None


def reset_alert_state() -> None:
    """测试用：清空冷却与 hook。"""
    with _lock:
        _last_sent.clear()
    global _post_hook
    _post_hook = None


def set_post_hook(hook: Any) -> None:
    """测试用：拦截发送。"""
    global _post_hook
    _post_hook = hook


def _cooldown_ok(alert_code: str) -> bool:
    cool = max(0, int(settings.alert_cooldown_seconds))
    now = time.monotonic()
    with _lock:
        last = _last_sent.get(alert_code)
        if last is not None and now - last < cool:
            return False
        _last_sent[alert_code] = now
        return True


def build_payload(
    alert_code: str,
    *,
    event: str = "",
    trace_id: str = "",
    ticket_id: str = "",
    node: str = "",
    queue_depth: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "alert_code": alert_code,
        "event": event or alert_code,
        "trace_id": trace_id or None,
        "ticket_id": ticket_id or None,
        "node": node or None,
        "queue_depth": queue_depth,
        "app": settings.app_name,
        **{k: v for k, v in extra.items() if v not in (None, "")},
    }


def _post_webhook(url: str, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    timeout = float(settings.alert_webhook_timeout_seconds)
    with request.urlopen(req, timeout=timeout) as resp:
        resp.read()


def _send_email(payload: dict[str, Any]) -> None:
    to_addr = (settings.alert_email_to or "").strip()
    host = (settings.alert_smtp_host or "").strip()
    if not to_addr or not host:
        return
    msg = EmailMessage()
    msg["Subject"] = f"[{settings.app_name}] {payload.get('alert_code')}"
    msg["From"] = (settings.alert_email_from or "ticket-triage@localhost").strip()
    msg["To"] = to_addr
    msg.set_content(json.dumps(payload, ensure_ascii=False, indent=2))
    port = int(settings.alert_smtp_port)
    if settings.alert_smtp_use_tls:
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            smtp.starttls()
            user = settings.alert_smtp_user
            if user:
                smtp.login(user, settings.alert_smtp_password or "")
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            user = settings.alert_smtp_user
            if user:
                smtp.login(user, settings.alert_smtp_password or "")
            smtp.send_message(msg)


def maybe_alert(
    alert_code: str,
    *,
    event: str = "",
    trace_id: str = "",
    ticket_id: str = "",
    node: str = "",
    queue_depth: int | None = None,
    **extra: Any,
) -> bool:
    """若配置了通道且未在冷却中，发送告警。返回是否实际发送。"""
    code = (alert_code or "").strip()
    if code not in ALERT_CODES:
        return False
    if not settings.alert_webhook_url and not (
        settings.alert_email_to and settings.alert_smtp_host
    ):
        return False
    if not _cooldown_ok(code):
        logger.debug("alert_suppressed code=%s", code)
        return False
    payload = build_payload(
        code,
        event=event,
        trace_id=trace_id,
        ticket_id=ticket_id,
        node=node,
        queue_depth=queue_depth,
        **extra,
    )
    if _post_hook is not None:
        _post_hook(payload)
        return True
    try:
        if settings.alert_webhook_url:
            _post_webhook(settings.alert_webhook_url, payload)
        if settings.alert_email_to and settings.alert_smtp_host:
            _send_email(payload)
        logger.info(
            "alert_sent code=%s ticket_id=%s trace_id=%s",
            code,
            ticket_id or "-",
            trace_id or "-",
        )
        return True
    except error.URLError as exc:
        logger.warning("alert_webhook_failed code=%s err=%s", code, exc)
        return False
    except Exception:
        logger.exception("alert_failed code=%s", code)
        return False
