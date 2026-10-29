"""act 节点的动作协议。模型只能在白名单里选工具，或结束。"""

from __future__ import annotations

from ticket_triage.llm.prompts import ACT_SYSTEM, act_prompt
from ticket_triage.models import Category, Classification, TicketIn

# 写状态不在此列。finalize 仍通过网关调用 update_ticket_status。
ALLOWED_ACT_TOOLS = ("search_knowledge_base", "get_ticket_history")
MAX_ACT_STEPS = 4


def parse_action(payload: dict) -> dict:
    """把模型 JSON 收成 finish 或 tool。非法工具名留给执行层拒绝，不在这里抛错。"""
    if not isinstance(payload, dict):
        raise ValueError("model_output_not_object")  # noqa: TRY004 — 与其他模型输出错误一样用 ValueError
    action = payload.get("action")
    if action == "finish":
        return {"action": "finish"}
    if action != "tool":
        raise ValueError("model_output_bad_action")
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("model_output_bad_tool_name")
    arguments = payload.get("arguments", {})
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ValueError("model_output_bad_arguments")  # noqa: TRY004
    return {"action": "tool", "name": name.strip(), "arguments": dict(arguments)}


def stub_next_action(
    ticket: TicketIn,
    classification: Classification | None,
    tool_calls: list[dict],
) -> dict:
    """确定性策略：other 不检索；其余先搜知识库，再查当前工单历史。"""
    category = classification.category if classification else Category.other
    succeeded = {
        call.get("name")
        for call in tool_calls
        if call.get("ok") and call.get("name") != "finish"
    }
    if category == Category.other:
        return {"action": "finish"}
    if "search_knowledge_base" not in succeeded:
        query = f"{ticket.subject} {ticket.body}".strip()
        return {
            "action": "tool",
            "name": "search_knowledge_base",
            "arguments": {"query": query},
        }
    if "get_ticket_history" not in succeeded:
        return {
            "action": "tool",
            "name": "get_ticket_history",
            "arguments": {"ticket_id": ticket.ticket_id},
        }
    return {"action": "finish"}


def act_user_prompt(
    ticket: TicketIn,
    classification: Classification | None,
    tool_calls: list[dict],
    kb_hits: list[dict] | None = None,
    history: list | None = None,
    *,
    budget: int | None = None,
) -> str:
    return act_prompt(
        ticket,
        classification,
        tool_calls,
        kb_hits,
        history,
        budget=budget,
    )
