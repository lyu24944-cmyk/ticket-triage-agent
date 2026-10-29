#!/usr/bin/env python3
"""同一套黄金集：current 与 previous 各跑一次，对比类别、引用、工具选择、退回次数。

不进 CI。CI 仍只跑 stub + 当前 PROMPT_PIN，避免一次流水线打两次外网模型。
退回变多或工具选择掉下去时，把 PROMPT_PIN=previous 即算回滚完成。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = Path(__file__).parent / "last_report.json"
COMPARE_OUT = Path(__file__).parent / "prompt_compare.json"


def _run(pin: str) -> dict:
    import os

    env = os.environ.copy()
    env["PROMPT_PIN"] = pin
    # 子进程串行写同一 last_report.json；对比脚本不进 CI，避免一次流水线打两次外网。
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "run_evals.py")],
        cwd=str(ROOT),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if not REPORT.exists():
        raise SystemExit(f"pin={pin} 没有写出 last_report.json\n{proc.stdout}\n{proc.stderr}")
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    report["_exit_code"] = proc.returncode
    report["_stderr"] = (proc.stderr or "").strip()
    if proc.returncode != 0:
        print(f"pin={pin} 门禁未过: {report['_stderr'] or proc.stdout[-500:]}", flush=True)
    return report


def _fallbacks(cases: list[dict]) -> int:
    total = 0
    for case in cases:
        for key in ("classify_model_failed", "act_model_failed", "draft_model_failed"):
            if case.get(key):
                total += 1
                break
    return total


def _summary(report: dict) -> dict:
    metrics = report.get("metrics") or {}
    return {
        "prompt_version": report.get("prompt_version"),
        "category_accuracy": metrics.get("category_accuracy"),
        "citation_present_when_kb_hit": metrics.get("citation_present_when_kb_hit"),
        "no_invented_citations": metrics.get("no_invented_citations"),
        "tool_decision": metrics.get("tool_decision"),
        "fallback_tickets": _fallbacks(report.get("cases") or []),
        "gate_ok": report.get("_exit_code") == 0,
    }


def compare(current: dict, previous: dict) -> dict:
    cur = _summary(current)
    prev = _summary(previous)
    tool_drop = (prev.get("tool_decision") or 0) - (cur.get("tool_decision") or 0)
    fallback_up = (cur.get("fallback_tickets") or 0) - (prev.get("fallback_tickets") or 0)
    prefer_previous = tool_drop > 0 or fallback_up > 0
    return {
        "current": cur,
        "previous": prev,
        "delta": {
            "category_accuracy": (cur.get("category_accuracy") or 0)
            - (prev.get("category_accuracy") or 0),
            "citation_present_when_kb_hit": (cur.get("citation_present_when_kb_hit") or 0)
            - (prev.get("citation_present_when_kb_hit") or 0),
            "tool_decision": (cur.get("tool_decision") or 0) - (prev.get("tool_decision") or 0),
            "fallback_tickets": fallback_up,
        },
        "recommend_pin": "previous" if prefer_previous else "current",
        "note": (
            "退回变多或工具选择掉下去：设 PROMPT_PIN=previous 即回滚完成，不要求再训练。"
            if prefer_previous
            else "当前版不差于上一版；保持 PROMPT_PIN=current。"
        ),
    }


def main() -> int:
    print("跑 current (v3)…", flush=True)
    current = _run("current")
    print("跑 previous (v2)…", flush=True)
    previous = _run("previous")
    result = compare(current, previous)
    COMPARE_OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"已写入 {COMPARE_OUT}")
    print(result["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
