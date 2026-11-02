"""故障案例只读入口：已记录的三次评测失败，不含密钥，不改门禁。"""

from fastapi.testclient import TestClient

from ticket_triage.api.main import app
from ticket_triage.config import settings

_FIELDS = {"readonly", "source", "ci", "measured", "cases"}


def test_failure_cases_are_public_and_fixed() -> None:
    client = TestClient(app)
    response = client.get("/v1/failure-cases")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == _FIELDS
    assert body["readonly"] is True
    assert "P7_AGENT_PLAN.md" in body["source"]
    assert "stub" in body["ci"]
    assert "openai-act-v3" in body["measured"]["model"]
    assert "1.0" in body["measured"]["stub"]
    ids = [item["id"] for item in body["cases"]]
    assert ids == ["search-out-of-scope", "transport-fallback", "inject-no-write"]
    signals = [item["signal"] for item in body["cases"]]
    assert "act_model_failed" in signals[1]
    assert "tool_not_allowed" in signals[2]
    assert "不改黄金集" in body["cases"][0]["do"]
    assert "不要为这一条改 CI 门槛" in body["cases"][1]["do"]
    assert "test-webhook-secret" not in response.text
    assert "test-service-key" not in response.text
    assert "test-review-key" not in response.text
    assert client.post("/v1/failure-cases").status_code == 405


def test_failure_cases_ignore_runtime_secrets(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "sk-live-should-not-leak")
    monkeypatch.setattr(settings, "prompt_pin", "previous")
    response = TestClient(app).get("/v1/failure-cases")
    body = response.json()
    assert body["cases"][0]["id"] == "search-out-of-scope"
    assert "sk-live-should-not-leak" not in response.text
    assert "previous" not in response.text
