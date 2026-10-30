"""知识库检索 — 本地 FTS5 索引 + 关键词回退（无 RAG/MCP 时）。"""

from __future__ import annotations

import hashlib
import json

KB_ARTICLES: list[dict[str, str]] = [
    {
        "id": "kb-billing-refund",
        "title": "退款政策",
        "body": "未使用订阅可在 14 天内退款。回复时请提及政策编号 REF-14。",
    },
    {
        "id": "kb-tech-reset",
        "title": "密码重置",
        "body": "用户可通过 /settings/security 重置密码。切勿在对话中索要当前密码。",
    },
    {
        "id": "kb-account-delete",
        "title": "账号注销",
        "body": "注销需邮件确认，并有 7 天冷静期。",
    },
]


# 整句包含匹配，避免中文无空格时被拆词漏检；空查询不得返回文章
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "kb-billing-refund": ("refund", "billing", "charge", "invoice", "退款", "扣款", "账单"),
    "kb-tech-reset": ("password", "login", "密码", "登录"),
    "kb-account-delete": ("delete account", "account deletion", "注销", "关闭账号"),
}

_ARTICLES_BY_ID = {article["id"]: article for article in KB_ARTICLES}


def _corpus_fingerprint() -> str:
    payload = {
        "articles": KB_ARTICLES,
        "keywords": {key: list(value) for key, value in _KEYWORDS.items()},
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _ensure_kb_index() -> None:
    """把 KB 文章 upsert 进共享 SQLite 的 kb_fts；语料变更时重建。"""
    from ticket_triage.tools.tickets import _LOCK, _connect

    fingerprint = _corpus_fingerprint()
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT value FROM kb_fts_meta WHERE key = 'fingerprint'"
            ).fetchone()
            if row and row[0] == fingerprint:
                return
            conn.execute("DELETE FROM kb_fts")
            for article in KB_ARTICLES:
                keywords = " ".join(_KEYWORDS.get(article["id"], ()))
                conn.execute(
                    """
                    INSERT INTO kb_fts (article_id, title, body, keywords)
                    VALUES (?, ?, ?, ?)
                    """,
                    (article["id"], article["title"], article["body"], keywords),
                )
            conn.execute(
                """
                INSERT INTO kb_fts_meta (key, value) VALUES ('fingerprint', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (fingerprint,),
            )
            conn.commit()
        finally:
            conn.close()


def _keyword_scores(query_lower: str) -> dict[str, int]:
    scores: dict[str, int] = {}
    for article_id, keywords in _KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.lower() in query_lower)
        if score:
            scores[article_id] = score
    return scores


def _fts_scores(query: str, *, limit: int) -> dict[str, int]:
    """短查询走 trigram 子串；长查询不整句 MATCH（避免 AND 过严）。"""
    from ticket_triage.tools.tickets import _LOCK, _connect, _fts_quote

    term = query.strip()
    if len(term) > 64:
        return {}
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT article_id
                FROM kb_fts
                WHERE kb_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (_fts_quote(term), max(limit * 3, 9)),
            ).fetchall()
        except Exception:
            return {}
        finally:
            conn.close()
    # 按 FTS 返回顺序给分（越靠前越高）
    out: dict[str, int] = {}
    for index, (article_id,) in enumerate(rows):
        out[str(article_id)] = max(1, len(rows) - index)
    return out


def search_knowledge_base(query: str, limit: int = 3) -> list[dict[str, str]]:
    """FTS5 索引命中 + 关键词回退；空查询或无关文本返回空列表，不伪造引用。"""
    q = (query or "").strip()
    if not q:
        return []
    _ensure_kb_index()
    scores = _keyword_scores(q.lower())
    # 关键词权重更高，保证黄金集/长工单正文仍靠关键词命中
    for article_id, score in list(scores.items()):
        scores[article_id] = score * 10
    for article_id, score in _fts_scores(q, limit=limit).items():
        scores[article_id] = scores.get(article_id, 0) + score
    if not scores:
        return []
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    hits: list[dict[str, str]] = []
    for article_id, _ in ranked[:limit]:
        article = _ARTICLES_BY_ID.get(article_id)
        if article:
            hits.append(article)
    return hits
