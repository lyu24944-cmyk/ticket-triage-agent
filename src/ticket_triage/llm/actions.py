"""act 节点的动作协议。模型只能在白名单里选工具，或结束。"""

from __future__ import annotations

from ticket_triage.models import Category, Classification, TicketIn

# 写状态不在此列。finalize 仍通过网关调用 update_ticket_status。
ALLOWED_ACT_TOOLS = ("search_knowledge_base", "get_ticket_history")
MAX_ACT_STEPS = 4

ACT_SYSTEM = (
    "你是客服工单的工具调度助手。每一步只输出一个 JSON 对象，不要输出其他文字。"
    "可以调用的工具只有两个："
    "search_knowledge_base，参数是 query（检索词）；"
    "get_ticket_history，参数是 ticket_id，且必须是当前工单编号。"
    "调用格式："
    '{"action": "tool", "name": "search_knowledge_base", "arguments": {"query": "检索词"}}。'
    "类别是 other，或已经拿到足够信息时，输出 {\"action\": \"finish\"}。"
    "不要修改工单状态，不要读取其他工单。"
)


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
) -> str:
    category = classification.category.value if classification else "unknown"
    lines = [
        f"工单编号：{ticket.ticket_id}",
        f"主题：{ticket.subject}",
        f"正文：{ticket.body}",
        f"类别：{category}",
    ]
    if tool_calls:
        lines.append("已执行的动作（含失败原因，请改选或结束）：")
        for call in tool_calls:
            error = call.get("error") or ""
            lines.append(
                f"- {call.get('name')} ok={bool(call.get('ok'))} error={error}"
            )
    else:
        lines.append("已执行的动作：无")
    return "\n".join(lines)
