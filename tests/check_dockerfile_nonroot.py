"""P0.3：Dockerfile 必须以非 root（appuser / uid 10001）收尾。可直接 python 运行。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
COMPOSE = ROOT / "deploy" / "docker-compose.yml"
APP_UID = "10001"
APP_USER = "appuser"


def check_dockerfile(text: str) -> list[str]:
    errors: list[str] = []
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if "useradd" not in text or f"--uid {APP_UID}" not in text:
        errors.append(f"expected useradd --uid {APP_UID}")
    user_lines = [line for line in lines if line.upper().startswith("USER ")]
    if not user_lines:
        errors.append("missing USER instruction")
        return errors
    if user_lines[-1] != f"USER {APP_USER}":
        errors.append(f"final USER must be {APP_USER}, got {user_lines[-1]!r}")
    return errors


def check_compose(text: str) -> list[str]:
    errors: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip().lower().replace('"', "").replace("'", "")
        if line.startswith("user:") and line.split(":", 1)[1].strip() in {"root", "0"}:
            errors.append(f"compose must not set {raw.strip()}")
    return errors


def main() -> int:
    errors = check_dockerfile(DOCKERFILE.read_text(encoding="utf-8"))
    if COMPOSE.is_file():
        errors.extend(check_compose(COMPOSE.read_text(encoding="utf-8")))
    if errors:
        for item in errors:
            print(f"FAIL: {item}", file=sys.stderr)
        return 1
    print(f"OK: Dockerfile ends as USER {APP_USER} (uid {APP_UID})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
