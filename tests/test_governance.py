"""模型治理只读入口：销钉与失败信号口径，不含密钥。"""

from fastapi.testclient import TestClient

from ticket_triage.api.main import app
from ticket_triage.config import settings

_FIELDS = {
    "readonly",
    "prompt_pin",
    "prompt_rev",
    "pin_status",
    "prompt_version",
    "llm_provider",
    "uses_model",
    "path_note",
    "rollback",
    "ci",
    "signals",
}


def test_governance_is_public_readonly_stub() -> None:
    client = TestClient(app)
    response = client.get("/v1/governance/model")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == _FIELDS
    assert body["readonly"] is True
    assert body["prompt_pin"] == "current"
    assert body["prompt_rev"] == "v3"
    assert body["pin_status"] == "ok"
    assert body["prompt_version"] == "stub-act-v1"
    assert body["llm_provider"] == "stub"
    assert body["uses_model"] is False
    assert "stub" in body["path_note"]
    codes = [item["code"] for item in body["signals"]]
    assert codes == [
        "classify_model_failed",
        "act_model_failed",
        "draft_model_failed",
    ]
    assert "分类模型失败" in body["signals"][0]["label"]
    assert "草稿模型失败" in body["signals"][2]["label"]
    assert "PROMPT_PIN" in body["rollback"]
    assert "CI" in body["ci"]
    assert "test-webhook-secret" not in response.text
    assert "test-service-key" not in response.text
    assert "test-review-key" not in response.text
    denied = client.post("/v1/governance/model")
    assert denied.status_code == 405


def test_governance_follows_previous_pin(monkeypatch) -> None:
    monkeypatch.setattr(settings, "prompt_pin", "openai-act-v2")
    body = TestClient(app).get("/v1/governance/model").json()
    assert body["prompt_pin"] == "previous"
    assert body["prompt_rev"] == "v2"
    assert body["prompt_version"] == "stub-act-v1"
    assert body["uses_model"] is False


def test_governance_reports_model_path_without_secrets(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "sk-live-should-not-leak")
    monkeypatch.setattr(settings, "prompt_pin", "current")
    response = TestClient(app).get("/v1/governance/model")
    body = response.json()
    assert body["uses_model"] is True
    assert body["llm_provider"] == "openai"
    assert body["prompt_version"] == "openai-act-v3"
    assert "sk-live-should-not-leak" not in response.text
    assert "api.openai.com" not in response.text


def test_governance_hides_unknown_pin_and_provider(monkeypatch) -> None:
    monkeypatch.setattr(settings, "prompt_pin", "sk-not-a-pin")
    monkeypatch.setattr(settings, "llm_provider", "https://secret.example/v1")
    response = TestClient(app).get("/v1/governance/model")
    body = response.json()
    assert body["pin_status"] == "unknown"
    assert body["prompt_pin"] is None
    assert body["prompt_rev"] is None
    assert body["prompt_version"] is None
    assert body["llm_provider"] == "other"
    assert body["uses_model"] is False
    assert "sk-not-a-pin" not in response.text
    assert "secret.example" not in response.text
