"""工作流各节点实现。"""

from __future__ import annotations

from ticket_triage.config import settings
from ticket_triage.graph.state import TriageState
from ticket_triage.llm import StubLLM, get_llm
from ticket_triage.tools import gateway


def ingest_node(state: TriageState) -> dict:
    """入库工单。"""
    ticket = state["ticket"]
    gateway.seed_ticket(ticket.ticket_id, ticket.subject, ticket.body)
    return {"status": "ingested", "errors": [], "tool_retries": 0}


def classify_node(state: TriageState) -> dict:
    """分类优先级与类别。"""
    llm = get_llm()
    classification = llm.classify(state["ticket"])
    return {"classification": classification, "status": "classified"}


def enrich_node(state: TriageState) -> dict:
    """检索知识库与历史；失败时记录错误供重试。"""
    ticket = state["ticket"]
    classification = state.get("classification")
    # 主题 + 正文一起检索，避免正文里的「退款/refund」被丢掉
    query = (
        f"{ticket.subject} {ticket.body} "
        f"{classification.category.value if classification else ''}"
    )
    try:
        hits = gateway.search_knowledge_base(query)
        history = gateway.get_ticket_history(ticket.ticket_id)
        return {
            "kb_hits": hits,
            "history": history,
            "status": "enriched",
            "tool_retries": 0,
        }
    except Exception as exc:  # noqa: BLE001 — 工具失败写入状态
        retries = int(state.get("tool_retries") or 0) + 1
        return {
            "errors": [f"enrich_failed: {exc}"],
            "tool_retries": retries,
            "kb_hits": [],
            "status": "enrich_error",
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
