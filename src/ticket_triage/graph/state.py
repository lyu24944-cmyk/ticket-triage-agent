"""工作流状态定义。"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from ticket_triage.models import Classification, DraftReply, TicketIn


def _merge_errors(left: list[str] | None, right: list[str] | None) -> list[str]:
    """合并错误列表（LangGraph reducer）。"""
    return (left or []) + (right or [])


class TriageState(TypedDict, total=False):
    """分流图状态。"""

    ticket: TicketIn
    classification: Classification | None
    kb_hits: list[dict[str, str]]
    history: list[dict[str, Any]]
    draft: DraftReply | None
    status: str
    awaiting_human: bool
    human_approved: bool | None
    human_edits: str | None
    errors: Annotated[list[str], _merge_errors]
    tool_retries: int
    auto_approve: bool
    trace_id: str
