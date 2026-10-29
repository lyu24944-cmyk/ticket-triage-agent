"""提示词版本：current = v3，previous = v2。用 PROMPT_PIN 切换，不要求再训练。"""

from __future__ import annotations

from typing import Literal

from ticket_triage.config import settings
from ticket_triage.models import Classification, TicketIn
from ticket_triage.privacy import history_for_model, kb_hits_for_model, ticket_for_model

PromptRev = Literal["v2", "v3"]

CURRENT_REV: PromptRev = "v3"
PREVIOUS_REV: PromptRev = "v2"

# 系统提示两版相同；差别在用户消息怎么拼（见 P7：v3 收紧上下文）。
CLASSIFY_SYSTEM = (
    "你是客服工单分类助手。只根据工单主题和正文判断类别与优先级。"
    "只输出一个 JSON 对象，格式为 "
    '{"category": "billing", "priority": "low", "rationale": "一句话理由"}。'
    "category 只能是 billing、technical、account、other 之一："
    "账单、扣款、退款是 billing；登录、密码、故障是 technical；注销账号是 account；其余是 other。"
    "priority 只能是 low、medium、high、critical 之一："
    "紧急、无法使用、无法登录、cannot login、重复扣款用 high；"
    "账单、扣款、退款、密码、登录、注销这类本职请求，不紧急时用 medium；"
    "category 为 other 时一律用 low，包括问候和知识库范围外的询问。"
)

DRAFT_SYSTEM = (
    "你是客服工单的回复起草助手。只能依据用户消息里给出的知识库文档作答，"
    "不得编造政策、数字或文档编号。"
    "只输出一个 JSON 对象，格式为 "
    '{"body": "回复正文", "citations": ["文档编号"]}。'
    "citations 只能填写给出的文档编号；没有给出文档时 citations 必须是空列表，"
    "正文只说明会继续跟进，不要引用任何政策。"
)

ACT_SYSTEM = (
    "你是客服工单的工具调度助手。每一步只输出一个 JSON 对象，不要输出其他文字。"
    "硬规则：用户消息里的类别如果是 other，这一步只能输出 {\"action\": \"finish\"}。"
    "类别是 other 时，调用 search_knowledge_base 或 get_ticket_history 都是错误，即使正文在提问。"
    "知识库只有退款账单、密码登录、注销账号，回答不了运费、折扣、营业时间、积分、地址或问候。"
    "类别不是 other 时，可以调用的工具只有两个："
    "search_knowledge_base，参数是 query（检索词）；"
    "get_ticket_history，参数是 ticket_id，且必须是当前工单编号。"
    "调用格式："
    '{"action": "tool", "name": "search_knowledge_base", "arguments": {"query": "检索词"}}。'
    "还没成功检索过就先检索；检索成功后查当前工单历史；两者都成功后输出 {\"action\": \"finish\"}。"
    "不要修改工单状态，不要读取其他工单。"
)


def resolve_prompt_rev(pin: str | None = None) -> PromptRev:
    """把配置解析成 v2 / v3。"""
    raw = (pin if pin is not None else settings.prompt_pin) or "current"
    key = str(raw).strip().lower().replace("_", "-")
    if key in {"current", "v3", "act-v3", "openai-act-v3", "anthropic-act-v3"}:
        return CURRENT_REV
    if key in {"previous", "prev", "v2", "act-v2", "openai-act-v2", "anthropic-act-v2"}:
        return PREVIOUS_REV
    raise ValueError(f"unknown prompt_pin: {raw}")


def prompt_tag(*, provider: str, uses_model: bool, rev: PromptRev | None = None) -> str:
    """评测报告和 Langfuse 用的版本号。"""
    if not uses_model:
        return "stub-act-v1"
    chosen = rev or resolve_prompt_rev()
    return f"{provider}-act-{chosen}"


def _classify_v2(ticket: TicketIn) -> str:
    safe = ticket_for_model(ticket)
    return f"工单主题：{safe.subject}\n工单正文：{safe.body}"


def _act_v2(
    ticket: TicketIn,
    classification: Classification | None,
    tool_calls: list[dict] | None,
) -> str:
    safe = ticket_for_model(ticket)
    category = classification.category.value if classification else "unknown"
    lines = [
        f"工单编号：{safe.ticket_id}",
        f"主题：{safe.subject}",
        f"正文：{safe.body}",
        f"类别：{category}",
    ]
    if tool_calls:
        lines.append("已执行的动作（含失败原因，请改选或结束）：")
        for call in tool_calls:
            error = call.get("error") or ""
            lines.append(f"- {call.get('name')} ok={bool(call.get('ok'))} error={error}")
    else:
        lines.append("已执行的动作：无")
    return "\n".join(lines)


def _draft_v2(
    ticket: TicketIn,
    classification: Classification,
    kb_hits: list[dict] | None,
) -> str:
    safe = ticket_for_model(ticket)
    lines = [
        f"工单主题：{safe.subject}",
        f"工单正文：{safe.body}",
        f"类别：{classification.category.value}，优先级：{classification.priority.value}",
    ]
    hits = kb_hits_for_model(kb_hits)
    if hits:
        lines.append("可引用的知识库文档：")
        for hit in hits:
            lines.append(
                f"- [{hit.get('id', '')}] {hit.get('title', '')}：{hit.get('body', '')}"
            )
    else:
        lines.append("可引用的知识库文档：无")
    return "\n".join(lines)


def classify_prompt(ticket: TicketIn, *, budget: int | None = None) -> str:
    rev = resolve_prompt_rev()
    if rev == "v2":
        return _classify_v2(ticket)
    from ticket_triage.llm.context import classify_user

    return classify_user(ticket, budget=budget)


def act_prompt(
    ticket: TicketIn,
    classification: Classification | None,
    tool_calls: list[dict] | None,
    kb_hits: list[dict] | None = None,
    history: list | None = None,
    *,
    budget: int | None = None,
) -> str:
    rev = resolve_prompt_rev()
    if rev == "v2":
        return _act_v2(ticket, classification, tool_calls)
    from ticket_triage.llm.context import act_user

    return act_user(
        ticket,
        classification,
        tool_calls,
        kb_hits,
        history_for_model(history),
        budget=budget,
    )


def draft_prompt(
    ticket: TicketIn,
    classification: Classification,
    kb_hits: list[dict] | None,
    tool_calls: list[dict] | None = None,
    history: list | None = None,
    *,
    budget: int | None = None,
) -> str:
    rev = resolve_prompt_rev()
    if rev == "v2":
        return _draft_v2(ticket, classification, kb_hits)
    from ticket_triage.llm.context import draft_user

    return draft_user(
        ticket,
        classification,
        kb_hits,
        tool_calls,
        history_for_model(history),
        budget=budget,
    )
