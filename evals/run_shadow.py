#!/usr/bin/env python3
"""影子评测：对新提示词抽样真实单只打分，不写工单状态。

用法（仓库根目录）::

    python evals/run_shadow.py
    python evals/run_shadow.py --sample 20 --compare
    python evals/run_shadow.py --source .data/tickets.db

验收信号：退回率（*_model_failed）升高时报告建议 PROMPT_PIN=previous。
不进 CI 黄金门禁；本地 / 内测抽真实库。无真实单时回退黄金集正文（仍不写生产库状态）。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scorers import (  # noqa: E402
    score_illegal_tool_free,
    score_nonempty_draft,
    score_no_invented_citation,
    score_tool_decision,
)
from ticket_triage.config import settings  # noqa: E402
from ticket_triage.graph.workflow import run_shadow_triage  # noqa: E402
from ticket_triage.llm import ModelLLM, get_llm  # noqa: E402
from ticket_triage.models import RunRequest, TicketIn  # noqa: E402
from ticket_triage.observability import prompt_version  # noqa: E402
from ticket_triage.tools import tickets as tickets_mod  # noqa: E402

GOLDEN = Path(__file__).parent / "golden" / "tickets.jsonl"
REPORT_PATH = Path(__file__).parent / "shadow_report.json"


def _load_golden_samples(limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        rows.append(
            {
                "ticket_id": case["ticket_id"],
                "subject": case["subject"],
                "body": case["body"],
                "tenant_id": "default",
                "source": "golden",
                "expect": case.get("expect") or {},
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _load_db_samples(
    *,
    db_path: Path,
    sample: int,
    seed: int,
    tenant_id: str,
) -> list[dict[str, Any]]:
    settings.ticket_db_path = str(db_path)
    # 影子只读正文；runtime 指到旁路，避免误写 pending
    shadow_dir = ROOT / ".data" / "evals_shadow"
    shadow_dir.mkdir(parents=True, exist_ok=True)
    settings.runtime_db_path = str(shadow_dir / "runtime.db")
    all_rows = tickets_mod.list_tickets(tenant_id=tenant_id, limit=500)
    usable = [
        row
        for row in all_rows
        if (row.get("subject") or "").strip() and (row.get("body") or "").strip()
    ]
    if not usable:
        return []
    rng = random.Random(seed)
    if len(usable) > sample:
        usable = rng.sample(usable, sample)
    else:
        rng.shuffle(usable)
    return [
        {
            "ticket_id": row["ticket_id"],
            "subject": row["subject"],
            "body": row["body"],
            "tenant_id": row.get("tenant_id") or tenant_id,
            "source": "tickets.db",
            "prior_status": row.get("status"),
            "expect": {},
        }
        for row in usable
    ]


def _mean(values: list[float | None]) -> float:
    kept = [v for v in values if v is not None]
    if not kept:
        return 1.0
    return sum(kept) / len(kept)


def _score_case(sample: dict[str, Any], result) -> dict[str, Any]:
    expect = sample.get("expect") or {}
    citations = list(result.draft.citations) if result.draft else []
    draft_body = result.draft.body if result.draft else ""
    fell = {
        "classify_model_failed": any(
            e.startswith("classify_model_failed") for e in result.errors
        ),
        "act_model_failed": any(e.startswith("act_model_failed") for e in result.errors),
        "draft_model_failed": any(
            e.startswith("draft_model_failed") for e in result.errors
        ),
    }
    fallback = any(fell.values())
    return {
        "ticket_id": sample["ticket_id"],
        "source": sample.get("source"),
        "prior_status": sample.get("prior_status"),
        "fallback": fallback,
        **fell,
        "tool_calls": [call.get("name") for call in result.tool_calls],
        "citations": citations,
        "category": (
            result.classification.category.value if result.classification else None
        ),
        "no_empty_draft": score_nonempty_draft(draft_body),
        "illegal_tool_free": score_illegal_tool_free(result.tool_calls),
        "tool_decision": score_tool_decision(
            result.tool_calls, expect.get("must_search")
        ),
        "no_invented_citations": score_no_invented_citation(
            citations, bool(expect.get("forbid_citations"))
        ),
        "errors": list(result.errors),
    }


def _run_pin(samples: list[dict[str, Any]], pin: str) -> dict[str, Any]:
    os.environ["PROMPT_PIN"] = pin
    settings.prompt_pin = pin
    version = prompt_version()
    case_results = []
    for sample in samples:
        # 影子用独立 ticket_id，避免误触真实单的 pending / 状态写路径
        shadow_id = f"shadow-{sample['ticket_id']}"[:64]
        result = run_shadow_triage(
            RunRequest(
                ticket=TicketIn(
                    ticket_id=shadow_id,
                    subject=sample["subject"],
                    body=sample["body"],
                    tenant_id=sample.get("tenant_id") or "default",
                )
            )
        )
        scored = _score_case(sample, result)
        if sample.get("source") == "tickets.db":
            after = tickets_mod.get_ticket(
                sample["ticket_id"],
                tenant_id=sample.get("tenant_id") or "default",
            )
            scored["status_after"] = after.get("status") if after else None
            scored["status_unchanged"] = scored.get("prior_status") == scored.get(
                "status_after"
            )
        else:
            scored["status_after"] = None
            scored["status_unchanged"] = True
        case_results.append(scored)

    fallback_n = sum(1 for c in case_results if c["fallback"])
    total = len(case_results) or 1
    metrics = {
        "no_empty_draft": _mean([c["no_empty_draft"] for c in case_results]),
        "illegal_tool_rate": _mean([c["illegal_tool_free"] for c in case_results]),
        "tool_decision": _mean([c["tool_decision"] for c in case_results]),
        "no_invented_citations": _mean(
            [c["no_invented_citations"] for c in case_results]
        ),
        "fallback_rate": fallback_n / total,
    }
    return {
        "prompt_pin": pin,
        "prompt_version": version,
        "sample_size": len(case_results),
        "fallback_tickets": fallback_n,
        "fallback_rate": fallback_n / total,
        "metrics": metrics,
        "cases": case_results,
        "uses_model": isinstance(get_llm(), ModelLLM),
        "all_status_unchanged": all(c.get("status_unchanged") for c in case_results),
    }


def _recommend(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    if previous is None:
        rate = current.get("fallback_rate") or 0
        prefer_previous = rate > 0.2 and current.get("uses_model")
        return {
            "recommend_pin": "previous" if prefer_previous else "current",
            "fallback_rate": rate,
            "note": (
                "影子退回率偏高：设 PROMPT_PIN=previous 即回滚，不要求再训练。"
                if prefer_previous
                else "影子退回率可接受；保持 PROMPT_PIN=current。无对比时仅看当前销钉。"
            ),
        }
    cur_rate = current.get("fallback_rate") or 0
    prev_rate = previous.get("fallback_rate") or 0
    tool_cur = (current.get("metrics") or {}).get("tool_decision") or 0
    tool_prev = (previous.get("metrics") or {}).get("tool_decision") or 0
    prefer_previous = (cur_rate > prev_rate + 1e-9) or (tool_cur + 1e-9 < tool_prev)
    return {
        "recommend_pin": "previous" if prefer_previous else "current",
        "delta": {
            "fallback_rate": cur_rate - prev_rate,
            "tool_decision": tool_cur - tool_prev,
        },
        "note": (
            "退回变多或工具选择掉下去：设 PROMPT_PIN=previous 即回滚完成，不要求再训练。"
            if prefer_previous
            else "当前版不差于上一版；保持 PROMPT_PIN=current。"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="影子评测：只打分不写状态")
    parser.add_argument("--sample", type=int, default=20, help="抽样条数")
    parser.add_argument("--seed", type=int, default=42, help="抽样随机种子")
    parser.add_argument(
        "--source",
        type=str,
        default="",
        help="真实 tickets.db 路径；空则用 settings / .data/tickets.db",
    )
    parser.add_argument("--tenant-id", type=str, default="default")
    parser.add_argument(
        "--compare",
        action="store_true",
        help="串行跑 current 与 previous，给出 PROMPT_PIN 回滚建议",
    )
    parser.add_argument(
        "--pin",
        type=str,
        default="current",
        help="单销钉模式（--compare 时忽略）",
    )
    args = parser.parse_args(argv)

    # 影子不打外网 RAG；与黄金评测一致用关键词库，避免污染对比
    settings.rag_base_url = None

    db = Path(args.source) if args.source else (
        Path(settings.ticket_db_path) if settings.ticket_db_path else ROOT / ".data" / "tickets.db"
    )
    samples: list[dict[str, Any]] = []
    sample_source = "golden"
    if db.exists():
        samples = _load_db_samples(
            db_path=db,
            sample=args.sample,
            seed=args.seed,
            tenant_id=args.tenant_id,
        )
        if samples:
            sample_source = str(db)
    if not samples:
        samples = _load_golden_samples(args.sample)
        sample_source = "golden"

    # 旁路 runtime，避免影子写 pending
    shadow_dir = ROOT / ".data" / "evals_shadow"
    shadow_dir.mkdir(parents=True, exist_ok=True)
    settings.runtime_db_path = str(shadow_dir / "runtime.db")
    if sample_source == "golden":
        settings.ticket_db_path = str(shadow_dir / "tickets.db")
        tickets_path = Path(settings.ticket_db_path)
        if tickets_path.exists():
            tickets_path.unlink()

    pins = ["current", "previous"] if args.compare else [args.pin]
    reports: dict[str, Any] = {}
    for pin in pins:
        print(f"影子跑 pin={pin} sample={len(samples)} source={sample_source}…", flush=True)
        reports[pin] = _run_pin(samples, pin)

    current = reports.get("current") or reports[pins[0]]
    previous = reports.get("previous") if args.compare else None
    advice = _recommend(current, previous)
    out = {
        "sample_source": sample_source,
        "sample_size": len(samples),
        "writes_status": False,
        "pins": reports,
        **advice,
    }
    REPORT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"已写入 {REPORT_PATH}")
    print(advice["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
