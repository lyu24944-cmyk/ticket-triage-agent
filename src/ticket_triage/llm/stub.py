"""确定性 stub LLM，骨架阶段无需 API Key。"""

from __future__ import annotations

import logging

from ticket_triage.config import settings
from ticket_triage.llm.actions import stub_next_action
from ticket_triage.models import Category, Classification, DraftReply, Priority, TicketIn

logger = logging.getLogger("ticket_triage.llm")


class StubLLM:
    """基于关键词的启发式分类与起草（仅用于本地/CI）。"""

    def classify(self, ticket: TicketIn) -> Classification:
        text = f"{ticket.subject} {ticket.body}".lower()
        if any(w in text for w in ("refund", "charge", "invoice", "billing", "付款", "退款")):
            category = Category.billing
            priority = Priority.high if "urgent" in text or "紧急" in text else Priority.medium
        elif any(w in text for w in ("password", "login", "crash", "bug", "密码", "登录")):
            category = Category.technical
            priority = Priority.high if "cannot" in text or "无法" in text else Priority.medium
        elif any(w in text for w in ("delete account", "注销", "关闭账号")):
            category = Category.account
            priority = Priority.medium
        else:
            category = Category.other
            priority = Priority.low

        needs_escalation = priority in (Priority.high, Priority.critical) and category == Category.billing
        return Classification(
            priority=priority,
            category=category,
            rationale="stub 启发式分类",
            needs_escalation=needs_escalation,
        )

    def draft_reply(
        self,
        ticket: TicketIn,
        classification: Classification,
        kb_hits: list[dict[str, str]],
    ) -> DraftReply:
        citations = [h["id"] for h in kb_hits]
        if kb_hits:
            body = (
                f"感谢联系客服，关于「{ticket.subject}」。"
                f"根据文档（{', '.join(citations)}）：{kb_hits[0]['body']}"
                "如需更多帮助请回复本工单。"
            )
            confidence = 0.75
        else:
            body = (
                f"感谢联系客服，关于「{ticket.subject}」。"
                "我们正在处理您的请求，稍后回复。"
            )
            confidence = 0.4
        if classification.needs_escalation:
            body += " 【已标记账单升级】"
        return DraftReply(body=body, citations=citations, confidence=confidence)

    def next_action(
        self,
        ticket: TicketIn,
        classification: Classification | None,
        tool_calls: list[dict],
    ) -> dict:
        return stub_next_action(ticket, classification, tool_calls)


def get_llm():
    """llm_provider 为 anthropic / openai 且有 Key 时用真实模型分类和起草，否则用 stub。"""
    provider = (settings.llm_provider or "stub").lower()
    if provider == "anthropic" and settings.anthropic_api_key:
        from ticket_triage.llm.model import ModelLLM, anthropic_complete

        return ModelLLM(anthropic_complete())
    if provider == "openai" and settings.openai_api_key:
        from ticket_triage.llm.model import ModelLLM, openai_complete

        return ModelLLM(openai_complete())
    if provider != "stub":
        logger.warning("llm_provider=%s 缺少 API Key，改用 stub", provider)
    return StubLLM()
