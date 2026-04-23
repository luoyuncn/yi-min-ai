"""Agent runtime 到 Web/协议层之间的内部事件模型。"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AgentRuntimeEvent:
    """内部 runtime event 基类。"""

    kind: str = field(init=False)


@dataclass(slots=True)
class RunStartedEvent(AgentRuntimeEvent):
    thread_id: str
    run_id: str
    kind: str = field(init=False, default="run_started")


@dataclass(slots=True)
class MessagesSnapshotEvent(AgentRuntimeEvent):
    messages: list[dict[str, Any]]
    kind: str = field(init=False, default="messages_snapshot")


@dataclass(slots=True)
class StepStartedEvent(AgentRuntimeEvent):
    step_name: str
    kind: str = field(init=False, default="step_started")


@dataclass(slots=True)
class StepFinishedEvent(AgentRuntimeEvent):
    step_name: str
    kind: str = field(init=False, default="step_finished")


@dataclass(slots=True)
class AssistantTextStartEvent(AgentRuntimeEvent):
    message_id: str
    kind: str = field(init=False, default="assistant_text_started")


@dataclass(slots=True)
class AssistantTextDeltaEvent(AgentRuntimeEvent):
    message_id: str
    delta: str
    kind: str = field(init=False, default="assistant_text_delta")


@dataclass(slots=True)
class AssistantTextEndEvent(AgentRuntimeEvent):
    message_id: str
    kind: str = field(init=False, default="assistant_text_ended")


@dataclass(slots=True)
class ToolCallStartEvent(AgentRuntimeEvent):
    tool_call_id: str
    tool_call_name: str
    parent_message_id: str | None = None
    kind: str = field(init=False, default="tool_call_started")


@dataclass(slots=True)
class ToolCallArgsEvent(AgentRuntimeEvent):
    tool_call_id: str
    delta: str
    kind: str = field(init=False, default="tool_call_args")


@dataclass(slots=True)
class ToolCallResultEvent(AgentRuntimeEvent):
    message_id: str
    tool_call_id: str
    content: str
    kind: str = field(init=False, default="tool_call_result")


@dataclass(slots=True)
class ToolCallEndEvent(AgentRuntimeEvent):
    tool_call_id: str
    kind: str = field(init=False, default="tool_call_ended")


@dataclass(slots=True)
class CustomEvent(AgentRuntimeEvent):
    name: str
    value: Any
    kind: str = field(init=False, default="custom")


@dataclass(slots=True)
class RunFinishedEvent(AgentRuntimeEvent):
    thread_id: str
    run_id: str
    result_text: str
    kind: str = field(init=False, default="run_finished")


@dataclass(slots=True)
class RunErrorEvent(AgentRuntimeEvent):
    message: str
    code: str | None = None
    kind: str = field(init=False, default="run_error")
