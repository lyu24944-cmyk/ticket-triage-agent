"""审核台：待审列表，以及处理后从列表消失。"""

from fastapi.testclient import TestClient

from ticket_triage.api.main import app


def test_pending_lists_ticket_until_approved() -> None:
    client = TestClient(app)
    created = client.post(
        "/runs",
        json={
            "auto_approve": False,
            "ticket": {
                "ticket_id": "review-1",
                "subject": "紧急退款",
                "body": "我被重复扣款，需要退款",
            },
        },
    )
    assert created.status_code == 200
    assert created.json()["awaiting_human"] is True

    pending = client.get("/runs/pending")
    assert pending.status_code == 200
    match = [item for item in pending.json() if item["ticket_id"] == "review-1"]
    assert len(match) == 1
    assert match[0]["category"] == "billing"
    assert "kb-billing" in "".join(match[0]["citations"])
    assert match[0]["draft_body"]
    assert match[0]["errors"] == []
    assert [call["name"] for call in match[0]["tool_calls"]] == [
        "search_knowledge_base",
        "get_ticket_history",
        "finish",
    ]

    page = client.get("/review")
    assert page.status_code == 200
    assert "工单审核台" in page.text
    assert "本次工具调用" in page.text

    decided = client.post("/runs/human", json={"ticket_id": "review-1", "approved": True})
    assert decided.json()["status"] == "resolved"
    again = client.get("/runs/pending").json()
    assert all(item["ticket_id"] != "review-1" for item in again)

    done = client.get("/tickets")
    assert done.status_code == 200
    stored = [item for item in done.json() if item["ticket_id"] == "review-1"]
    assert stored[0]["status"] == "resolved"
    assert [item["status"] for item in stored[0]["history"]] == ["open", "resolved"]


def test_reopen_keeps_escalation_and_returns_to_queue() -> None:
    client = TestClient(app)
    created = client.post(
        "/runs",
        json={
            "auto_approve": False,
            "ticket": {
                "ticket_id": "review-2",
                "subject": "紧急退款",
                "body": "我被重复扣款，需要退款",
            },
        },
    )
    draft = created.json()["draft"]["body"]
    client.post("/runs/human", json={"ticket_id": "review-2", "approved": False})

    reopened = client.post("/tickets/review-2/reopen")
    assert reopened.status_code == 200
    assert reopened.json()["awaiting_human"] is True
    assert reopened.json()["draft"]["body"] == draft

    pending = client.get("/runs/pending").json()
    assert any(item["ticket_id"] == "review-2" for item in pending)
    done = client.get("/tickets").json()
    assert all(item["ticket_id"] != "review-2" for item in done)

    from ticket_triage.tools.tickets import get_ticket

    history = [item["status"] for item in get_ticket("review-2")["history"]]
    assert history == ["open", "escalated", "pending"]


def test_reopen_without_saved_draft_still_keeps_history() -> None:
    from ticket_triage.tools.tickets import get_ticket, seed_ticket, update_ticket_status

    seed_ticket("old-1", "紧急退款", "需要退款")
    update_ticket_status("old-1", "escalated", None)
    client = TestClient(app)
    reopened = client.post("/tickets/old-1/reopen")
    assert reopened.json()["awaiting_human"] is True
    assert "上次草稿未留档" in reopened.json()["draft"]["body"]
    history = [item["status"] for item in get_ticket("old-1")["history"]]
    assert history == ["open", "escalated", "pending"]


def test_pending_exposes_model_fallbacks(monkeypatch) -> None:
    from ticket_triage.llm import ModelLLM

    monkeypatch.setattr(
        "ticket_triage.graph.nodes.get_llm",
        lambda: ModelLLM(lambda _system, _user: "not json"),
    )
    client = TestClient(app)
    created = client.post(
        "/runs",
        json={
            "auto_approve": False,
            "ticket": {
                "ticket_id": "review-fallback",
                "subject": "紧急退款",
                "body": "我被重复扣款，需要退款",
            },
        },
    )
    assert created.status_code == 200
    item = next(
        row for row in client.get("/runs/pending").json()
        if row["ticket_id"] == "review-fallback"
    )
    assert any(error.startswith("classify_model_failed") for error in item["errors"])
    assert any(error.startswith("draft_model_failed") for error in item["errors"])
    page = client.get("/review").text
    assert "分类模型失败，当前分类由关键词规则生成。" in page
    assert "草稿模型失败，当前草稿由 stub 生成。" in page
