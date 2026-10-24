"""工作流各节点实现。"""

from __future__ import annotations

from ticket_triage.config import settings
from ticket_triage.graph.state import TriageState
from ticket_triage.llm import StubLLM, get_llm
from ticket_triage.llm.actions import ALLOWED_ACT_TOOLS, MAX_ACT_STEPS, stub_next_action
from ticket_triage.tools import gateway


def ingest_node(state: TriageState) -> dict:
    """入库工单。"""
    ticket = state["ticket"]
    gateway.seed_ticket(ticket.ticket_id, ticket.subject, ticket.body)
    return {"status": "ingested", "errors": [], "tool_retries": 0}


def classify_node(state: TriageState) -> dict:
    """分类优先级与类别。模型失败时退回关键词结果。"""
    llm = get_llm()
    try:
        classification = llm.classify(state["ticket"])
    except Exception as exc:  # noqa: BLE001 — 模型失败时退回 stub 分类，流程继续
        classification = StubLLM().classify(state["ticket"])
        return {
            "classification": classification,
            "status": "classified",
            "errors": [f"classify_model_failed: {exc}"],
        }
    return {"classification": classification, "status": "classified"}


def _already_succeeded(calls: list[dict], name: str, arguments: dict) -> bool:
    for call in calls:
        if call.get("ok") and call.get("name") == name and call.get("arguments") == arguments:
            return True
    return False


def _finished(calls: list[dict]) -> bool:
    return any(call.get("ok") and call.get("name") == "finish" for call in calls)


def _merge_hits(existing: list[dict] | None, hits: list[dict]) -> list[dict]:
    """同一次运行里多次检索都保留，后一次不覆盖前面已经拿到的文档。"""
    merged = [dict(hit) for hit in (existing or [])]
    seen = {hit.get("id") for hit in merged}
    for hit in hits:
        hit_id = hit.get("id")
        if hit_id in seen:
            continue
        merged.append(dict(hit))
        seen.add(hit_id)
    return merged


def act_node(state: TriageState) -> dict:
    """按策略选择一个只读工具或结束。写状态不在白名单内。"""
    calls = [dict(item) for item in (state.get("tool_calls") or [])]
    if _finished(calls) or len(calls) >= MAX_ACT_STEPS:
        errors: list[str] = []
        if not _finished(calls):
            errors.append("act_max_steps")
        return {"tool_calls": calls, "status": "enriched", "errors": errors}

    ticket = state["ticket"]
    classification = state.get("classification")
    llm = get_llm()
    use_stub = bool(state.get("act_use_stub"))
    errors = []
    if use_stub:
        action = stub_next_action(ticket, classification, calls)
    else:
        try:
            action = llm.next_action(ticket, classification, calls)
        except Exception as exc:  # noqa: BLE001 — 坏 JSON 或模型错误时改走 stub
            errors.append(f"act_model_failed: {exc}")
            use_stub = True
            action = stub_next_action(ticket, classification, calls)

    base = {"act_use_stub": use_stub, "tool_calls": calls}
    if action.get("action") == "finish":
        calls.append({"name": "finish", "arguments": {}, "ok": True, "error": None})
        return {**base, "status": "enriched", "errors": errors}

    name = str(action.get("name") or "")
    arguments = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
    if name not in ALLOWED_ACT_TOOLS:
        error = f"tool_not_allowed:{name}"
        calls.append({"name": name, "arguments": dict(arguments), "ok": False, "error": error})
        errors.append(error)
        return {**base, "status": "acting", "errors": errors}

    if name == "search_knowledge_base":
        arguments = {"query": str(arguments.get("query") or "")}
    else:
        arguments = {"ticket_id": str(arguments.get("ticket_id") or "")}

    if _already_succeeded(calls, name, arguments):
        error = f"tool_duplicate:{name}"
        calls.append({"name": name, "arguments": arguments, "ok": False, "error": error})
        errors.append(error)
        return {**base, "status": "acting", "errors": errors}

    if name == "get_ticket_history" and arguments["ticket_id"] != ticket.ticket_id:
        calls.append(
            {
                "name": name,
                "arguments": arguments,
                "ok": False,
                "error": "ticket_id_mismatch",
            }
        )
        errors.append("ticket_id_mismatch")
        return {**base, "status": "acting", "errors": errors}

    try:
        if name == "search_knowledge_base":
            hits = gateway.search_knowledge_base(arguments["query"])
            calls.append({"name": name, "arguments": arguments, "ok": True, "error": None})
            return {
                **base,
                "kb_hits": _merge_hits(state.get("kb_hits"), hits),
                "status": "acting",
                "tool_retries": 0,
                "errors": errors,
            }
        history = gateway.get_ticket_history(arguments["ticket_id"])
        calls.append({"name": name, "arguments": arguments, "ok": True, "error": None})
        return {
            **base,
            "history": history,
            "status": "acting",
            "tool_retries": 0,
            "errors": errors,
        }
    except Exception as exc:  # noqa: BLE001 — 工具失败写入状态，由图决定是否重试
        retries = int(state.get("tool_retries") or 0) + 1
        error = f"enrich_failed: {exc}"
        calls.append({"name": name, "arguments": arguments, "ok": False, "error": error})
        errors.append(error)
        return {
            **base,
            "tool_retries": retries,
            "status": "enrich_error",
            "errors": errors,
        }


def draft_node(state: TriageState) -> dict:
    """生成带引用的回复草稿。"""
    llm = get_llm()
    classification = state.get("classification")
    if classification is None:
        return {"errors": ["draft_without_classification"], "status": "failed"}
    kb_hits = state.get("kb_hits") or []
    try:
        draft = llm.draft_reply(state["ticket"], classification, kb_hits)
    except Exception as exc:  # noqa: BLE001 — 模型失败时退回 stub 草稿，仍进人审
        draft = StubLLM().draft_reply(state["ticket"], classification, kb_hits)
        return {"draft": draft, "status": "drafted", "errors": [f"draft_model_failed: {exc}"]}
    return {"draft": draft, "status": "drafted"}


def hitl_gate_node(state: TriageState) -> dict:
    """人工审核关卡：未自动通过则暂停。"""
    if state.get("auto_approve") or not settings.require_hitl:
        return {"awaiting_human": False, "human_approved": True, "status": "auto_approved"}
    return {"awaiting_human": True, "status": "awaiting_human"}


def finalize_node(state: TriageState) -> dict:
    """根据人审结果结案或升级。"""
    ticket = state["ticket"]
    approved = state.get("human_approved")
    draft = state.get("draft")
    if approved is False:
        gateway.update_ticket_status(ticket.ticket_id, "escalated", note=state.get("human_edits"))
        return {"status": "escalated", "awaiting_human": False}
    body = state.get("human_edits") or (draft.body if draft else "")
    gateway.update_ticket_status(ticket.ticket_id, "resolved", note=body[:200])
    return {"status": "resolved", "awaiting_human": False}
