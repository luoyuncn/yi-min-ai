"""把内部 runtime event 转成 AG-UI 事件。"""

import json
from uuid import uuid5, NAMESPACE_URL

from ag_ui.core import (
    CustomEvent as AGUICustomEvent,
    MessagesSnapshotEvent as AGUIMessagesSnapshotEvent,
    RunErrorEvent as AGUIRunErrorEvent,
    RunFinishedEvent as AGUIRunFinishedEvent,
    RunStartedEvent as AGUIRunStartedEvent,
    StepFinishedEvent as AGUIStepFinishedEvent,
    StepStartedEvent as AGUIStepStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent as AGUIToolCallArgsEvent,
    ToolCallEndEvent as AGUIToolCallEndEvent,
    ToolCallResultEvent as AGUIToolCallResultEvent,
    ToolCallStartEvent as AGUIToolCallStartEvent,
)

from agent.web import events as runtime_events


def runtime_event_to_ag_ui(event):
    """把内部 runtime event 映射成官方 AG-UI 事件对象。"""

    if isinstance(event, runtime_events.RunStartedEvent):
        return AGUIRunStartedEvent(threadId=event.thread_id, runId=event.run_id)
    if isinstance(event, runtime_events.MessagesSnapshotEvent):
        completed_tool_call_ids = {
            message.get("tool_call_id")
            for message in event.messages
            if message.get("role") == "tool" and message.get("tool_call_id")
        }
        return AGUIMessagesSnapshotEvent(
            messages=[
                _to_ag_ui_message(
                    message,
                    index=index,
                    completed_tool_call_ids=completed_tool_call_ids,
                )
                for index, message in enumerate(event.messages)
            ]
        )
    if isinstance(event, runtime_events.StepStartedEvent):
        return AGUIStepStartedEvent(stepName=event.step_name)
    if isinstance(event, runtime_events.StepFinishedEvent):
        return AGUIStepFinishedEvent(stepName=event.step_name)
    if isinstance(event, runtime_events.AssistantTextStartEvent):
        return TextMessageStartEvent(messageId=event.message_id, role="assistant")
    if isinstance(event, runtime_events.AssistantTextDeltaEvent):
        return TextMessageContentEvent(messageId=event.message_id, delta=event.delta)
    if isinstance(event, runtime_events.AssistantTextEndEvent):
        return TextMessageEndEvent(messageId=event.message_id)
    if isinstance(event, runtime_events.ToolCallStartEvent):
        return AGUIToolCallStartEvent(
            toolCallId=event.tool_call_id,
            toolCallName=event.tool_call_name,
            parentMessageId=event.parent_message_id,
        )
    if isinstance(event, runtime_events.ToolCallArgsEvent):
        return AGUIToolCallArgsEvent(toolCallId=event.tool_call_id, delta=event.delta)
    if isinstance(event, runtime_events.ToolCallResultEvent):
        return AGUIToolCallResultEvent(
            messageId=event.message_id,
            toolCallId=event.tool_call_id,
            content=event.content,
            role="tool",
        )
    if isinstance(event, runtime_events.ToolCallEndEvent):
        return AGUIToolCallEndEvent(toolCallId=event.tool_call_id)
    if isinstance(event, runtime_events.RunFinishedEvent):
        return AGUIRunFinishedEvent(
            threadId=event.thread_id,
            runId=event.run_id,
            result={"text": event.result_text},
        )
    if isinstance(event, runtime_events.RunErrorEvent):
        return AGUIRunErrorEvent(message=event.message, code=event.code)
    if isinstance(event, runtime_events.CustomEvent):
        return AGUICustomEvent(name=event.name, value=event.value)
    raise TypeError(f"Unsupported runtime event: {type(event).__name__}")


def _to_ag_ui_message(
    message: dict,
    *,
    index: int,
    completed_tool_call_ids: set[str],
) -> dict:
    role = message["role"]
    message_id = _message_id(message, role=role, index=index)
    if role == "assistant":
        tool_calls = [
            {
                "id": tool_call.get("id") or _tool_call_id(tool_call, index=index, role=role),
                "type": "function",
                "function": {
                    "name": tool_call["name"],
                    "arguments": json.dumps(tool_call["input"], ensure_ascii=False),
                },
            }
            for tool_call in message.get("tool_calls", [])
            if (tool_call.get("id") or _tool_call_id(tool_call, index=index, role=role))
            in completed_tool_call_ids
        ]
        return {
            "id": message_id,
            "role": role,
            "content": message.get("content", ""),
            "tool_calls": tool_calls or None,
        }
    if role == "tool":
        return {
            "id": message_id,
            "role": role,
            "content": message.get("content", ""),
            "tool_call_id": message.get("tool_call_id") or _tool_result_id(message, index=index),
        }
    return {
        "id": message_id,
        "role": role,
        "content": message.get("content", ""),
    }


def _message_id(message: dict, *, role: str, index: int) -> str:
    message_id = message.get("id")
    if isinstance(message_id, str) and message_id:
        return message_id
    return _stable_fallback_id(f"message:{role}:{index}:{message.get('content', '')}")


def _tool_call_id(tool_call: dict, *, index: int, role: str) -> str:
    return _stable_fallback_id(
        f"tool-call:{role}:{index}:{tool_call.get('name', '')}:{json.dumps(tool_call.get('input', {}), ensure_ascii=False, sort_keys=True)}"
    )


def _tool_result_id(message: dict, *, index: int) -> str:
    return _stable_fallback_id(f"tool-result:{index}:{message.get('content', '')}")


def _stable_fallback_id(seed: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"yi-min-ai:{seed}"))
