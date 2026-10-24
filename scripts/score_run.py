"""给一次演示运行打确定性分数。配了 Langfuse 时贴到同一条 trace。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals.scorers import (
    score_category,
    score_citation,
    score_illegal_tool_free,
    score_no_invented_citation,
    score_priority,
    score_tool_decision,
)
from ticket_triage.observability import score_eval_case

_PROFILES = {
    "refund": {
        "category": "billing",
        "priority": "high",
        "citation_prefix": "kb-billing",
        "forbid_citations": False,
        "must_search": True,
    },
    "greeting": {
        "category": "other",
        "priority": "low",
        "citation_prefix": None,
        "forbid_citations": True,
        "must_search": False,
    },
}


def score_payload(profile_name: str, payload: dict) -> dict[str, float | None]:
    profile = _PROFILES[profile_name]
    classification = payload.get("classification") or {}
    draft = payload.get("draft") or {}
    calls = list(payload.get("tool_calls") or [])
    citations = list(draft.get("citations") or [])
    return {
        "category_accuracy": score_category(str(classification.get("category")), profile["category"]),
        "priority_accuracy": score_priority(str(classification.get("priority")), profile["priority"]),
        "citation_present_when_kb_hit": score_citation(citations, profile["citation_prefix"]),
        "no_invented_citations": score_no_invented_citation(citations, profile["forbid_citations"]),
        "illegal_tool_free": score_illegal_tool_free(calls),
        "tool_decision": score_tool_decision(calls, profile["must_search"]),
    }


def assert_absent(ticket_id: str, payload: list) -> None:
    if any(item.get("ticket_id") == ticket_id for item in payload):
        raise SystemExit(f"通过之前 {ticket_id} 不应出现在已处理列表")


def assert_resolved(ticket_id: str, payload: list) -> None:
    hits = [item for item in payload if item.get("ticket_id") == ticket_id]
    if len(hits) != 1:
        raise SystemExit(f"期望恰好一张 {ticket_id}，实际 {len(hits)}")
    status = hits[0].get("status")
    history = [item.get("status") for item in hits[0].get("history") or []]
    if status != "resolved" or "open" not in history or "resolved" not in history:
        raise SystemExit(f"期望 open → resolved，实际 status={status} history={history}")


def main() -> None:
    profile_name = sys.argv[1]
    payload = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8-sig"))
    if profile_name == "absent":
        assert_absent(sys.argv[3], payload)
        return
    if profile_name == "resolved":
        assert_resolved(sys.argv[3], payload)
        return
    scores = score_payload(profile_name, payload)
    errors = [str(item) for item in (payload.get("errors") or [])]
    fallback = any(item.startswith(("classify_model_failed", "draft_model_failed", "act_model_failed")) for item in errors)
    print(json.dumps({"profile": profile_name, "scores": scores, "fallback": fallback}, ensure_ascii=False))
    failed = [name for name, value in scores.items() if value is not None and value < 1.0]
    if failed or fallback or payload.get("status") != "awaiting_human":
        raise SystemExit(f"演示打分未通过: {failed or payload.get('status')} fallback={fallback}")
    score_eval_case(payload.get("trace_id"), scores)


if __name__ == "__main__":
    main()
