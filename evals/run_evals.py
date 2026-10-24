#!/usr/bin/env python3
"""P3 评测入口 — 低于阈值时以 exit code 1 失败（CI 门禁）。"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scorers import (  # noqa: E402
    aggregate,
    score_category,
    score_citation,
    score_illegal_tool_free,
    score_no_invented_citation,
    score_nonempty_draft,
    score_priority,
    score_tool_decision,
)
from ticket_triage.config import settings  # noqa: E402
from ticket_triage.graph.workflow import run_triage  # noqa: E402
from ticket_triage.llm import ModelLLM, get_llm  # noqa: E402
from ticket_triage.models import RunRequest, TicketIn  # noqa: E402

TOOL_VERSION = "p3-gateway"
USES_MODEL = isinstance(get_llm(), ModelLLM)
PROMPT_VERSION = f"{settings.llm_provider}-act-v2" if USES_MODEL else "stub-act-v1"

THRESHOLDS = {
    "category_accuracy": 0.85,
    "priority_accuracy": 0.75,
    "citation_present_when_kb_hit": 0.90,
    "no_invented_citations": 1.0,
    "no_empty_draft": 1.0,
    "illegal_tool_rate": 1.0,
    "tool_decision": 1.0,
}
# 模型不必和 stub 一样每次都选对工具；stub / CI 仍要求 1.0。
if USES_MODEL:
    THRESHOLDS["tool_decision"] = 0.70

GOLDEN = Path(__file__).parent / "golden" / "tickets.jsonl"
REPORT_PATH = Path(__file__).parent / "last_report.json"


def main() -> int:
    # 40 条黄金集按 3 篇关键词文档编写。忽略 .env 里的 RAG_BASE_URL，保证和 CI 同一知识库。
    settings.rag_base_url = None
    rows = []
    latencies = []
    case_results = []
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        expect = case["expect"]
        t0 = time.perf_counter()
        result = run_triage(
            RunRequest(
                ticket=TicketIn(
                    ticket_id=case["ticket_id"],
                    subject=case["subject"],
                    body=case["body"],
                ),
                auto_approve=True,
            )
        )
        latencies.append((time.perf_counter() - t0) * 1000)
        assert result.classification and result.draft
        citations = list(result.draft.citations)
        row = {
            "category": score_category(
                result.classification.category.value, expect["category"]
            ),
            "priority": score_priority(
                result.classification.priority.value, expect["priority"]
            ),
            "citation": score_citation(citations, expect.get("must_have_citation_prefix")),
            "no_invented": score_no_invented_citation(
                citations, bool(expect.get("forbid_citations"))
            ),
            "draft": score_nonempty_draft(result.draft.body),
            "illegal": score_illegal_tool_free(result.tool_calls),
            "tool_decision": score_tool_decision(
                result.tool_calls, expect.get("must_search")
            ),
        }
        rows.append(row)
        case_results.append(
            {
                "ticket_id": case["ticket_id"],
                "citations": citations,
                "category": result.classification.category.value,
                "priority": result.classification.priority.value,
                "draft_model_failed": any(
                    item.startswith("draft_model_failed") for item in result.errors
                ),
                "classify_model_failed": any(
                    item.startswith("classify_model_failed") for item in result.errors
                ),
                "act_model_failed": any(
                    item.startswith("act_model_failed") for item in result.errors
                ),
                "tool_calls": [call.get("name") for call in result.tool_calls],
                "illegal": row["illegal"],
                "tool_decision": row["tool_decision"],
                "errors": list(result.errors),
            }
        )

    metrics = aggregate(rows)
    latencies_sorted = sorted(latencies)
    p95_index = max(0, int(len(latencies_sorted) * 0.95) - 1)
    metrics["p95_latency_ms"] = latencies_sorted[p95_index]

    report = {
        "tool_version": TOOL_VERSION,
        "prompt_version": PROMPT_VERSION,
        "metrics": metrics,
        "thresholds": THRESHOLDS,
        "cases": case_results,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))

    failed = []
    for key, floor in THRESHOLDS.items():
        if metrics.get(key, 0) < floor:
            failed.append(f"{key}: {metrics.get(key)} < {floor}")
    # 延迟预算只约束本地 stub；真实模型的耗时只记录在报告里
    if not USES_MODEL and metrics["p95_latency_ms"] >= 2000:
        failed.append(f"p95_latency_ms: {metrics['p95_latency_ms']} >= 2000")
    # 退回 stub 的草稿不能算作模型通过
    fell_back = [c["ticket_id"] for c in case_results if c["draft_model_failed"]]
    if USES_MODEL and fell_back:
        failed.append(f"draft_model_failed: {', '.join(fell_back)}")
    classify_fell_back = [c["ticket_id"] for c in case_results if c["classify_model_failed"]]
    if USES_MODEL and classify_fell_back:
        failed.append(f"classify_model_failed: {', '.join(classify_fell_back)}")
    act_fell_back = [c["ticket_id"] for c in case_results if c["act_model_failed"]]
    if USES_MODEL and act_fell_back:
        failed.append(f"act_model_failed: {', '.join(act_fell_back)}")

    if failed:
        print("评测门禁失败:", "; ".join(failed), file=sys.stderr)
        return 1
    print("评测门禁通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
