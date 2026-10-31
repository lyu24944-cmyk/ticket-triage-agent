"""P0.3：非 root 门禁与安全工作流声明。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
_DEPLOY = ROOT / "deploy"
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))
if str(_DEPLOY) not in sys.path:
    sys.path.insert(0, str(_DEPLOY))

from check_dockerfile_nonroot import (
    APP_UID,
    APP_USER,
    check_compose,
    check_dockerfile,
)
from check_runtime_image_deps import (
    floor_errors,
    pip_vendor_bom,
    strip_pip,
)


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
    assert "aquasecurity/trivy-action@v0.35.0" in text
    assert 'exit-code: "1"' in text
    assert "ignore-unfixed: true" in text
    assert "CRITICAL,HIGH" in text
    assert "check_dockerfile_nonroot.py" in text
    assert APP_UID in text
    assert "entrypoint id" in text


def test_image_build_strips_pip_vendor_sbom_and_pins_floors() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "COPY deploy/check_runtime_image_deps.py" in dockerfile
    assert "check_runtime_image_deps.py --strip-pip" in dockerfile
    assert "urllib3>=2.8.0" in pyproject
    assert "setuptools>=78.1.1" in pyproject
    assert (ROOT / "deploy" / "check_runtime_image_deps.py").is_file()
    rules = [
        line.strip()
        for line in ignore.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert not any(rule in {"deploy", "deploy/*", "deploy/**"} for rule in rules)


def test_runtime_dep_floors() -> None:
    assert floor_errors({"urllib3": "2.8.0", "msgpack": None, "setuptools": None}) == []
    assert floor_errors({"urllib3": "2.7.0", "msgpack": "1.1.2", "setuptools": "70.3.0"})
    assert floor_errors({"urllib3": None, "msgpack": None, "setuptools": "78.1.1"})
    assert floor_errors({"urllib3": "2.8.0", "msgpack": "1.2.3", "setuptools": "78.1.1"}) == []


def test_strip_pip_removes_vendor_sbom(tmp_path: Path) -> None:
    purelib = tmp_path / "site-packages"
    scripts = tmp_path / "bin"
    bom = pip_vendor_bom(purelib)
    bom.parent.mkdir(parents=True)
    bom.write_text('{"components":[]}', encoding="utf-8")
    (purelib / "pip" / "_vendor" / "urllib3").mkdir()
    (purelib / "pip" / "_vendor" / "urllib3" / "_version.py").write_text(
        "__version__ = '2.7.0'\n", encoding="utf-8"
    )
    dist = purelib / "pip-26.2.2.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Name: pip\n", encoding="utf-8")
    scripts.mkdir()
    (scripts / "pip").write_text("#!/usr/bin/env python\n", encoding="utf-8")
    (purelib / "urllib3").mkdir()
    (purelib / "urllib3" / "__init__.py").write_text("__version__ = '2.8.0'\n", encoding="utf-8")

    strip_pip(purelib, scripts)

    assert not bom.exists()
    assert not (purelib / "pip").exists()
    assert not dist.exists()
    assert not (scripts / "pip").exists()
    assert (purelib / "urllib3" / "__init__.py").is_file()
