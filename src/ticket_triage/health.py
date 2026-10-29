"""就绪检查：数据库可写、单进程锁、已配置时的问答服务。"""

from __future__ import annotations

import httpx

from ticket_triage import __version__
from ticket_triage.config import settings
from ticket_triage.process_lock import holds_lock
from ticket_triage.runtime import active_job_count
from ticket_triage.runtime import probe_writable as probe_runtime
from ticket_triage.tools.tickets import probe_writable as probe_tickets


def _db_check(probe) -> dict:
    try:
        probe()
    except Exception as exc:  # noqa: BLE001 — 就绪检查只暴露异常类型
        return {"ok": False, "error": type(exc).__name__}
    return {"ok": True}


def _rag_check() -> dict:
    base = (settings.rag_base_url or "").rstrip("/")
    if not base:
        return {"ok": True, "skipped": True}
    timeout = min(3.0, settings.rag_timeout_seconds)
    try:
        response = httpx.get(f"{base}/health", timeout=timeout)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — 问答服务不可用时标记未就绪
        return {"ok": False, "skipped": False, "error": type(exc).__name__}
    return {"ok": True, "skipped": False}


def readiness() -> tuple[int, dict]:
    """返回 HTTP 状态码和响应体。未就绪是 503。"""
    checks = {
        "runtime_db": _db_check(probe_runtime),
        "tickets_db": _db_check(probe_tickets),
        "rag": _rag_check(),
        "single_process": {"ok": holds_lock()},
        "queue": {
            "ok": True,
            "active": active_job_count(),
            "limit": max(1, int(settings.run_queue_limit)),
            "workers": max(1, int(settings.run_workers)),
        },
    }
    ok = all(bool(item.get("ok")) for item in checks.values())
    return (200 if ok else 503), {"ok": ok, "version": __version__, "checks": checks}
