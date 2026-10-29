"""P0.2：网关配置与应用 body 上限同口径；TLS / 限流声明齐全。"""

from __future__ import annotations

from pathlib import Path

from ticket_triage.limits import MAX_BODY_BYTES

ROOT = Path(__file__).resolve().parents[1]
NGINX = ROOT / "deploy" / "nginx" / "nginx.conf"
COMPOSE = ROOT / "deploy" / "docker-compose.yml"


def test_max_body_bytes_is_64000() -> None:
    assert MAX_BODY_BYTES == 64_000


def test_nginx_aligns_body_tls_and_rate_limits() -> None:
    text = NGINX.read_text(encoding="utf-8")
    assert f"client_max_body_size {MAX_BODY_BYTES}" in text
    assert "listen 443 ssl" in text
    assert "ssl_certificate" in text
    assert "limit_req_zone" in text
    assert "zone=triage_runs" in text
    assert "zone=triage_general" in text
    assert "limit_req_status 429" in text
    assert "location = /runs" in text
    assert "server api:8000" in text
    # 不得在网关配置里再开第二个 upstream 写库实例
    assert text.count("upstream ticket_triage_api") == 1


def test_compose_does_not_publish_api_port() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert "expose:" in text
    assert '"8000"' in text or "'8000'" in text or "- \"8000\"" in text
    # api 服务不得 ports 映射 8000；网关才对外
    api_block = text.split("gateway:")[0]
    assert "ports:" not in api_block
    assert "8443:443" in text
    # 不得用 replicas / deploy.replicas 把 API 拉成多副本
    assert "replicas:" not in text
