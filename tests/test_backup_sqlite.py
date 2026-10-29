"""P0.4：WAL 收口冷备份、不拷 lock、异地与保留份数。"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import backup_sqlite  # noqa: E402


def _make_db(path: Path, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO t (note) VALUES (?)", (marker,))
        conn.commit()
        # 留下一点 WAL 再让脚本 TRUNCATE
        conn.execute("INSERT INTO t (note) VALUES (?)", (marker + "-wal",))
        conn.commit()
    finally:
        conn.close()


def test_backup_checkpoints_copies_skips_lock_and_offsite(tmp_path: Path) -> None:
    data = tmp_path / "data"
    local_root = tmp_path / "backups"
    offsite_root = tmp_path / "offsite"
    _make_db(data / "tickets.db", "ticket-a")
    _make_db(data / "runtime.db", "runtime-a")
    (data / "instance.lock").write_text("pid=1", encoding="utf-8")

    manifest = backup_sqlite.run_backup(
        data_dir=data,
        backup_root=local_root,
        offsite_root=offsite_root,
        keep=5,
        stamp="test1",
    )
    dest = Path(manifest["local"])
    assert dest.is_dir()
    assert (dest / "tickets.db").is_file()
    assert (dest / "runtime.db").is_file()
    assert not (dest / "instance.lock").exists()
    assert "instance.lock" in manifest["skipped"]
    assert (dest / "MANIFEST.json").is_file()

    # WAL 应收口：备份目录通常不再需要旁路文件；主库可读出标记
    conn = sqlite3.connect(str(dest / "tickets.db"))
    try:
        notes = [r[0] for r in conn.execute("SELECT note FROM t ORDER BY id")]
    finally:
        conn.close()
    assert "ticket-a" in notes
    assert "ticket-a-wal" in notes

    offsite = Path(manifest["offsite"])
    assert offsite.is_dir()
    assert (offsite / "tickets.db").is_file()
    assert (offsite / "MANIFEST.json").is_file()


def test_prune_keeps_newest(tmp_path: Path) -> None:
    data = tmp_path / "data"
    local_root = tmp_path / "backups"
    _make_db(data / "tickets.db", "t")
    _make_db(data / "runtime.db", "r")
    for stamp in ("20260101-010000", "20260102-010000", "20260103-010000"):
        backup_sqlite.run_backup(
            data_dir=data,
            backup_root=local_root,
            offsite_root=None,
            keep=2,
            stamp=stamp,
        )
    names = sorted(p.name for p in local_root.iterdir() if p.is_dir())
    assert names == ["backup-20260102-010000", "backup-20260103-010000"]


def test_cli_fails_without_databases(tmp_path: Path) -> None:
    code = backup_sqlite.main(
        [
            "--data-dir",
            str(tmp_path / "empty"),
            "--dest",
            str(tmp_path / "out"),
            "--stamp",
            "x",
        ]
    )
    assert code == 1


def test_backup_doc_states_rpo_rto() -> None:
    text = (ROOT / "docs" / "BACKUP.md").read_text(encoding="utf-8")
    assert "RPO" in text and "24" in text
    assert "RTO" in text and "1" in text
    assert "季度" in text
    assert "P5_RUNBOOK" in text or "P5" in text
