"""P0.3：非 root 门禁与安全工作流声明。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from check_dockerfile_nonroot import APP_UID, APP_USER, check_compose, check_dockerfile  # noqa: E402



def test_dockerfile_ends_as_non_root_appuser() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert check_dockerfile(text) == []
    assert f"--uid {APP_UID}" in text
    assert f"USER {APP_USER}" in text


def test_compose_does_not_force_root() -> None:
    text = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    assert check_compose(text) == []


def test_security_workflow_has_audit_trivy_and_uid_gate() -> None:
    text = (ROOT / ".github" / "workflows" / "security.yml").read_text(encoding="utf-8")
    assert "pip-audit" in text
    assert "audit-reqs.txt" in text
    assert "trivy-action" in text
    assert "CRITICAL,HIGH" in text
    assert "check_dockerfile_nonroot.py" in text
    assert APP_UID in text
    assert "entrypoint id" in text
