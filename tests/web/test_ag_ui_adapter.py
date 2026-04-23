"""AG-UI 适配测试。"""

from agent.web.ag_ui_adapter import runtime_event_to_ag_ui
from agent.web.events import AssistantTextDeltaEvent, CustomEvent, MessagesSnapshotEvent, RunStartedEvent


def test_runtime_event_maps_to_ag_ui_payload() -> None:
    """内部 runtime event 应被映射成 AG-UI 事件对象。"""

    payload = runtime_event_to_ag_ui(RunStartedEvent(thread_id="thread-1", run_id="run-1"))
    delta = runtime_event_to_ag_ui(AssistantTextDeltaEvent(message_id="msg-1", delta="hello"))

    assert payload.type.value == "RUN_STARTED"
    assert payload.thread_id == "thread-1"
    assert delta.type.value == "TEXT_MESSAGE_CONTENT"
    assert delta.delta == "hello"


def test_runtime_event_maps_snapshot_and_custom_events() -> None:
    """线程重放和 interrupt 需要 snapshot/custom 事件映射。"""

    snapshot = runtime_event_to_ag_ui(
        MessagesSnapshotEvent(messages=[{"id": "user-1", "role": "user", "content": "你好"}])
    )
    custom = runtime_event_to_ag_ui(
        CustomEvent(name="on_interrupt", value={"approval_id": "approval-1"})
    )

    assert snapshot.type.value == "MESSAGES_SNAPSHOT"
    assert snapshot.messages[0].role == "user"
    assert custom.type.value == "CUSTOM"
    assert custom.name == "on_interrupt"


def test_runtime_event_maps_legacy_snapshot_messages_without_ids() -> None:
    """老归档里缺少 message id 时，snapshot 仍应可被前端恢复。"""

    snapshot = runtime_event_to_ag_ui(
        MessagesSnapshotEvent(
            messages=[
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "欢迎回来"},
            ]
        )
    )

    assert snapshot.type.value == "MESSAGES_SNAPSHOT"
    assert snapshot.messages[0].id
    assert snapshot.messages[1].id
