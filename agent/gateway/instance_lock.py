"""Gateway 进程单实例锁。"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class InstanceLockError(RuntimeError):
    """重复启动 Gateway 时抛出的错误。"""


@dataclass(slots=True)
class InstanceLockHandle:
    """已获取的锁句柄。"""

    path: Path
    pid: int

    def release(self) -> None:
        """释放锁文件。"""

        if self.path.exists():
            self.path.unlink()


def acquire_instance_lock(lock_path: Path) -> InstanceLockHandle:
    """获取单实例锁。

    如果锁文件已存在且对应进程仍存活，则拒绝重复启动。
    如果锁文件陈旧，则自动回收后重新获取。
    """

    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()

    try:
        return _create_lock(path, pid)
    except FileExistsError:
        existing_pid = _read_pid(path)
        if existing_pid is not None and _is_pid_running(existing_pid):
            raise InstanceLockError(
                f"Another gateway instance is already running with pid={existing_pid}. "
                f"Lock file: {path}"
            )
        path.unlink(missing_ok=True)
        return _create_lock(path, pid)


def _create_lock(path: Path, pid: int) -> InstanceLockHandle:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        payload = json.dumps({"pid": pid}, ensure_ascii=True).encode("utf-8")
        os.write(fd, payload)
    finally:
        os.close(fd)
    return InstanceLockHandle(path=path, pid=pid)


def _read_pid(path: Path) -> int | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    pid = data.get("pid")
    if isinstance(pid, int) and pid > 0:
        return pid
    return None


def _is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False

    if os.name == "nt":
        return _is_pid_running_windows(pid)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _is_pid_running_windows(pid: int) -> bool:
    """在 Windows 上通过 tasklist 判断 PID 是否仍然存活。"""

    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="ignore",
        )
    except Exception:
        return False

    if result.returncode != 0:
        return False

    stdout = (result.stdout or "").strip()
    if not stdout or "No tasks are running" in stdout:
        return False

    return f'"{pid}"' in stdout or f",{pid}," in stdout
