"""测试夹具：隔离 SQLite 路径。"""

from __future__ import annotations

import pytest

from ticket_triage.config import settings


@pytest.fixture(autouse=True)
def _isolate_ticket_db(tmp_path, monkeypatch):
    db = tmp_path / "tickets.db"
    monkeypatch.setattr(settings, "ticket_db_path", str(db))
    monkeypatch.setattr(settings, "runtime_db_path", str(tmp_path / "runtime.db"))
    monkeypatch.setattr(settings, "use_mcp", False)
    monkeypatch.setattr(settings, "rag_base_url", None)
    monkeypatch.setattr(settings, "llm_provider", "stub")
    monkeypatch.setattr(settings, "langfuse_public_key", None)
    monkeypatch.setattr(settings, "langfuse_secret_key", None)
    yield
