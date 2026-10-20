"""MCP 工具处理函数（与 Agent 共用业务实现）。"""

from __future__ import annotations

import logging
from typing import Any

from ticket_triage.tools.kb import search_knowledge_base
from ticket_triage.tools.tickets import get_ticket_history, update_ticket_status

logger = logging.getLogger("ticket_triage.mcp")

ALLOWED_STATUSES = frozenset({"open", "resolved", "escalated", "pending"})


def handle_search_knowledge_base(query: str, limit: int = 3) -> list[dict[str, str]]:
    """只读：搜索知识库。"""
    return search_knowledge_base(query=query, limit=limit)


def handle_get_ticket_history(ticket_id: str) -> list[dict[str, Any]]:
    """只读：查询工单历史。"""
    return get_ticket_history(ticket_id)


def handle_update_ticket_status(
    ticket_id: str, status: str, note: str | None = None
) -> dict[str, Any]:
    """写操作：更新状态（白名单 + 审计日志）。"""
    if status not in ALLOWED_STATUSES:
        logger.warning(
            "拒绝非法 status: ticket_id=%s status=%s allowed=%s",
            ticket_id,
            status,
            sorted(ALLOWED_STATUSES),
        )
        return {"error": "status_not_allowed", "allowed": sorted(ALLOWED_STATUSES)}
    result = update_ticket_status(ticket_id, status, note)
    logger.info(
        "写操作 update_ticket_status: ticket_id=%s status=%s note=%s",
        ticket_id,
        status,
        (note or "")[:120],
    )
    return result
