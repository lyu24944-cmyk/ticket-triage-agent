"""真实模型分类与起草。引用只能来自本次检索结果；CI 默认不走这条路径。"""

from __future__ import annotations

import json
import re
from typing import Callable

import httpx

from ticket_triage.config import settings
from ticket_triage.llm.actions import ACT_SYSTEM, act_user_prompt, parse_action
from ticket_triage.llm.prompts import CLASSIFY_SYSTEM, DRAFT_SYSTEM, classify_prompt, draft_prompt
from ticket_triage.models import Category, Classification, DraftReply, Priority, TicketIn

Complete = Callable[[str, str], str]

_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-5",
    "openai": "gpt-4o-mini",
}
_TRANSPORT_ATTEMPTS = 3


def _post(http: httpx.Client, url: str, **kwargs) -> httpx.Response:
    """传输中断时重试。模型返回的坏 JSON 不在这里重试。"""
    for attempt in range(_TRANSPORT_ATTEMPTS):
        try:
            return http.post(url, **kwargs)
        except httpx.TransportError:
            if attempt + 1 == _TRANSPORT_ATTEMPTS:
                raise
    raise RuntimeError("transport retries exhausted")


def _classify_prompt(ticket: TicketIn) -> str:
    return classify_prompt(ticket)


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
    tool_calls: list[dict] | None = None,
    history: list | None = None,
) -> str:
    return draft_prompt(ticket, classification, kb_hits, tool_calls, history)


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
        from ticket_triage.privacy import ticket_for_model

        raw = self._complete(CLASSIFY_SYSTEM, _classify_prompt(ticket_for_model(ticket)))
        return _classification_from_payload(_parse_json(raw))

    def draft_reply(
        self,
        ticket: TicketIn,
        classification: Classification,
        kb_hits: list[dict[str, str]],
        tool_calls: list[dict] | None = None,
        history: list | None = None,
    ) -> DraftReply:
        from ticket_triage.privacy import ticket_for_model

        safe = ticket_for_model(ticket)
        raw = self._complete(
            DRAFT_SYSTEM,
            _draft_prompt(safe, classification, kb_hits, tool_calls, history),
        )
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
        kb_hits: list[dict] | None = None,
        history: list | None = None,
    ) -> dict:
        from ticket_triage.privacy import ticket_for_model

        raw = self._complete(
            ACT_SYSTEM,
            act_user_prompt(
                ticket_for_model(ticket), classification, tool_calls, kb_hits, history
            ),
        )
        return parse_action(_parse_json(raw))


def anthropic_complete(client: httpx.Client | None = None) -> Complete:
    model = settings.llm_model or _DEFAULT_MODELS["anthropic"]

    def complete(system: str, user: str) -> str:
        owns = client is None
        http = client or httpx.Client(timeout=settings.llm_timeout_seconds)
        try:
            response = _post(
                http,
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
            payload = response.json()
            parts = payload.get("content") or []
            text = "".join(part.get("text", "") for part in parts if part.get("type") == "text")
            thinking = "".join(part.get("thinking", "") for part in parts if part.get("type") == "thinking")
            _trace_generation(
                _observation_name(system),
                model,
                system,
                user,
                text,
                _anthropic_usage(payload),
                thinking or None,
            )
            return text
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
            response = _post(
                http,
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
            payload = response.json()
            message = payload["choices"][0]["message"] or {}
            text = message.get("content") or ""
            thinking = message.get("reasoning_content") or message.get("reasoning")
            _trace_generation(
                _observation_name(system),
                model,
                system,
                user,
                text,
                _openai_usage(payload),
                thinking or None,
            )
            return text
        finally:
            if owns:
                http.close()

    return complete


def _observation_name(system: str) -> str:
    if "工具调度" in system:
        return "choose-tool"
    if "分类" in system:
        return "classify-ticket"
    return "draft-reply"


def _trace_generation(
    name: str,
    model: str,
    system: str,
    user: str,
    output: str,
    usage: dict[str, int] | None,
    thinking: str | None = None,
) -> None:
    try:
        from ticket_triage.observability import record_generation

        record_generation(
            name=name,
            model=model,
            system=system,
            user=user,
            output=output,
            usage=usage,
            metadata={"reasoning": thinking} if thinking else None,
        )
    except Exception:  # noqa: BLE001 — 追踪失败不能影响模型调用
        return


def _openai_usage(payload: dict) -> dict[str, int] | None:
    raw = payload.get("usage") or {}
    usage: dict[str, int] = {}
    if "prompt_tokens" in raw:
        usage["input"] = int(raw["prompt_tokens"])
    if "completion_tokens" in raw:
        usage["output"] = int(raw["completion_tokens"])
    if "total_tokens" in raw:
        usage["total"] = int(raw["total_tokens"])
    return usage or None


def _anthropic_usage(payload: dict) -> dict[str, int] | None:
    raw = payload.get("usage") or {}
    usage: dict[str, int] = {}
    if "input_tokens" in raw:
        usage["input"] = int(raw["input_tokens"])
    if "output_tokens" in raw:
        usage["output"] = int(raw["output_tokens"])
    if usage:
        usage["total"] = usage.get("input", 0) + usage.get("output", 0)
    return usage or None
