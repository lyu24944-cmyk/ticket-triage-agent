"""兼容旧脚手架：列出工具规格；真正服务见 app.py / python -m mcp_server。"""

from __future__ import annotations

import json

from mcp_server.handlers import (
    ALLOWED_STATUSES,
    handle_get_ticket_history,
    handle_search_knowledge_base,
    handle_update_ticket_status,
)

# 供外部直接调用（非 stdio）
tool_search_knowledge_base = handle_search_knowledge_base
tool_get_ticket_history = handle_get_ticket_history
tool_update_ticket_status = handle_update_ticket_status

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
                "status": {"type": "string", "enum": sorted(ALLOWED_STATUSES)},
                "note": {"type": "string"},
                "reviewer": {"type": "string"},
            },
            "required": ["ticket_id", "status"],
        },
    },
]


def list_tools() -> list[dict]:
    return TOOL_SPECS


if __name__ == "__main__":
    print(json.dumps({"tools": list_tools()}, indent=2, ensure_ascii=False))
