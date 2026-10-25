"""同一数据目录只允许一个 API 进程持有写锁。"""

from __future__ import annotations

import os
from pathlib import Path


class ProcessLockError(RuntimeError):
    """另一个进程已经占用了这份运行数据。"""


_HELD: dict[str, object] = {}


def lock_path() -> Path:
    from ticket_triage.runtime import runtime_db_path

    return runtime_db_path().parent / "instance.lock"


def lock_file(path: Path):
    """锁住文件。同一进程里再锁一次也会失败，调用方自己做重入。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")  # noqa: SIM115 — 句柄必须一直打开，锁才不会被释放
    try:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        _lock_os(handle)
    except OSError as exc:
        handle.close()
        raise ProcessLockError(f"数据目录已有另一个进程: {path}") from exc
    return handle


def acquire_single_process_lock(path: Path | None = None) -> None:
    """本进程第一次拿到锁。已经持有同一路径时直接返回。"""
    target = (path or lock_path()).resolve()
    key = str(target)
    if key in _HELD:
        return
    _HELD[key] = lock_file(target)


def holds_lock(path: Path | None = None) -> bool:
    target = (path or lock_path()).resolve()
    return str(target) in _HELD


def _lock_os(handle) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
