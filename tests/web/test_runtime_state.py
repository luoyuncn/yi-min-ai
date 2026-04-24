"""Web runtime state 测试。"""

from agent.web.runtime_state import PendingApprovalStore, RunControlRegistry


def test_run_control_registry_tracks_active_runs() -> None:
    """中断控制器注册表应能跟踪和清理活跃 run。"""

    registry = RunControlRegistry()

    control = registry.start(thread_id="thread-1", run_id="run-1")

    assert registry.get("run-1") is control
    assert not control.is_interrupted

    control.interrupt()

    assert control.is_interrupted

    registry.finish("run-1")

    assert registry.get("run-1") is None


def test_pending_approval_store_keeps_latest_thread_interrupt() -> None:
    """同一个线程的待审批状态应可按线程取回，并在 resolve 后移除。"""

    store = PendingApprovalStore()

    approval = store.create(
        thread_id="thread-1",
        run_id="run-1",
        tool_call={"id": "tool-1", "name": "file_write", "input": {"path": "notes.txt", "content": "hello"}},
        context=[{"role": "user", "content": "请写文件"}],
        message="审批 file_write",
    )

    assert store.get(approval.approval_id) is approval
    assert store.get_by_thread("thread-1") is approval

    store.resolve(approval.approval_id)

    assert store.get(approval.approval_id) is None
    assert store.get_by_thread("thread-1") is None


def test_pending_approval_store_supports_runtime_thread_aliases() -> None:
    """待审批状态应同时支持内部 thread key 和外部线程别名取回。"""

    store = PendingApprovalStore()

    approval = store.create(
        thread_id="web:default:thread-1",
        run_id="run-1",
        tool_call={"id": "tool-1", "name": "file_write", "input": {"path": "notes.txt", "content": "hello"}},
        context=[{"role": "user", "content": "请写文件"}],
        message="审批 file_write",
        aliases=["thread-1"],
    )

    assert store.get_by_thread("web:default:thread-1") is approval
    assert store.get_by_thread("thread-1") is approval

    store.resolve(approval.approval_id)

    assert store.get_by_thread("web:default:thread-1") is None
    assert store.get_by_thread("thread-1") is None
