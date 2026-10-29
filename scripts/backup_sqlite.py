#!/usr/bin/env python3
"""冷备份 tickets.db / runtime.db：先 WAL 收口，再拷贝；可选异地目录与保留份数。

冷备份约定：调用前应停掉唯一 API 进程（见 docs/BACKUP.md）。
不复制 instance.lock。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_NAMES = ("tickets.db", "runtime.db")
SKIP_NAMES = frozenset({"instance.lock"})


def wal_checkpoint(db_path: Path) -> None:
    """把 WAL 收进主文件（TRUNCATE）。库不存在则跳过。"""
    if not db_path.is_file():
        return
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def related_paths(db_path: Path) -> list[Path]:
    """主库及旁路 -wal / -shm（若存在）。"""
    paths = [db_path]
    for suffix in ("-wal", "-shm"):
        side = Path(str(db_path) + suffix)
        if side.is_file():
            paths.append(side)
    return paths


def copy_databases(data_dir: Path, dest_dir: Path) -> list[str]:
    """拷贝两个库（含仍存在的 wal/shm）。返回已拷相对名。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in DB_NAMES:
        src = data_dir / name
        if not src.is_file():
            continue
        for path in related_paths(src):
            if path.name in SKIP_NAMES:
                continue
            target = dest_dir / path.name
            shutil.copy2(path, target)
            copied.append(path.name)
    lock = data_dir / "instance.lock"
    if lock.exists():
        # 明确不拷；清单里记一笔便于验收
        pass
    if (dest_dir / "instance.lock").exists():
        raise RuntimeError("refusing to leave instance.lock in backup")
    return copied


def prune_old(backup_root: Path, keep: int) -> list[str]:
    """只保留最新 keep 个 backup-* 目录。"""
    if keep < 1:
        return []
    dirs = sorted(
        [p for p in backup_root.iterdir() if p.is_dir() and p.name.startswith("backup-")],
        key=lambda p: p.name,
        reverse=True,
    )
    removed: list[str] = []
    for stale in dirs[keep:]:
        shutil.rmtree(stale)
        removed.append(stale.name)
    return removed


def run_backup(
    *,
    data_dir: Path,
    backup_root: Path,
    offsite_root: Path | None,
    keep: int,
    stamp: str | None = None,
) -> dict:
    data_dir = data_dir.resolve()
    backup_root = backup_root.resolve()
    if not data_dir.is_dir():
        raise FileNotFoundError(f"data dir not found: {data_dir}")

    for name in DB_NAMES:
        wal_checkpoint(data_dir / name)

    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = backup_root / f"backup-{stamp}"
    if dest.exists():
        raise FileExistsError(f"backup already exists: {dest}")

    copied = copy_databases(data_dir, dest)
    if not copied:
        raise FileNotFoundError(f"no tickets.db/runtime.db under {data_dir}")

    offsite_dest = None
    if offsite_root is not None:
        offsite_root = offsite_root.resolve()
        offsite_dest = offsite_root / dest.name
        offsite_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(dest, offsite_dest)

    removed = prune_old(backup_root, keep)
    if offsite_root is not None:
        prune_old(offsite_root, keep)

    manifest = {
        "stamp": stamp,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data_dir": str(data_dir),
        "local": str(dest),
        "offsite": str(offsite_dest) if offsite_dest else None,
        "files": copied,
        "skipped": ["instance.lock"],
        "pruned": removed,
        "rpo_note": "RPO = time since this successful backup (default schedule: daily → RPO ≤ 24h)",
    }
    (dest / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if offsite_dest is not None:
        shutil.copy2(dest / "MANIFEST.json", offsite_dest / "MANIFEST.json")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cold-backup tickets.db and runtime.db")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(".data"),
        help="SQLite 数据目录（含 tickets.db / runtime.db）",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=Path(".data/backups"),
        help="本地备份根目录",
    )
    parser.add_argument(
        "--offsite",
        type=Path,
        default=None,
        help="异地/第二块盘备份根目录（可选）",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=14,
        help="每个备份根保留最近几份（默认 14 ≈ 两周日备）",
    )
    parser.add_argument(
        "--stamp",
        type=str,
        default=None,
        help="备份目录后缀（测试用）；默认 UTC 时间戳",
    )
    args = parser.parse_args(argv)
    try:
        manifest = run_backup(
            data_dir=args.data_dir,
            backup_root=args.dest,
            offsite_root=args.offsite,
            keep=args.keep,
            stamp=args.stamp,
        )
    except Exception as exc:  # noqa: BLE001 — CLI 出口
        print(f"backup_failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
