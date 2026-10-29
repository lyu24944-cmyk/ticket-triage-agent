"""P0.1：文件注入、轮换窗口、日志脱敏、密钥不进 repr。"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi.testclient import TestClient

from ticket_triage.api.main import app
from ticket_triage.config import settings
from ticket_triage.secret_log import SecretRedactFilter, install_secret_redact_filter
from ticket_triage.secrets import clear_secret_cache, known_secret_values, read_secret_value


def _ticket(ticket_id: str) -> dict:
    return {
        "ticket": {
            "ticket_id": ticket_id,
            "subject": "紧急退款",
            "body": "我被重复扣款，需要退款",
        },
    }


def test_read_secret_prefers_file(tmp_path: Path) -> None:
    path = tmp_path / "service.key"
    path.write_text("from-file\n", encoding="utf-8")
    assert read_secret_value("from-env", str(path)) == "from-file"
    assert read_secret_value("from-env", str(tmp_path / "missing")) == "from-env"
    assert read_secret_value("  ", None) is None


def test_service_key_file_and_previous_accepted(monkeypatch, tmp_path: Path) -> None:
    current = tmp_path / "service-current"
    previous = tmp_path / "service-previous"
    current.write_text("file-new-service", encoding="utf-8")
    previous.write_text("file-old-service", encoding="utf-8")
    monkeypatch.setattr(settings, "service_api_key", None)
    monkeypatch.setattr(settings, "service_api_key_file", str(current))
    monkeypatch.setattr(settings, "service_api_key_previous", None)
    monkeypatch.setattr(settings, "service_api_key_previous_file", str(previous))
    monkeypatch.setattr(settings, "review_api_key", "review-only")
    clear_secret_cache()

    client = TestClient(app)
    for key, ticket_id in (("file-old-service", "sec-old"), ("file-new-service", "sec-new")):
        response = client.post(
            "/runs",
            json=_ticket(ticket_id),
            headers={"X-API-Key": key},
        )
        assert response.status_code == 202, response.text

    denied = client.post(
        "/runs",
        json=_ticket("sec-bad"),
        headers={"X-API-Key": "not-a-key"},
    )
    assert denied.status_code == 401
    assert denied.json()["errors"] == ["service_auth_failed"]


def test_previous_env_key_works_until_removed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "service_api_key", "svc-new")
    monkeypatch.setattr(settings, "service_api_key_previous", "svc-old")
    monkeypatch.setattr(settings, "service_api_key_file", None)
    monkeypatch.setattr(settings, "service_api_key_previous_file", None)
    clear_secret_cache()
    client = TestClient(app)
    assert (
        client.post(
            "/runs",
            json=_ticket("rot-old"),
            headers={"X-API-Key": "svc-old"},
        ).status_code
        == 202
    )

    monkeypatch.setattr(settings, "service_api_key_previous", None)
    clear_secret_cache()
    rejected = client.post(
        "/runs",
        json=_ticket("rot-gone"),
        headers={"X-API-Key": "svc-old"},
    )
    assert rejected.status_code == 401
    assert rejected.json()["errors"] == ["service_auth_failed"]
    assert (
        client.post(
            "/runs",
            json=_ticket("rot-new"),
            headers={"X-API-Key": "svc-new"},
        ).status_code
        == 202
    )


def test_settings_repr_hides_api_keys(monkeypatch) -> None:
    monkeypatch.setattr(settings, "service_api_key", "super-secret-service")
    monkeypatch.setattr(settings, "review_api_key", "super-secret-review")
    monkeypatch.setattr(settings, "openai_api_key", "sk-should-not-show")
    text = repr(settings)
    assert "super-secret-service" not in text
    assert "super-secret-review" not in text
    assert "sk-should-not-show" not in text


def test_log_filter_redacts_known_secrets(monkeypatch) -> None:
    monkeypatch.setattr(settings, "service_api_key", "leak-me-service-key")
    monkeypatch.setattr(settings, "review_api_key", "leak-me-review-key")
    clear_secret_cache()
    install_secret_redact_filter()
    assert "leak-me-service-key" in known_secret_values(force_refresh=True)

    logger = logging.getLogger("ticket_triage.test_secrets")
    logger.addFilter(SecretRedactFilter())
    record = logger.makeRecord(
        logger.name,
        logging.INFO,
        __file__,
        0,
        "auth header=leak-me-service-key other=leak-me-review-key",
        (),
        None,
    )
    assert SecretRedactFilter().filter(record) is True
    assert "leak-me-service-key" not in record.getMessage()
    assert "leak-me-review-key" not in record.getMessage()
    assert "[REDACTED_SECRET]" in record.getMessage()


def test_dockerfile_and_dockerignore_keep_env_out_of_image() -> None:
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (root / ".dockerignore").read_text(encoding="utf-8")
    assert "COPY .env" not in dockerfile
    assert ".env" in dockerignore
    assert "USER appuser" in dockerfile
