"""确定性评测打分器。"""

from __future__ import annotations

from typing import Any


def score_category(actual: str, expected: str) -> float:
    """类别是否命中。"""
    return 1.0 if actual == expected else 0.0


def score_priority(actual: str, expected: str) -> float:
    """优先级是否命中。"""
    return 1.0 if actual == expected else 0.0


def score_citation(citations: list[str], prefix: str | None) -> float:
    """应命中 KB 时是否带正确引用前缀。"""
    if prefix is None:
        return 1.0
    return 1.0 if any(c.startswith(prefix) for c in citations) else 0.0


def score_nonempty_draft(body: str | None) -> float:
    """草稿是否非空。"""
    return 1.0 if body and body.strip() else 0.0


def aggregate(rows: list[dict[str, Any]]) -> dict[str, float]:
    """聚合各条用例得分。"""
    if not rows:
        return {
            "category_accuracy": 0.0,
            "priority_accuracy": 0.0,
            "citation_present_when_kb_hit": 0.0,
            "no_empty_draft": 0.0,
        }
    n = len(rows)
    return {
        "category_accuracy": sum(r["category"] for r in rows) / n,
        "priority_accuracy": sum(r["priority"] for r in rows) / n,
        "citation_present_when_kb_hit": sum(r["citation"] for r in rows) / n,
        "no_empty_draft": sum(r["draft"] for r in rows) / n,
    }
