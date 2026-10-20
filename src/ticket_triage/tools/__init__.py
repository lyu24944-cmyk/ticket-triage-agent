"""进程内工具（P2 将以 MCP 暴露同等能力）。"""

from ticket_triage.tools.kb import search_knowledge_base
from ticket_triage.tools.tickets import get_ticket_history, update_ticket_status

__all__ = ["search_knowledge_base", "get_ticket_history", "update_ticket_status"]
