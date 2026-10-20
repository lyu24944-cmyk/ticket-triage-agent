"""基于 MCP Python SDK v2（MCPServer）的 stdio 工具服务。"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.mcpserver import MCPServer

from mcp_server.handlers import (
    handle_get_ticket_history,
    handle_search_knowledge_base,
    handle_update_ticket_status,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ticket_triage.mcp")

mcp = MCPServer(
    "ticket-triage-tools",
    instructions="客服工单工具面：知识库检索、工单历史、状态更新。无 shell/SQL。",
)


@mcp.tool()
def search_knowledge_base(query: str, limit: int = 3) -> dict[str, Any]:
    """搜索客服知识库文章。只读。返回 {articles: [...]}。"""
    # MCP SDK 对顶层 list 结构化结果不完整，故统一用对象包裹
    return {"articles": handle_search_knowledge_base(query=query, limit=limit)}


@mcp.tool()
def get_ticket_history(ticket_id: str) -> dict[str, Any]:
    """返回工单状态历史。只读。返回 {history: [...]}。"""
    return {"history": handle_get_ticket_history(ticket_id)}


@mcp.tool()
def update_ticket_status(ticket_id: str, status: str, note: str | None = None) -> dict[str, Any]:
    """更新工单状态。写操作；status 仅允许 open/resolved/escalated/pending。"""
    return handle_update_ticket_status(ticket_id=ticket_id, status=status, note=note)


def run_stdio() -> None:
    """以 stdio 传输启动 MCP Server。"""
    logger.info("启动 MCP Server: ticket-triage-tools (stdio)")
    mcp.run(transport="stdio")
