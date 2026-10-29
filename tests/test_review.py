"""审核台：待审列表，以及处理后从列表消失。"""

from fastapi.testclient import TestClient

from ticket_triage.api.main import app
from ticket_triage.auth_jwt import mint_review_token


def _bearer(sub: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_review_token(sub)}"}


def test_pending_lists_ticket_until_approved(service_headers, review_headers, finish_run) -> None:
    client = TestClient(app)
    created = finish_run(client, client.post(
        "/runs",
        headers=service_headers,
        json={
            "ticket": {
                "ticket_id": "review-1",
                "subject": "紧急退款",
                "body": "我被重复扣款，需要退款",
            },
        },
    ))
    assert created.status_code == 200
    assert created.json()["awaiting_human"] is True

    pending = client.get("/runs/pending", headers=review_headers)
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
    # Vite SPA：壳在 index.html，文案在 /review/assets/*.js
    assert 'id="root"' in page.text
    assert "/review/assets/" in page.text
    assert "工单审核台" in page.text

    decided = client.post(
        "/runs/human",
        headers=review_headers,
        json={"ticket_id": "review-1", "approved": True, "reviewer": "ada"},
    )
    assert decided.json()["status"] == "resolved"
    again = client.get("/runs/pending", headers=review_headers).json()
    assert all(item["ticket_id"] != "review-1" for item in again)

    done = client.get("/tickets", headers=review_headers)
    assert done.status_code == 200
    stored = [item for item in done.json() if item["ticket_id"] == "review-1"]
    assert stored[0]["status"] == "resolved"
    assert [item["status"] for item in stored[0]["history"]] == ["open", "resolved"]
    assert stored[0]["history"][-1]["reviewer"] == "ada"


def test_poll_follows_human_decision_and_reopen(service_headers, review_headers, finish_run) -> None:
    client = TestClient(app)
    created = finish_run(
        client,
        client.post(
            "/runs",
            headers=service_headers,
            json={
                "ticket": {
                    "ticket_id": "review-poll",
                    "subject": "紧急退款",
                    "body": "我被重复扣款，需要退款",
                },
            },
        ),
    )
    trace_id = created.json()["trace_id"]
    decided = client.post(
        "/runs/human",
        headers=review_headers,
        json={"ticket_id": "review-poll", "approved": True, "reviewer": "ada"},
    )
    assert decided.json()["status"] == "resolved"
    polled = client.get(f"/runs/{trace_id}", headers=service_headers)
    assert polled.status_code == 200
    assert polled.json()["status"] == "resolved"
    assert polled.json()["awaiting_human"] is False

    reopened = client.post(
        "/tickets/review-poll/reopen",
        headers=_bearer("bea"),
        json={},
    )
    assert reopened.status_code == 200
    again = client.get(f"/runs/{reopened.json()['trace_id']}", headers=service_headers)
    assert again.json()["status"] == "awaiting_human"
    assert again.json()["awaiting_human"] is True


def test_reopen_keeps_escalation_and_returns_to_queue(service_headers, review_headers, finish_run) -> None:
    client = TestClient(app)
    created = finish_run(client, client.post(
        "/runs",
        headers=service_headers,
        json={
            "ticket": {
                "ticket_id": "review-2",
                "subject": "紧急退款",
                "body": "我被重复扣款，需要退款",
            },
        },
    ))
    draft = created.json()["draft"]["body"]
    client.post(
        "/runs/human",
        headers=review_headers,
        json={"ticket_id": "review-2", "approved": False, "reviewer": "ada"},
    )

    reopened = client.post(
        "/tickets/review-2/reopen",
        headers=_bearer("bea"),
        json={},
    )
    assert reopened.status_code == 200
    assert reopened.json()["awaiting_human"] is True
    assert reopened.json()["draft"]["body"] == draft

    pending = client.get("/runs/pending", headers=review_headers).json()
    assert any(item["ticket_id"] == "review-2" for item in pending)
    done = client.get("/tickets", headers=review_headers).json()
    assert all(item["ticket_id"] != "review-2" for item in done)

    from ticket_triage.tools.tickets import get_ticket

    history = get_ticket("review-2")["history"]
    assert [item["status"] for item in history] == ["open", "escalated", "pending"]
    assert history[-2]["reviewer"] == "ada"
    assert history[-1]["reviewer"] == "bea"


def test_reopen_without_saved_draft_still_keeps_history(review_headers) -> None:
    from ticket_triage.tools.tickets import get_ticket, seed_ticket, update_ticket_status

    seed_ticket("old-1", "紧急退款", "需要退款")
    update_ticket_status("old-1", "escalated", None)
    client = TestClient(app)
    reopened = client.post("/tickets/old-1/reopen", headers=review_headers, json={"reviewer": "ada"})
    assert reopened.json()["awaiting_human"] is True
    assert "上次草稿未留档" in reopened.json()["draft"]["body"]
    history = [item["status"] for item in get_ticket("old-1")["history"]]
    assert history == ["open", "escalated", "pending"]


def test_pending_exposes_model_fallbacks(monkeypatch, service_headers, review_headers, finish_run) -> None:
    from ticket_triage.llm import ModelLLM

    monkeypatch.setattr(
        "ticket_triage.graph.nodes.get_llm",
        lambda: ModelLLM(lambda _system, _user: "not json"),
    )
    client = TestClient(app)
    created = finish_run(
        client,
        client.post(
            "/runs",
            headers=service_headers,
            json={
                "ticket": {
                    "ticket_id": "review-fallback",
                    "subject": "紧急退款",
                    "body": "我被重复扣款，需要退款",
                },
            },
        ),
    )
    assert created.status_code == 200
    item = next(
        row for row in client.get("/runs/pending", headers=review_headers).json()
        if row["ticket_id"] == "review-fallback"
    )
    assert any(error.startswith("classify_model_failed") for error in item["errors"])
    assert any(error.startswith("draft_model_failed") for error in item["errors"])
    # 前端把上述错误码渲染成中文警告；文案在构建后的 JS 包里
    from pathlib import Path

    from ticket_triage.api.main import _REVIEW_STATIC

    assets = _REVIEW_STATIC / "assets"
    js_blob = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore") for p in assets.glob("*.js")
    ) if assets.is_dir() else ""
    assert "分类模型失败" in js_blob
    assert "草稿模型失败" in js_blob
