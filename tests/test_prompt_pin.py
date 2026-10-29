"""PROMPT_PIN 在 current / previous 间切换；不打外网模型。"""

from ticket_triage.config import settings
from ticket_triage.llm.prompts import (
    PREVIOUS_REV,
    CURRENT_REV,
    act_prompt,
    classify_prompt,
    draft_prompt,
    prompt_tag,
    resolve_prompt_rev,
)
from ticket_triage.models import Category, Classification, Priority, TicketIn
from ticket_triage.observability import prompt_version


def _ticket() -> TicketIn:
    return TicketIn(ticket_id="pin-1", subject="紧急退款", body="我被重复扣款，需要退款")


def test_resolve_pin_aliases() -> None:
    assert resolve_prompt_rev("current") == CURRENT_REV
    assert resolve_prompt_rev("v3") == CURRENT_REV
    assert resolve_prompt_rev("previous") == PREVIOUS_REV
    assert resolve_prompt_rev("v2") == PREVIOUS_REV
    assert resolve_prompt_rev("openai-act-v2") == PREVIOUS_REV


def test_stub_version_ignores_pin(monkeypatch) -> None:
    monkeypatch.setattr(settings, "prompt_pin", "previous")
    monkeypatch.setattr(settings, "llm_provider", "stub")
    assert prompt_version() == "stub-act-v1"
    assert prompt_tag(provider="openai", uses_model=False) == "stub-act-v1"


def test_model_tag_follows_pin(monkeypatch) -> None:
    monkeypatch.setattr(settings, "prompt_pin", "previous")
    assert prompt_tag(provider="openai", uses_model=True) == "openai-act-v2"
    monkeypatch.setattr(settings, "prompt_pin", "current")
    assert prompt_tag(provider="openai", uses_model=True) == "openai-act-v3"


def test_v2_act_lists_failed_tools_v3_keeps_compact_error(monkeypatch) -> None:
    ticket = _ticket()
    classification = Classification(
        priority=Priority.high,
        category=Category.billing,
        rationale="billing",
    )
    calls = [
        {
            "name": "search_knowledge_base",
            "arguments": {"query": "x"},
            "ok": False,
            "error": "enrich_failed: timeout",
        }
    ]
    monkeypatch.setattr(settings, "prompt_pin", "previous")
    v2 = act_prompt(ticket, classification, calls)
    assert "已执行的动作" in v2
    assert "enrich_failed: timeout" in v2

    monkeypatch.setattr(settings, "prompt_pin", "current")
    v3 = act_prompt(ticket, classification, calls)
    assert "上一步错误：enrich_failed" in v3
    assert "已执行的动作" not in v3


def test_classify_and_draft_switch_shapes(monkeypatch) -> None:
    ticket = _ticket()
    classification = Classification(
        priority=Priority.high,
        category=Category.billing,
        rationale="billing",
    )
    monkeypatch.setattr(settings, "prompt_pin", "previous")
    assert "工单主题" in classify_prompt(ticket)
    assert "可引用的知识库文档：无" in draft_prompt(ticket, classification, [])
    monkeypatch.setattr(settings, "prompt_pin", "current")
    assert "工单主题" in classify_prompt(ticket)
    assert "可引用的知识库文档：无" in draft_prompt(ticket, classification, [])
