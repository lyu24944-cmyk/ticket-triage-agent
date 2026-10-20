"""确定性 stub LLM，骨架阶段无需 API Key。"""

from __future__ import annotations

from ticket_triage.config import settings
from ticket_triage.models import Category, Classification, DraftReply, Priority, TicketIn


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


def get_llm() -> StubLLM:
    """按配置返回 LLM；当前统一返回 stub，后续可按 provider 分支。"""
    _ = settings.llm_provider
    return StubLLM()
