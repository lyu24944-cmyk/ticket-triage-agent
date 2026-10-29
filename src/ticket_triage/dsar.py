"""按 ticket_id 导出 / 删除（DSAR）；删除后写可证明审计。"""

from __future__ import annotations

import hashlib
from typing import Any

from ticket_triage.audit import record_audit
from ticket_triage.runtime import purge_ticket_runtime
from ticket_triage.tools import tickets as tickets_mod


def export_ticket(ticket_id: str) -> dict[str, Any] | None:
    row = tickets_mod.get_ticket(ticket_id)
    if row is None:
        return None
    from ticket_triage.runtime import load_pending

    pending = load_pending(ticket_id)
    return {
        "ticket_id": ticket_id,
        "ticket": row,
        "pending_run": pending,
    }


def erase_ticket(ticket_id: str, *, actor: str) -> dict[str, Any]:
    """硬删 tickets + runtime 留存；审计留删除证明（含正文指纹，不含正文）。"""
    before = tickets_mod.get_ticket(ticket_id)
    body_fp = None
    subject_fp = None
    if before is not None:
        body = str(before.get("body") or "")
        subject = str(before.get("subject") or "")
        body_fp = hashlib.sha256(body.encode()).hexdigest()
        subject_fp = hashlib.sha256(subject.encode()).hexdigest()
    tickets_mod.delete_ticket(ticket_id)
    purged_runtime = purge_ticket_runtime(ticket_id)
    after = tickets_mod.get_ticket(ticket_id)
    proof = {
        "existed": before is not None,
        "subject_sha256": subject_fp,
        "body_sha256": body_fp,
        "runtime_rows_touched": purged_runtime,
        "ticket_gone": after is None,
    }
    audit_id = record_audit(
        actor=actor,
        action="privacy.erase",
        ticket_id=ticket_id,
        detail=proof,
    )
    return {"ticket_id": ticket_id, "deleted": after is None, "audit_id": audit_id, "proof": proof}


def assert_no_body_residue(ticket_id: str) -> list[str]:
    """验收用：SQLite / runtime 不应再出现该单正文。返回残留描述。"""
    from ticket_triage.runtime import load_pending, ticket_runtime_residue

    issues: list[str] = []
    if tickets_mod.get_ticket(ticket_id) is not None:
        issues.append("tickets_row_present")
    if load_pending(ticket_id) is not None:
        issues.append("pending_present")
    issues.extend(ticket_runtime_residue(ticket_id))
    return issues


def retention_status() -> dict[str, Any]:
    from ticket_triage.config import settings
    from ticket_triage.data_crypto import encryption_enabled

    return {
        "ticket_retention_days": settings.ticket_retention_days,
        "encryption_at_rest": encryption_enabled(),
        "purge_on_startup": True,
        "tls_in_transit": "terminate_at_gateway",
    }