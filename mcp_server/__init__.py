"""工单工具 MCP Server 包。启动：python -m mcp_server"""

__all__ = ["list_tools"]


def list_tools():
    from mcp_server.server import list_tools as _list

    return _list()
