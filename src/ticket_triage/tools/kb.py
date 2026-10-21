"""模拟知识库 — P2 将通过 MCP 暴露同一接口。"""

from __future__ import annotations

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


def search_knowledge_base(query: str, limit: int = 3) -> list[dict[str, str]]:
    """按关键词匹配返回模拟 KB 命中；空查询或无关文本返回空列表，不伪造引用。"""
    q = (query or "").lower().strip()
    if not q:
        return []
    scored: list[tuple[int, dict[str, str]]] = []
    for article in KB_ARTICLES:
        score = sum(1 for kw in _KEYWORDS.get(article["id"], ()) if kw.lower() in q)
        if score:
            scored.append((score, article))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [a for _, a in scored[:limit]]
