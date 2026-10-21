"""P6：把知识库检索委托给 citation-rag-agent 的 /ask。"""

from __future__ import annotations

import httpx

from ticket_triage.config import settings
from ticket_triage.tools.kb import KB_ARTICLES

# 英文句必须是中文原文的直译。模型会把摘录当作政策引用，多出的说法会被写进回复。
_RAG_BODIES: dict[str, str] = {
    "kb-billing-refund": (
        "未使用订阅可在 14 天内退款。回复时请提及政策编号 REF-14。 "
        "Unused subscriptions can be refunded within 14 days. "
        "Mention policy number REF-14 in the reply."
    ),
    "kb-tech-reset": (
        "用户可通过 /settings/security 重置密码。切勿在对话中索要当前密码。 "
        "Users can reset their password at /settings/security. "
        "Never ask for the current password in the conversation."
    ),
    "kb-account-delete": (
        "注销需邮件确认，并有 7 天冷静期。 "
        "Account deletion requires email confirmation and has a 7-day cooling-off period."
    ),
}

_SEEDED: set[str] = set()


def clear_seed_cache() -> None:
    """测试里换假服务时清掉已入库标记。"""
    _SEEDED.clear()


def _articles() -> list[dict[str, str]]:
    documents = []
    for article in KB_ARTICLES:
        documents.append(
            {
                "doc_id": article["id"],
                "title": article["title"],
                "body": _RAG_BODIES.get(article["id"], article["body"]),
            }
        )
    return documents


def _ensure_seeded(client: httpx.Client, base_url: str) -> None:
    if base_url in _SEEDED:
        return
    for document in _articles():
        response = client.post("/documents", json=document)
        response.raise_for_status()
    _SEEDED.add(base_url)


def search_knowledge_base(
    query: str,
    limit: int = 3,
    client: httpx.Client | None = None,
) -> list[dict[str, str]]:
    """提问 P4。没有依据时返回空列表，不把问答服务的空话当成引用。"""
    text = (query or "").strip()
    if not text:
        return []
    base_url = (settings.rag_base_url or "").rstrip("/")
    if not base_url and client is None:
        return []

    owns_client = client is None
    if client is None:
        client = httpx.Client(base_url=base_url, timeout=settings.rag_timeout_seconds)
    try:
        _ensure_seeded(client, base_url or str(client.base_url))
        response = client.post("/ask", json={"question": text, "top_k": limit})
        response.raise_for_status()
        payload = response.json()
        if payload.get("abstained"):
            return []
        # 问答服务的库里可能还有别的语料，只接收工单知识库自己的文档
        known = {article["id"] for article in KB_ARTICLES}
        hits: list[dict[str, str]] = []
        for item in payload.get("citations") or []:
            if str(item.get("doc_id")) not in known:
                continue
            hits.append(
                {
                    "id": str(item["doc_id"]),
                    "title": str(item.get("title") or ""),
                    "body": str(item.get("excerpt") or ""),
                }
            )
        return hits[:limit]
    finally:
        if owns_client:
            client.close()
