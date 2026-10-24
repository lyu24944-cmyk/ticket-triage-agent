"""P2 MCP 验收测试。"""

from __future__ import annotations

import asyncio

import pytest

from mcp_server.handlers import handle_update_ticket_status
from mcp_server.server import list_tools
from ticket_triage.config import settings
from ticket_triage.graph.workflow import run_triage
from ticket_triage.models import RunRequest, TicketIn
from ticket_triage.tools.mcp_gateway import list_mcp_tools_async


def test_tool_specs_have_three_tools():
    names = {t["name"] for t in list_tools()}
    assert names == {
        "search_knowledge_base",
        "get_ticket_history",
        "update_ticket_status",
    }


def test_mutating_tool_rejects_unknown_status():
    out = handle_update_ticket_status("t-x", "delete_all")
    assert out["error"] == "status_not_allowed"
    assert "resolved" in out["allowed"]


@pytest.mark.integration
def test_mcp_server_lists_tools_over_stdio():
    names = asyncio.run(list_mcp_tools_async())
    assert set(names) == {
        "search_knowledge_base",
        "get_ticket_history",
        "update_ticket_status",
    }


@pytest.mark.integration
def test_use_mcp_enrich_matches_inprocess_shape(monkeypatch):
    monkeypatch.setattr(settings, "use_mcp", True)
    monkeypatch.setattr(settings, "require_hitl", False)
    result = run_triage(
        RunRequest(
            ticket=TicketIn(
                ticket_id="mcp-1",
                subject="Urgent refund request",
                body="I was charged twice, need a refund",
            ),
            auto_approve=True,
        )
    )
    assert result.status == "resolved"
    assert result.classification is not None
    assert result.classification.category.value == "billing"
    assert result.draft is not None
    assert result.draft.citations
    assert any(c.startswith("kb-billing") for c in result.draft.citations)


def test_enrich_soft_fallback_on_mcp_timeout(monkeypatch):
    """超时记 enrich_failed，最终仍可起草（软降级）。"""
    from ticket_triage.tools import tickets as tickets_mod

    def _boom(*_a, **_k):
        raise TimeoutError("simulated")

    monkeypatch.setattr(settings, "max_tool_retries", 0)
    monkeypatch.setattr(settings, "require_hitl", False)
    # enrich 读工具超时；finalize 写库走进程内实现
    monkeypatch.setattr(
        "ticket_triage.graph.nodes.gateway.search_knowledge_base", _boom
    )
    monkeypatch.setattr(
        "ticket_triage.graph.nodes.gateway.get_ticket_history", lambda *_a, **_k: []
    )
    monkeypatch.setattr(
        "ticket_triage.graph.nodes.gateway.update_ticket_status",
        tickets_mod.update_ticket_status,
    )

    result = run_triage(
        RunRequest(
            ticket=TicketIn(
                ticket_id="mcp-timeout",
                subject="Urgent refund request",
                body="I was charged twice, need a refund",
            ),
            auto_approve=True,
        )
    )
    assert result.status == "resolved"
    assert any("enrich_failed" in e for e in result.errors)
    assert result.draft is not None
