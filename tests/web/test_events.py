"""Web runtime event 测试。"""

from agent.web.events import AssistantTextDeltaEvent, RunStartedEvent


def test_runtime_events_capture_basic_metadata() -> None:
    """runtime event 应保留最基本的线程和增量文本信息。"""

    event = RunStartedEvent(thread_id="thread-1", run_id="run-1")
    delta = AssistantTextDeltaEvent(message_id="msg-1", delta="hello")

    assert event.thread_id == "thread-1"
    assert event.kind == "run_started"
    assert delta.delta == "hello"
    assert delta.kind == "assistant_text_delta"
