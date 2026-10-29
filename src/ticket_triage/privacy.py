"""客户正文：送模型前脱敏；SQLite 保留期限见 docs/DATA.md。"""

from __future__ import annotations

import re
from copy import deepcopy

from ticket_triage.models import TicketIn

# 邮箱、电话、卡号、常见密钥形态。替换后可重复调用，不会把占位符再吃掉。
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# 手机号或带分隔的固话；避免误伤短数字。
_PHONE = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[\s\-])?(?:1\d{10}|\d{2,4}[\s\-]\d{3,4}[\s\-]\d{4})(?!\d)"
)
_CARD = re.compile(r"(?<!\d)(?:\d[ -]*?){13,19}(?!\d)")
_SECRET = re.compile(
    r"(?i)\b(?:sk-[A-Za-z0-9_-]{8,}|Bearer\s+[A-Za-z0-9._\-+/=]{8,}|"
    r"(?:api[_-]?key|secret|token)\s*[:=]\s*\S+)"
)

_PLACEHOLDERS = {
    "email": "[EMAIL]",
    "phone": "[PHONE]",
    "card": "[CARD]",
    "secret": "[SECRET]",
}


def redact_text(text: str) -> str:
    """去掉不该进模型的敏感片段。SQLite 里仍可保留原文。"""
    if not text:
        return text
    out = _SECRET.sub(_PLACEHOLDERS["secret"], text)
    out = _EMAIL.sub(_PLACEHOLDERS["email"], out)
    out = _CARD.sub(_PLACEHOLDERS["card"], out)
    out = _PHONE.sub(_PLACEHOLDERS["phone"], out)
    return out


def ticket_for_model(ticket: TicketIn) -> TicketIn:
    """提示词只用脱敏后的主题和正文。customer_email / metadata / 审核人不进模型。"""
    data = ticket.model_dump(mode="python")
    data["subject"] = redact_text(str(data.get("subject") or ""))
    data["body"] = redact_text(str(data.get("body") or ""))
    data["customer_email"] = None
    data["metadata"] = {}
    return TicketIn.model_validate(data)


def history_for_model(history: list | None) -> list[dict]:
    """进提示词的历史只留 status。note、reviewer 不送模型。"""
    cleaned: list[dict] = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        if status:
            cleaned.append({"status": str(status)})
    return cleaned


def kb_hits_for_model(hits: list[dict] | None) -> list[dict]:
    """知识库命中可进提示词；做浅拷贝避免改到状态里的原文。"""
    return [deepcopy(hit) for hit in (hits or []) if isinstance(hit, dict)]
