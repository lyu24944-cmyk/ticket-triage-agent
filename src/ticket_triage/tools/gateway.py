"""工具网关：按 USE_MCP 在进程内实现与 MCP 之间切换。"""

from __future__ import annotations

from typing import Any

from ticket_triage.config import settings
from ticket_triage.tools import kb as kb_mod
from ticket_triage.tools import tickets as tickets_mod


def search_knowledge_base(query: str, limit: int = 3) -> list[dict[str, str]]:
    if settings.use_mcp:
        from ticket_triage.tools.mcp_gateway import search_knowledge_base_mcp

        return search_knowledge_base_mcp(query, limit=limit)
    if settings.rag_base_url:
        from ticket_triage.tools.rag_client import search_knowledge_base as search_via_rag

        return search_via_rag(query, limit=limit)
    return kb_mod.search_knowledge_base(query, limit=limit)


def get_ticket_history(ticket_id: str) -> list[dict[str, Any]]:
    if settings.use_mcp:
        from ticket_triage.tools.mcp_gateway import get_ticket_history_mcp

        return get_ticket_history_mcp(ticket_id)
    return tickets_mod.get_ticket_history(ticket_id)


def update_ticket_status(ticket_id: str, status: str, note: str | None = None) -> dict[str, Any]:
    if settings.use_mcp:
        from ticket_triage.tools.mcp_gateway import update_ticket_status_mcp

        return update_ticket_status_mcp(ticket_id, status, note)
    return tickets_mod.update_ticket_status(ticket_id, status, note)


def get_ticket(ticket_id: str) -> dict[str, Any] | None:
    """读取工单。与入库一样直接读共享 SQLite。"""
    return tickets_mod.get_ticket(ticket_id)


def list_tickets(statuses: list[str] | None = None) -> list[dict[str, Any]]:
    """列出工单。直接读共享 SQLite。"""
    return tickets_mod.list_tickets(statuses)


def seed_ticket(ticket_id: str, subject: str, body: str) -> None:
    # 入库始终写共享 SQLite，供 MCP 子进程读取
    tickets_mod.seed_ticket(ticket_id, subject, body)
