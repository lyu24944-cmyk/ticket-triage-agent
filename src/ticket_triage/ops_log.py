"""值班可搜的结构化日志。字段固定；可 JSON 进集中日志；告警码触发 Webhook/邮件。

固定键：event、trace_id、ticket_id、node、queue_depth、stub_fallback、tool_error、
model_failed、alert_code。
CI 仍是 stub pytest + 黄金评测，不把这些日志当成合并门槛。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("ticket_triage.ops")

# 模型退回 stub 的错误码前缀
STUB_FALLBACK_PREFIXES = (
    "classify_model_failed",
    "act_model_failed",
    "draft_model_failed",
)
# 工具失败码前缀（节点名仍是 act）
TOOL_ERROR_PREFIXES = ("enrich_failed",)

_CODE_RE = re.compile(r"^([a-z0-9_]+)")
_JSON_HANDLER_NAME = "ticket_triage_ops_json"


def error_code(item: Any) -> str:
    """从 errors 条目取出可搜的码，例如 enrich_failed。"""
    text = str(item or "").strip()
    if not text:
        return ""
    match = _CODE_RE.match(text)
    return match.group(1) if match else text.split(":", 1)[0]


def stub_fallback_from_errors(errors: list[Any] | None) -> bool:
    return any(
        error_code(item).startswith(STUB_FALLBACK_PREFIXES)
        or str(item).startswith(STUB_FALLBACK_PREFIXES)
        for item in (errors or [])
    )


def tool_error_from_errors(errors: list[Any] | None) -> str:
    for item in errors or []:
        code = error_code(item)
        if code.startswith(TOOL_ERROR_PREFIXES) or str(item).startswith(TOOL_ERROR_PREFIXES):
            return code or "enrich_failed"
    return ""


def model_failed_from_errors(errors: list[Any] | None) -> str:
    """取出 classify/act/draft_model_failed 码，便于集中日志与告警。"""
    for item in errors or []:
        code = error_code(item)
        if code.startswith(STUB_FALLBACK_PREFIXES) or str(item).startswith(STUB_FALLBACK_PREFIXES):
            return code or str(item).split(":", 1)[0]
    return ""


def resolve_alert_code(
    event: str,
    *,
    model_failed: str = "",
    tool_error: str = "",
) -> str:
    """映射到可告警码；无则空串。"""
    if event == "queue_full":
        return "queue_full"
    if event == "closure_callback_dead" or tool_error == "closure_callback_failed":
        return "closure_callback_failed"
    if model_failed.startswith(STUB_FALLBACK_PREFIXES):
        return model_failed
    if tool_error.startswith(TOOL_ERROR_PREFIXES):
        return "enrich_failed"
    return ""


def format_event(
    event: str,
    *,
    trace_id: str = "",
    ticket_id: str = "",
    node: str = "",
    queue_depth: int | None = None,
    stub_fallback: bool | None = None,
    tool_error: str = "",
    model_failed: str = "",
    alert_code: str = "",
    **extra: Any,
) -> str:
    """拼成一行 key=value，缺省键也写出，方便 grep。"""
    depth = "" if queue_depth is None else str(int(queue_depth))
    if stub_fallback is None:
        stub = ""
    else:
        stub = "1" if stub_fallback else "0"
    parts = [
        f"event={event}",
        f"trace_id={trace_id or '-'}",
        f"ticket_id={ticket_id or '-'}",
        f"node={node or '-'}",
        f"queue_depth={depth if depth != '' else '-'}",
        f"stub_fallback={stub if stub != '' else '-'}",
        f"tool_error={tool_error or '-'}",
        f"model_failed={model_failed or '-'}",
        f"alert_code={alert_code or '-'}",
    ]
    for key, value in extra.items():
        if value is None or value == "":
            parts.append(f"{key}=-")
        else:
            text = str(value).replace(" ", "_")
            parts.append(f"{key}={text}")
    return " ".join(parts)


def event_fields(
    event: str,
    *,
    trace_id: str = "",
    ticket_id: str = "",
    node: str = "",
    queue_depth: int | None = None,
    stub_fallback: bool | None = None,
    tool_error: str = "",
    model_failed: str = "",
    alert_code: str = "",
    **extra: Any,
) -> dict[str, Any]:
    """集中日志用的字典（JSON 一行）。"""
    if stub_fallback is None:
        stub: bool | None = None
    else:
        stub = bool(stub_fallback)
    payload: dict[str, Any] = {
        "event": event,
        "trace_id": trace_id or None,
        "ticket_id": ticket_id or None,
        "node": node or None,
        "queue_depth": queue_depth,
        "stub_fallback": stub,
        "tool_error": tool_error or None,
        "model_failed": model_failed or None,
        "alert_code": alert_code or None,
    }
    for key, value in extra.items():
        if value == "":
            payload[key] = None
        else:
            payload[key] = value
    return payload


def log_event(
    event: str,
    *,
    level: int = logging.INFO,
    trace_id: str = "",
    ticket_id: str = "",
    node: str = "",
    queue_depth: int | None = None,
    stub_fallback: bool | None = None,
    tool_error: str = "",
    model_failed: str = "",
    errors: list[Any] | None = None,
    **extra: Any,
) -> str:
    if not model_failed and errors:
        model_failed = model_failed_from_errors(errors)
    if not tool_error and errors:
        tool_error = tool_error_from_errors(errors)
    if stub_fallback is None and errors is not None:
        stub_fallback = stub_fallback_from_errors(errors)
    alert_code = resolve_alert_code(
        event,
        model_failed=model_failed,
        tool_error=tool_error,
    )
    fields = event_fields(
        event,
        trace_id=trace_id,
        ticket_id=ticket_id,
        node=node,
        queue_depth=queue_depth,
        stub_fallback=stub_fallback,
        tool_error=tool_error,
        model_failed=model_failed,
        alert_code=alert_code,
        **extra,
    )
    from ticket_triage.config import settings

    if settings.ops_log_json:
        line = json.dumps(fields, ensure_ascii=False, default=str)
    else:
        line = format_event(
            event,
            trace_id=trace_id,
            ticket_id=ticket_id,
            node=node,
            queue_depth=queue_depth,
            stub_fallback=stub_fallback,
            tool_error=tool_error,
            model_failed=model_failed,
            alert_code=alert_code,
            **extra,
        )
    logger.log(level, line)
    if alert_code:
        from ticket_triage.alerts import maybe_alert

        maybe_alert(
            alert_code,
            event=event,
            trace_id=trace_id,
            ticket_id=ticket_id,
            node=node,
            queue_depth=queue_depth,
            model_failed=model_failed or None,
            tool_error=tool_error or None,
        )
    return line


def configure_ops_logging() -> None:
    """按需把 ticket_triage.ops 打成 JSON 到 stdout，便于采集进集中日志。"""
    from ticket_triage.config import settings

    ops = logging.getLogger("ticket_triage.ops")
    # 去掉旧的同名 handler，避免重复
    ops.handlers = [h for h in ops.handlers if getattr(h, "name", None) != _JSON_HANDLER_NAME]
    if not settings.ops_log_json:
        return
    handler = logging.StreamHandler()
    handler.set_name(_JSON_HANDLER_NAME)
    handler.setFormatter(logging.Formatter("%(message)s"))
    ops.addHandler(handler)
    ops.setLevel(logging.INFO)
    ops.propagate = True
