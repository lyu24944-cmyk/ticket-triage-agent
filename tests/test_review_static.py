"""审核台静态产物：/review 可打开；资产路径不越界。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ticket_triage.api.main import _REVIEW_STATIC, app

STATIC_INDEX = _REVIEW_STATIC / "index.html"


def test_review_page_serves_spa_or_fallback() -> None:
    client = TestClient(app)
    response = client.get("/review")
    assert response.status_code in {200, 503}
    if STATIC_INDEX.is_file():
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        body = response.text
        assert "工单审核台" in body or "root" in body


def test_review_assets_when_built() -> None:
    assets = _REVIEW_STATIC / "assets"
    if not assets.is_dir():
        return
    files = list(assets.glob("index-*.js")) + list(assets.glob("*.js"))
    if not files:
        return
    name = files[0].name
    client = TestClient(app)
    response = client.get(f"/review/assets/{name}")
    assert response.status_code == 200


def test_review_asset_rejects_traversal() -> None:
    client = TestClient(app)
    response = client.get("/review/assets/../../../../pyproject.toml")
    assert response.status_code == 404


def test_built_index_uses_review_base() -> None:
    if not STATIC_INDEX.is_file():
        return
    text = STATIC_INDEX.read_text(encoding="utf-8")
    assert "/review/assets/" in text
