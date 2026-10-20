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
    score_nonempty_draft,
    score_priority,
)
from ticket_triage.graph.workflow import run_triage  # noqa: E402
from ticket_triage.models import RunRequest, TicketIn  # noqa: E402

THRESHOLDS = {
    "category_accuracy": 0.85,
    "priority_accuracy": 0.75,
    "citation_present_when_kb_hit": 0.90,
    "no_empty_draft": 1.0,
}

GOLDEN = Path(__file__).parent / "golden" / "tickets.jsonl"


def main() -> int:
    rows = []
    latencies = []
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
        rows.append(
            {
                "category": score_category(
                    result.classification.category.value, expect["category"]
                ),
                "priority": score_priority(
                    result.classification.priority.value, expect["priority"]
                ),
                "citation": score_citation(
                    result.draft.citations, expect.get("must_have_citation_prefix")
                ),
                "draft": score_nonempty_draft(result.draft.body),
            }
        )

    metrics = aggregate(rows)
    latencies_sorted = sorted(latencies)
    p95 = latencies_sorted[max(0, int(len(latencies_sorted) * 0.95) - 1)]
    metrics["p95_latency_ms"] = p95

    report = {
        "tool_version": "p1-inprocess",
        "prompt_version": "stub-v1",
        "metrics": metrics,
        "thresholds": THRESHOLDS,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))

    failed = []
    for key, floor in THRESHOLDS.items():
        if metrics.get(key, 0) < floor:
            failed.append(f"{key}: {metrics.get(key)} < {floor}")
    if metrics["p95_latency_ms"] >= 2000:
        failed.append(f"p95_latency_ms: {metrics['p95_latency_ms']} >= 2000")

    if failed:
        print("评测门禁失败:", "; ".join(failed), file=sys.stderr)
        return 1
    print("评测门禁通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
