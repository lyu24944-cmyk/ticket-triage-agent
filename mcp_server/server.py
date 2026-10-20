"""
P2 脚手架：用 MCP 封装 ticket_triage 工具。

安装可选依赖：pip install -e ".[mcp]"
再接入 MCP Host（Cursor / Claude Desktop）。

本文件刻意保持薄封装，以便 P1 在未安装 MCP 时也能交付。
"""

from __future__ import annotations

from ticket_triage.tools.kb import search_knowledge_base
from ticket_triage.tools.tickets import get_ticket_history, update_ticket_status

ALLOWED_STATUSES = frozenset({"open", "resolved", "escalated", "pending"})


def tool_search_knowledge_base(query: str, limit: int = 3) -> list[dict[str, str]]:
    """只读：搜索知识库。"""
    return search_knowledge_base(query=query, limit=limit)


def tool_get_ticket_history(ticket_id: str) -> list[dict]:
    """只读：查询工单历史。"""
    return get_ticket_history(ticket_id)


def tool_update_ticket_status(ticket_id: str, status: str, note: str | None = None) -> dict:
    """写操作：更新状态（白名单校验）。"""
    if status not in ALLOWED_STATUSES:
        return {"error": "status_not_allowed", "allowed": sorted(ALLOWED_STATUSES)}
    return update_ticket_status(ticket_id, status, note)


TOOL_SPECS = [
    {
        "name": "search_knowledge_base",
        "description": "搜索客服知识库文章。只读。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 3},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_ticket_history",
        "description": "返回工单状态历史。只读。",
        "input_schema": {
            "type": "object",
            "properties": {"ticket_id": {"type": "string"}},
            "required": ["ticket_id"],
        },
    },
    {
        "name": "update_ticket_status",
        "description": "更新工单状态。写操作；status 白名单限制。",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string"},
                "status": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["ticket_id", "status"],
        },
    },
]


def list_tools() -> list[dict]:
    """列出工具目录（冒烟用）。"""
    return TOOL_SPECS


if __name__ == "__main__":
    # 打印工具目录做冒烟；完整 MCP stdio 循环放到第 4–5 周
    import json

    print(json.dumps({"tools": list_tools()}, indent=2, ensure_ascii=False))
