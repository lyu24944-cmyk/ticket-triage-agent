"""单进程文件锁：第二个句柄拿不到同一份数据目录。"""

import pytest

from ticket_triage.process_lock import ProcessLockError, lock_file


def test_second_handle_cannot_lock_the_same_file(tmp_path) -> None:
    path = tmp_path / "other" / "instance.lock"
    first = lock_file(path)
    try:
        with pytest.raises(ProcessLockError):
            lock_file(path)
    finally:
        first.close()
