"""真实模型分类与起草。引用只能来自本次检索结果；CI 默认不走这条路径。"""

from __future__ import annotations

import json
import re
from typing import Callable

import httpx

from ticket_triage.config import settings
from ticket_triage.llm.actions import ACT_SYSTEM, act_user_prompt, parse_action
from ticket_triage.models import Category, Classification, DraftReply, Priority, TicketIn

Complete = Callable[[str, str], str]

CLASSIFY_SYSTEM = (
    "你是客服工单分类助手。只根据工单主题和正文判断类别与优先级。"
    "只输出一个 JSON 对象，格式为 "
    '{"category": "billing", "priority": "low", "rationale": "一句话理由"}。'
    "category 只能是 billing、technical、account、other 之一："
    "账单、扣款、退款是 billing；登录、密码、故障是 technical；注销账号是 account；其余是 other。"
    "priority 只能是 low、medium、high、critical 之一："
    "紧急、无法使用、重复扣款用 high；一般咨询用 medium；问候和无关内容用 low。"
)

DRAFT_SYSTEM = (
    "你是客服工单的回复起草助手。只能依据用户消息里给出的知识库文档作答，"
    "不得编造政策、数字或文档编号。"
    "只输出一个 JSON 对象，格式为 "
    '{"body": "回复正文", "citations": ["文档编号"]}。'
    "citations 只能填写给出的文档编号；没有给出文档时 citations 必须是空列表，"
    "正文只说明会继续跟进，不要引用任何政策。"
)

_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-5",
    "openai": "gpt-4o-mini",
}


def _classify_prompt(ticket: TicketIn) -> str:
    return f"工单主题：{ticket.subject}\n工单正文：{ticket.body}"


def _classification_from_payload(payload: dict) -> Classification:
    category_raw = str(payload.get("category") or "").strip().lower()
    priority_raw = str(payload.get("priority") or "").strip().lower()
    rationale = str(payload.get("rationale") or "").strip()
    if category_raw not in Category._value2member_map_:
        raise ValueError("model_output_bad_category")
    if priority_raw not in Priority._value2member_map_:
        raise ValueError("model_output_bad_priority")
    if not rationale:
        raise ValueError("model_output_empty_rationale")
    category = Category(category_raw)
    priority = Priority(priority_raw)
    needs_escalation = priority in (Priority.high, Priority.critical) and category == Category.billing
    return Classification(
        priority=priority,
        category=category,
        rationale=rationale,
        needs_escalation=needs_escalation,
    )


def _draft_prompt(
    ticket: TicketIn,
    classification: Classification,
    kb_hits: list[dict[str, str]],
) -> str:
    lines = [
        f"工单主题：{ticket.subject}",
        f"工单正文：{ticket.body}",
        f"类别：{classification.category.value}，优先级：{classification.priority.value}",
    ]
    if kb_hits:
        lines.append("可引用的知识库文档：")
        for hit in kb_hits:
            lines.append(f"- [{hit['id']}] {hit.get('title', '')}：{hit.get('body', '')}")
    else:
        lines.append("可引用的知识库文档：无")
    return "\n".join(lines)


def _parse_json(text: str) -> dict:
    """模型可能包一层代码块，取第一个 { 到最后一个 }。"""
    cleaned = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.MULTILINE).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("model_output_not_json")
    return json.loads(cleaned[start : end + 1])


def keep_retrieved_citations(cited: list, kb_hits: list[dict[str, str]]) -> list[str]:
    """丢掉不在本次检索结果里的编号，保留顺序并去重。"""
    allowed = {hit["id"] for hit in kb_hits}
    kept: list[str] = []
    for item in cited or []:
        value = str(item)
        if value in allowed and value not in kept:
            kept.append(value)
    return kept


class ModelLLM:
    """complete(system, user) 返回模型原文。分类和起草都走模型。"""

    def __init__(self, complete: Complete) -> None:
        self._complete = complete

    def classify(self, ticket: TicketIn) -> Classification:
        raw = self._complete(CLASSIFY_SYSTEM, _classify_prompt(ticket))
        return _classification_from_payload(_parse_json(raw))

    def draft_reply(
        self,
        ticket: TicketIn,
        classification: Classification,
        kb_hits: list[dict[str, str]],
    ) -> DraftReply:
        raw = self._complete(DRAFT_SYSTEM, _draft_prompt(ticket, classification, kb_hits))
        payload = _parse_json(raw)
        body = str(payload.get("body") or "").strip()
        if not body:
            raise ValueError("model_output_empty_body")
        citations = keep_retrieved_citations(payload.get("citations") or [], kb_hits)
        if classification.needs_escalation:
            body += " 【已标记账单升级】"
        confidence = 0.75 if citations else 0.4
        return DraftReply(body=body, citations=citations, confidence=confidence)

    def next_action(
        self,
        ticket: TicketIn,
        classification: Classification | None,
        tool_calls: list[dict],
    ) -> dict:
        raw = self._complete(ACT_SYSTEM, act_user_prompt(ticket, classification, tool_calls))
        return parse_action(_parse_json(raw))


def anthropic_complete(client: httpx.Client | None = None) -> Complete:
    model = settings.llm_model or _DEFAULT_MODELS["anthropic"]

    def complete(system: str, user: str) -> str:
        owns = client is None
        http = client or httpx.Client(timeout=settings.llm_timeout_seconds)
        try:
            response = http.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.anthropic_api_key or "",
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": model,
                    "max_tokens": 800,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
            )
            response.raise_for_status()
            parts = response.json().get("content") or []
            return "".join(part.get("text", "") for part in parts if part.get("type") == "text")
        finally:
            if owns:
                http.close()

    return complete


def openai_complete(client: httpx.Client | None = None) -> Complete:
    """OpenAI 兼容接口；OPENAI_BASE_URL 可指向其他兼容服务。"""
    model = settings.llm_model or _DEFAULT_MODELS["openai"]
    base_url = settings.openai_base_url.rstrip("/")

    def complete(system: str, user: str) -> str:
        owns = client is None
        http = client or httpx.Client(timeout=settings.llm_timeout_seconds)
        try:
            response = http.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {settings.openai_api_key or ''}"},
                json={
                    "model": model,
                    "temperature": 0,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"] or ""
        finally:
            if owns:
                http.close()

    return complete
