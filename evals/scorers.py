"""确定性评测打分器。"""

from __future__ import annotations

from typing import Any


def score_category(actual: str, expected: str) -> float:
    """类别是否命中。"""
    return 1.0 if actual == expected else 0.0


def score_priority(actual: str, expected: str) -> float:
    """优先级是否命中。"""
    return 1.0 if actual == expected else 0.0


def score_citation(citations: list[str], prefix: str | None) -> float | None:
    """应命中 KB 时，引用非空且每一条都带期望前缀；无期望前缀时不计入该指标。"""
    if not prefix:
        return None
    if not citations:
        return 0.0
    return 1.0 if all(c.startswith(prefix) for c in citations) else 0.0


def score_no_invented_citation(citations: list[str], forbid: bool) -> float | None:
    """无关/空检索不得伪造引用。"""
    if not forbid:
        return None
    return 1.0 if not citations else 0.0


def score_nonempty_draft(body: str | None) -> float:
    """草稿是否非空。"""
    return 1.0 if body and body.strip() else 0.0


def _mean(values: list[float | None]) -> float:
    kept = [v for v in values if v is not None]
    if not kept:
        return 1.0
    return sum(kept) / len(kept)


def aggregate(rows: list[dict[str, Any]]) -> dict[str, float]:
    """聚合各条用例得分。引用类指标只统计适用样本。"""
    if not rows:
        return {
            "category_accuracy": 0.0,
            "priority_accuracy": 0.0,
            "citation_present_when_kb_hit": 0.0,
            "no_invented_citations": 0.0,
            "no_empty_draft": 0.0,
        }
    n = len(rows)
    return {
        "category_accuracy": sum(r["category"] for r in rows) / n,
        "priority_accuracy": sum(r["priority"] for r in rows) / n,
        "citation_present_when_kb_hit": _mean([r["citation"] for r in rows]),
        "no_invented_citations": _mean([r["no_invented"] for r in rows]),
        "no_empty_draft": sum(r["draft"] for r in rows) / n,
    }
