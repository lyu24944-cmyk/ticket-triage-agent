"""内存工单库 — P2 可替换为 Postgres / MCP。"""

from __future__ import annotations

from typing import Any

_STORE: dict[str, dict[str, Any]] = {}


def get_ticket_history(ticket_id: str) -> list[dict[str, Any]]:
    """返回工单状态历史。"""
    record = _STORE.get(ticket_id)
    if not record:
        return []
    return list(record.get("history", []))


def update_ticket_status(ticket_id: str, status: str, note: str | None = None) -> dict[str, Any]:
    """更新工单状态并追加历史。"""
    record = _STORE.setdefault(ticket_id, {"status": "open", "history": []})
    record["status"] = status
    record["history"].append({"status": status, "note": note})
    return {"ticket_id": ticket_id, "status": status}


def seed_ticket(ticket_id: str, subject: str, body: str) -> None:
    """初始化一条工单记录。"""
    _STORE[ticket_id] = {
        "status": "open",
        "history": [{"status": "open", "note": f"已入库: {subject}"}],
        "subject": subject,
        "body": body,
    }
