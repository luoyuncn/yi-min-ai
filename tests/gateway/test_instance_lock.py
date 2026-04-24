"""Gateway 单实例锁测试。"""

from pathlib import Path

import pytest

from agent.gateway.instance_lock import (
    InstanceLockError,
    _is_pid_running_windows,
    acquire_instance_lock,
)


def test_acquire_instance_lock_creates_and_releases_lock_file(tmp_path: Path) -> None:
    """成功获取锁后应创建锁文件，并在释放时删除。"""

    lock_path = tmp_path / "gateway.lock"

    handle = acquire_instance_lock(lock_path)

    assert lock_path.exists()
    assert str(handle.pid) in lock_path.read_text(encoding="utf-8")

    handle.release()

    assert not lock_path.exists()


def test_acquire_instance_lock_rejects_active_process_lock(tmp_path: Path, monkeypatch) -> None:
    """如果锁文件对应的进程仍存活，应拒绝重复启动。"""

    lock_path = tmp_path / "gateway.lock"
    lock_path.write_text('{"pid": 12345}', encoding="utf-8")
    monkeypatch.setattr("agent.gateway.instance_lock._is_pid_running", lambda pid: True)

    with pytest.raises(InstanceLockError, match="already running"):
        acquire_instance_lock(lock_path)


def test_acquire_instance_lock_reclaims_stale_lock_file(tmp_path: Path, monkeypatch) -> None:
    """如果锁文件对应进程已不存在，应自动回收陈旧锁。"""

    lock_path = tmp_path / "gateway.lock"
    lock_path.write_text('{"pid": 12345}', encoding="utf-8")
    monkeypatch.setattr("agent.gateway.instance_lock._is_pid_running", lambda pid: False)

    handle = acquire_instance_lock(lock_path)

    assert lock_path.exists()
    assert str(handle.pid) in lock_path.read_text(encoding="utf-8")

    handle.release()


def test_is_pid_running_windows_returns_true_when_tasklist_finds_pid(monkeypatch) -> None:
    """Windows 下应通过 tasklist 识别仍然存活的 PID。"""

    class Result:
        returncode = 0
        stdout = '"python.exe","1234","Console","1","12,000 K"\n'

    monkeypatch.setattr("agent.gateway.instance_lock.subprocess.run", lambda *args, **kwargs: Result())

    assert _is_pid_running_windows(1234) is True


def test_is_pid_running_windows_returns_false_when_tasklist_reports_missing_pid(monkeypatch) -> None:
    """Windows 下如果 tasklist 没找到 PID，应判定为未运行。"""

    class Result:
        returncode = 0
        stdout = "INFO: No tasks are running which match the specified criteria.\n"

    monkeypatch.setattr("agent.gateway.instance_lock.subprocess.run", lambda *args, **kwargs: Result())

    assert _is_pid_running_windows(1234) is False
