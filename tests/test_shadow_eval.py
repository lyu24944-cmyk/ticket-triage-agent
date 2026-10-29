"""影子评测：只打分不写工单状态；坏提示词可走 PROMPT_PIN 回滚信号。"""

from __future__ import annotations

import json
from pathlib import Path

from ticket_triage.config import settings
from ticket_triage.graph.workflow import run_shadow_triage
from ticket_triage.models import RunRequest, TicketIn
from ticket_triage.tools import tickets as tickets_mod


def test_shadow_does_not_write_ticket_status() -> None:
    tickets_mod.seed_ticket("real-1", "紧急退款", "我被重复扣款，需要退款")
    before = tickets_mod.get_ticket("real-1")
    assert before is not None
    assert before["status"] == "open"
    history_len = len(before["history"])

    result = run_shadow_triage(
        RunRequest(
            ticket=TicketIn(
                ticket_id="shadow-real-1",
                subject=before["subject"],
                body=before["body"],
            )
        )
    )
    assert result.status == "shadow_scored"
    assert result.classification is not None
    assert result.draft is not None
    assert result.awaiting_human is False

    after = tickets_mod.get_ticket("real-1")
    assert after is not None
    assert after["status"] == "open"
    assert len(after["history"]) == history_len
    # 影子 id 也不该被入库
    assert tickets_mod.get_ticket("shadow-real-1") is None


def test_run_shadow_script_emits_rollback_signal(tmp_path, monkeypatch) -> None:
    import importlib.util
    import sys

    root = Path(__file__).resolve().parents[1]
    mod_path = root / "evals" / "run_shadow.py"
    spec = importlib.util.spec_from_file_location("run_shadow_under_test", mod_path)
    assert spec and spec.loader
    shadow = importlib.util.module_from_spec(spec)
    sys.modules["run_shadow_under_test"] = shadow
    spec.loader.exec_module(shadow)

    db = tmp_path / "tickets.db"
    monkeypatch.setattr(settings, "ticket_db_path", str(db))
    monkeypatch.setattr(settings, "runtime_db_path", str(tmp_path / "runtime.db"))
    tickets_mod.seed_ticket("s1", "紧急退款", "我被重复扣款，需要退款")
    tickets_mod.seed_ticket("s2", "Hello", "Just saying hi")
    before = {tid: tickets_mod.get_ticket(tid)["status"] for tid in ("s1", "s2")}

    report_path = tmp_path / "shadow_report.json"
    monkeypatch.setattr(shadow, "REPORT_PATH", report_path)
    # 旁路目录放在 tmp，避免写仓库 .data
    monkeypatch.setattr(shadow, "ROOT", tmp_path)
    code = shadow.main(
        ["--sample", "2", "--seed", "1", "--source", str(db), "--compare"]
    )
    assert code == 0
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["writes_status"] is False
    assert report["recommend_pin"] in {"current", "previous"}
    assert "PROMPT_PIN" in report["note"] or "销钉" in report["note"]
    assert report["pins"]["current"]["all_status_unchanged"] is True
    for tid, status in before.items():
        assert tickets_mod.get_ticket(tid)["status"] == status
