"""MCP 客户端网关：通过 stdio 调用 mcp_server 工具，带超时。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from ticket_triage.config import settings

logger = logging.getLogger("ticket_triage.mcp_gateway")

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _server_params():
    from mcp import StdioServerParameters

    if settings.mcp_command:
        parts = settings.mcp_command.split()
        command, args = parts[0], parts[1:]
    else:
        command = sys.executable
        args = ["-m", "mcp_server"]

    env = os.environ.copy()
    sep = os.pathsep
    env["PYTHONPATH"] = sep.join(
        [
            str(PROJECT_ROOT / "src"),
            str(PROJECT_ROOT),
            env.get("PYTHONPATH", ""),
        ]
    ).strip(sep)
    if settings.ticket_db_path:
        env["TICKET_DB_PATH"] = settings.ticket_db_path

    return StdioServerParameters(command=command, args=args, env=env, cwd=str(PROJECT_ROOT))


def _parse_tool_result(result: Any) -> Any:
    if getattr(result, "is_error", False) or getattr(result, "isError", False):
        raise RuntimeError(f"mcp_tool_error:{result}")
    structured = getattr(result, "structured_content", None)
    if structured is None:
        structured = getattr(result, "structuredContent", None)
    if structured is not None:
        if isinstance(structured, dict) and set(structured.keys()) == {"result"}:
            return structured["result"]
        return structured
    content = getattr(result, "content", None) or []
    if not content:
        return None
    text = getattr(content[0], "text", None)
    if text is None:
        return content
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


async def _call_tool_async(name: str, arguments: dict[str, Any]) -> Any:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    timeout = settings.mcp_timeout_seconds
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await asyncio.wait_for(
                session.call_tool(name, arguments=arguments),
                timeout=timeout,
            )
            return _parse_tool_result(result)


def call_mcp_tool(name: str, arguments: dict[str, Any] | None = None) -> Any:
    """同步调用 MCP 工具；超时抛出 TimeoutError。"""
    arguments = arguments or {}
    logger.info("MCP call_tool name=%s args_keys=%s", name, list(arguments))
    try:
        return asyncio.run(_call_tool_async(name, arguments))
    except TimeoutError:
        logger.error("MCP 工具超时 name=%s timeout=%ss", name, settings.mcp_timeout_seconds)
        raise
    except Exception:
        logger.exception("MCP 工具失败 name=%s", name)
        raise


async def list_mcp_tools_async() -> list[str]:
    """列出远端工具名（冒烟/验收用）。"""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            return [t.name for t in tools.tools]


def search_knowledge_base_mcp(query: str, limit: int = 3) -> list[dict[str, str]]:
    raw = call_mcp_tool("search_knowledge_base", {"query": query, "limit": limit})
    if isinstance(raw, dict) and "articles" in raw:
        return list(raw["articles"])
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and "id" in raw:
        return [raw]
    return []


def get_ticket_history_mcp(ticket_id: str) -> list[dict[str, Any]]:
    raw = call_mcp_tool("get_ticket_history", {"ticket_id": ticket_id})
    if isinstance(raw, dict) and "history" in raw:
        return list(raw["history"])
    if isinstance(raw, list):
        return raw
    return []


def update_ticket_status_mcp(
    ticket_id: str,
    status: str,
    note: str | None = None,
    reviewer: str | None = None,
) -> dict[str, Any]:
    args: dict[str, Any] = {"ticket_id": ticket_id, "status": status}
    if note is not None:
        args["note"] = note
    if reviewer:
        args["reviewer"] = reviewer
    raw = call_mcp_tool("update_ticket_status", args)
    return raw if isinstance(raw, dict) else {"result": raw}
