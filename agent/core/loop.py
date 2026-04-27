"""一期 ReAct 核心循环。

阅读这个文件时，可以把它理解成整个阶段一的“调度中心”：
1. 取会话
2. 拼上下文
3. 调模型
4. 如果模型要用工具，就执行工具再继续
5. 如果模型直接回复，就归档并返回
"""

import asyncio
import json
import logging
from pathlib import Path
from uuid import uuid4

from agent.core.compaction import CompactionEngine
from agent.core.context import ContextAssembler
from agent.core.provider import LLMRequest, LLMResponse, LLMStreamChunk
from agent.memory import AlwaysOnMemory, LedgerStore, MemoryExtractor, MemoryStore, NoteStore, SessionArchive, TurnData
from agent.observability.react_log import ReactTraceLogger
from agent.observability.tracing import elapsed_ms, ensure_trace_id, monotonic_now, text_preview, trace_fields
from agent.session import SessionManager
from agent.skills import SkillLoader
from agent.tools import ToolExecutor, build_stage1_registry
from agent.tools.runtime_context import RuntimeServices, RuntimeToolContext
from agent.web.events import (
    AssistantTextDeltaEvent,
    AssistantTextEndEvent,
    AssistantTextStartEvent,
    CustomEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StepFinishedEvent,
    StepStartedEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from agent.web.runtime_state import PendingApprovalStore, RunControl, RunInterrupted

logger = logging.getLogger(__name__)


class AgentCore:
    """把各个子系统串起来的核心运行器。"""

    def __init__(
        self,
        *,
        workspace_dir: Path,
        provider_manager,
        always_on_memory: AlwaysOnMemory,
        session_archive: SessionArchive,
        session_manager: SessionManager,
        skill_loader: SkillLoader,
        ledger_store: LedgerStore | None = None,
        note_store: NoteStore | None = None,
        memory_store: MemoryStore | None = None,
        memory_extractor: MemoryExtractor | None = None,
        react_logger: ReactTraceLogger | None = None,
        mflow_bridge=None,
        runtime_services: RuntimeServices | None = None,
        enable_shell: bool = False,
        shell_requires_confirmation: bool = True,
        max_iterations: int = 8,
        context_history_turns: int = 12,
        system_prompt: str = "You are Yi Min.",
    ) -> None:
        # 这些依赖都在 build_app 或测试工厂中注入；
        # AgentCore 自己不负责创建它们，只负责调度它们协作。
        self.workspace_dir = Path(workspace_dir)
        self.provider_manager = provider_manager
        self.always_on_memory = always_on_memory
        self.session_archive = session_archive
        self.session_manager = session_manager
        self.skill_loader = skill_loader
        self.ledger_store = ledger_store
        self.mflow_bridge = mflow_bridge
        self.runtime_services = runtime_services or RuntimeServices()
        self.shell_requires_confirmation = shell_requires_confirmation
        self.max_iterations = max_iterations
        self.context_history_turns = context_history_turns
        self.note_store = note_store
        self.memory_store = memory_store
        self.memory_extractor = memory_extractor
        self.react_logger = react_logger or ReactTraceLogger(self.workspace_dir / "logs" / "react.log")
        self.context_assembler = ContextAssembler(system_prompt=system_prompt)
        self.compaction_engine = CompactionEngine(
            provider_manager=provider_manager,
            session_archive=session_archive,
        )
        self.tool_registry = build_stage1_registry(
            workspace_dir=self.workspace_dir,
            always_on_memory=self.always_on_memory,
            session_archive=self.session_archive,
            skill_loader=self.skill_loader,
            mflow_bridge=self.mflow_bridge,
            ledger_store=self.ledger_store,
            note_store=self.note_store,
            memory_store=self.memory_store,
            runtime_services=self.runtime_services,
            enable_shell=enable_shell,
        )
        self.tool_executor = ToolExecutor(self.tool_registry)

    async def run(self, message) -> str:
        """处理一条标准化消息。"""

        final_text = ""
        async for event in self.run_events(message):
            if isinstance(event, RunErrorEvent):
                raise RuntimeError(event.message)
            if isinstance(event, RunFinishedEvent):
                final_text = event.result_text
        return final_text

    async def run_events(
        self,
        message,
        *,
        runtime_control: RunControl | None = None,
        approval_store: PendingApprovalStore | None = None,
    ):
        """处理一条消息，并以事件流形式暴露执行轨迹。"""

        thread_id = message.thread_key
        thread_aliases = [message.session_id] if message.session_id != thread_id else []
        run_id = message.metadata.get("run_id") or message.message_id
        metadata = message.metadata
        ensure_trace_id(metadata, fallback_id=message.message_id)
        run_started_at = monotonic_now()
        metadata["run_started_at"] = run_started_at
        metadata["timing_model_ms_total"] = 0
        metadata["timing_tool_exec_ms_total"] = 0
        metadata["timing_tool_roundtrip_ms_total"] = 0
        metadata["timing_tool_call_count"] = 0
        metadata["timing_first_token_model_ms"] = None
        metadata["timing_first_token_run_ms"] = None
        metadata["timing_iterations"] = 0
        metadata["run_finished_at"] = None

        logger.info(
            f"{trace_fields(metadata, session_id=thread_id, channel=message.channel, run_id=run_id)} "
            f"event=run_started sender={message.sender} body_chars={len(message.body)} preview={text_preview(message.body)}"
        )
        yield RunStartedEvent(thread_id=thread_id, run_id=run_id)

        try:
            session = await self.session_manager.get_or_create(thread_id, channel=message.channel)
            logger.info(
                f"{trace_fields(metadata, session_id=thread_id, channel=message.channel, run_id=run_id)} "
                f"event=session_loaded history_messages={len(session.history)}"
            )
            command = message.metadata.get("command") or {}
            if command:
                async for event in self._resume_from_command(
                    session,
                    thread_id=thread_id,
                    run_id=run_id,
                    command=command,
                    message_metadata=metadata,
                    channel=message.channel,
                    runtime_control=runtime_control,
                    approval_store=approval_store,
                    channel_instance=message.channel_instance,
                    session_id=message.session_id,
                    sender=message.sender,
                ):
                    yield event
                return

            selected_history = self._select_history_for_context(session.history)
            context = self.context_assembler.assemble(
                soul_text=self.always_on_memory.load_soul(),
                memory_text=self.always_on_memory.load_profile(),
                memory_items_text=self._build_memory_items_text(message.body),
                tool_index=self.tool_registry.get_index(),
                skill_index=self.skill_loader.get_index(),
                history=selected_history,
                user_message=message.body,
                channel=message.channel,
                channel_instance=message.channel_instance,
                sender=message.sender,
                metadata=message.metadata,
            )
            logger.info(
                f"{trace_fields(metadata, session_id=thread_id, channel=message.channel, run_id=run_id)} "
                f"event=context_assembled context_messages={len(context)} "
                f"history_messages_used={len(selected_history)} "
                f"context_tokens={self.context_assembler.count_context_tokens(context)} "
                f"tool_count={len(self.tool_registry.get_schemas())}"
            )
            # 当前用户消息既要进入本次模型上下文，也要落进会话历史。
            session.append({"id": message.message_id, "role": "user", "content": message.body})

            async for event in self._run_loop(
                session,
                context,
                thread_id=thread_id,
                run_id=run_id,
                message_metadata=metadata,
                channel=message.channel,
                channel_instance=message.channel_instance,
                session_id=message.session_id,
                sender=message.sender,
                runtime_control=runtime_control,
                approval_store=approval_store,
                thread_aliases=thread_aliases,
                user_message=message.body,
                source_message_id=message.message_id,
                sender_id=message.sender,
            ):
                yield event
        except Exception as exc:
            code = "RunInterrupted" if isinstance(exc, RunInterrupted) else type(exc).__name__
            logger.error(
                f"{trace_fields(metadata, session_id=thread_id, channel=message.channel, run_id=run_id)} "
                f"event=run_failed total_ms={elapsed_ms(run_started_at)} code={code} error={exc}",
                exc_info=True,
            )
            yield RunErrorEvent(message=str(exc), code=code)

    def run_sync(self, message) -> str:
        """给同步调用方（例如 CLI）提供一个方便入口。"""

        return asyncio.run(self.run(message))

    async def _run_loop(
        self,
        session,
        context: list[dict],
        *,
        thread_id: str,
        run_id: str,
        message_metadata: dict,
        channel: str,
        channel_instance: str,
        session_id: str,
        sender: str | None,
        runtime_control: RunControl | None,
        approval_store: PendingApprovalStore | None,
        thread_aliases: list[str],
        user_message: str,
        source_message_id: str,
        sender_id: str | None,
    ):
        for index in range(self.max_iterations):
            step_name = f"iteration-{index + 1}"
            message_metadata["timing_iterations"] = index + 1
            self._ensure_active(runtime_control)
            step_started_at = monotonic_now()
            yield StepStartedEvent(step_name=step_name)
            logger.info(
                f"{trace_fields(message_metadata, session_id=thread_id, channel=channel, run_id=run_id)} "
                f"event=step_started step_name={step_name}"
            )

            # Pre-flight: 检查是否需要压缩
            token_count = self.context_assembler.count_context_tokens(context)
            if self.compaction_engine.should_compact(context, token_count):
                logger.info(f"Compacting context: {token_count} tokens")
                context = await self.compaction_engine.compact(context)
                token_count = self.context_assembler.count_context_tokens(context)
                logger.info(f"After compaction: {token_count} tokens")

            request = LLMRequest(messages=context, tools=self.tool_registry.get_schemas())
            assistant_message_id = str(uuid4())
            streamed_text_parts: list[str] = []
            response: LLMResponse | None = None
            emitted_stream_start = False
            model_started_at = monotonic_now()
            logger.info(
                f"{trace_fields(message_metadata, session_id=thread_id, channel=channel, run_id=run_id)} "
                f"event=model_request_started step_name={step_name} context_messages={len(context)} "
                f"tool_count={len(request.tools)}"
            )
            self.react_logger.record(
                "model_request",
                trace_id=message_metadata.get("trace_id"),
                thread_id=thread_id,
                run_id=run_id,
                step_name=step_name,
                context_messages=len(context),
                tool_count=len(request.tools),
            )

            async for chunk in self._iter_provider_stream(request):
                self._ensure_active(runtime_control)

                if chunk.type == "text_delta":
                    delta = chunk.delta or ""
                    if not delta:
                        continue
                    if not emitted_stream_start:
                        emitted_stream_start = True
                        first_token_model_ms = elapsed_ms(model_started_at)
                        if message_metadata.get("timing_first_token_model_ms") is None:
                            message_metadata["timing_first_token_model_ms"] = first_token_model_ms
                        if message_metadata.get("timing_first_token_run_ms") is None:
                            message_metadata["timing_first_token_run_ms"] = elapsed_ms(
                                message_metadata.get("run_started_at")
                            )
                        logger.info(
                            f"{trace_fields(message_metadata, session_id=thread_id, channel=channel, run_id=run_id)} "
                            f"event=model_first_token step_name={step_name} after_ms={first_token_model_ms}"
                        )
                        yield AssistantTextStartEvent(message_id=assistant_message_id)
                    streamed_text_parts.append(delta)
                    yield AssistantTextDeltaEvent(message_id=assistant_message_id, delta=delta)
                    continue

                if chunk.type == "response" and chunk.response is not None:
                    response = chunk.response

            if response is None:
                raise RuntimeError("Provider stream ended without a final response")

            streamed_text = "".join(streamed_text_parts)
            response = self._coerce_response(response)
            finalized_text = self._finalize_streamed_text(streamed_text, response.text)
            if finalized_text != response.text:
                response = self._coerce_response(response, text=finalized_text)
            model_ms = elapsed_ms(model_started_at)
            message_metadata["timing_model_ms_total"] += model_ms
            usage = response.usage or {}
            logger.info(
                f"{trace_fields(message_metadata, session_id=thread_id, channel=channel, run_id=run_id)} "
                f"event=model_response_completed step_name={step_name} after_ms={model_ms} "
                f"response_type={response.type} provider={response.provider or 'unknown'} "
                f"model={response.model or 'unknown'} output_chars={len((response.text or ''))} "
                f"tool_call_count={len(response.tool_calls or [])} streamed_chars={len(streamed_text)} "
                f"input_tokens={usage.get('input_tokens', -1)} output_tokens={usage.get('output_tokens', -1)}"
            )
            self.react_logger.record(
                "model_response",
                trace_id=message_metadata.get("trace_id"),
                thread_id=thread_id,
                run_id=run_id,
                step_name=step_name,
                response_type=response.type,
                provider=response.provider or "unknown",
                model=response.model or "unknown",
                text=response.text or "",
                tool_calls=response.tool_calls or [],
                usage=usage,
            )
            self._ensure_active(runtime_control)
            if response.type == "text":
                self.react_logger.record(
                    "decision",
                    trace_id=message_metadata.get("trace_id"),
                    thread_id=thread_id,
                    run_id=run_id,
                    step_name=step_name,
                    decision="final_answer",
                )
                assistant_text = response.text or streamed_text
                if emitted_stream_start:
                    tail_delta = self._stream_tail(streamed_text, assistant_text)
                    if tail_delta:
                        yield AssistantTextDeltaEvent(message_id=assistant_message_id, delta=tail_delta)
                    yield AssistantTextEndEvent(message_id=assistant_message_id)
                elif assistant_text:
                    if message_metadata.get("timing_first_token_model_ms") is None:
                        message_metadata["timing_first_token_model_ms"] = model_ms
                    if message_metadata.get("timing_first_token_run_ms") is None:
                        message_metadata["timing_first_token_run_ms"] = elapsed_ms(
                            message_metadata.get("run_started_at")
                        )
                    yield AssistantTextStartEvent(message_id=assistant_message_id)
                    yield AssistantTextDeltaEvent(message_id=assistant_message_id, delta=assistant_text)
                    yield AssistantTextEndEvent(message_id=assistant_message_id)
                session.append({"id": assistant_message_id, "role": "assistant", "content": assistant_text})
                self.session_archive.persist_session(session)
                self._extract_memories(
                    user_message=user_message,
                    assistant_text=assistant_text,
                    thread_id=thread_id,
                    source_message_id=source_message_id,
                    sender_id=sender_id,
                )

                # 异步写入 M-flow（非阻塞）
                await self._ingest_to_mflow(session, thread_id)

                yield StepFinishedEvent(step_name=step_name)
                logger.info(
                    f"{trace_fields(message_metadata, session_id=thread_id, channel=channel, run_id=run_id)} "
                    f"event=step_finished step_name={step_name} step_ms={elapsed_ms(step_started_at)}"
                )
                self._log_run_timing_summary(
                    thread_id=thread_id,
                    run_id=run_id,
                    channel=channel,
                    message_metadata=message_metadata,
                )
                message_metadata["run_finished_at"] = monotonic_now()
                logger.info(
                    f"{trace_fields(message_metadata, session_id=thread_id, channel=channel, run_id=run_id)} "
                    f"event=run_finished total_ms={elapsed_ms(message_metadata.get('run_started_at'))} "
                    f"result_chars={len(assistant_text or '')}"
                )
                yield RunFinishedEvent(thread_id=thread_id, run_id=run_id, result_text=assistant_text)
                return

            if response.text:
                if emitted_stream_start:
                    tail_delta = self._stream_tail(streamed_text, response.text)
                    if tail_delta:
                        yield AssistantTextDeltaEvent(message_id=assistant_message_id, delta=tail_delta)
                    yield AssistantTextEndEvent(message_id=assistant_message_id)
                else:
                    yield AssistantTextStartEvent(message_id=assistant_message_id)
                    yield AssistantTextDeltaEvent(message_id=assistant_message_id, delta=response.text)
                    yield AssistantTextEndEvent(message_id=assistant_message_id)

            assistant_message = {
                "id": assistant_message_id,
                "role": "assistant",
                "content": response.text or "",
                "tool_calls": response.tool_calls or [],
            }
            session.append(assistant_message)
            context.append(assistant_message)
            self.react_logger.record(
                "decision",
                trace_id=message_metadata.get("trace_id"),
                thread_id=thread_id,
                run_id=run_id,
                step_name=step_name,
                decision="execute_tools",
                tool_call_count=len(response.tool_calls or []),
            )

            interrupted = False
            async for event in self._execute_tool_calls(
                session,
                context,
                tool_calls=response.tool_calls or [],
                parent_message_id=assistant_message_id,
                thread_id=thread_id,
                run_id=run_id,
                step_name=step_name,
                message_metadata=message_metadata,
                channel=channel,
                approval_store=approval_store,
                thread_aliases=thread_aliases,
                channel_instance=channel_instance,
                session_id=session_id,
                sender=sender,
            ):
                if isinstance(event, CustomEvent):
                    interrupted = True
                if isinstance(event, RunErrorEvent):
                    yield event
                    return
                yield event

            direct_text = "" if interrupted else self._build_direct_tool_response(response.tool_calls or [], context)
            if direct_text:
                direct_message_id = str(uuid4())
                if message_metadata.get("timing_first_token_run_ms") is None:
                    message_metadata["timing_first_token_run_ms"] = elapsed_ms(
                        message_metadata.get("run_started_at")
                    )
                yield AssistantTextStartEvent(message_id=direct_message_id)
                yield AssistantTextDeltaEvent(message_id=direct_message_id, delta=direct_text)
                yield AssistantTextEndEvent(message_id=direct_message_id)
                session.append({"id": direct_message_id, "role": "assistant", "content": direct_text})
                self.session_archive.persist_session(session)
                self._extract_memories(
                    user_message=user_message,
                    assistant_text=direct_text,
                    thread_id=thread_id,
                    source_message_id=source_message_id,
                    sender_id=sender_id,
                )
                await self._ingest_to_mflow(session, thread_id)

            yield StepFinishedEvent(step_name=step_name)
            logger.info(
                f"{trace_fields(message_metadata, session_id=thread_id, channel=channel, run_id=run_id)} "
                f"event=step_finished step_name={step_name} step_ms={elapsed_ms(step_started_at)}"
            )
            if direct_text:
                self.react_logger.record(
                    "decision",
                    trace_id=message_metadata.get("trace_id"),
                    thread_id=thread_id,
                    run_id=run_id,
                    step_name=step_name,
                    decision="direct_tool_response",
                )
                self._log_run_timing_summary(
                    thread_id=thread_id,
                    run_id=run_id,
                    channel=channel,
                    message_metadata=message_metadata,
                )
                message_metadata["run_finished_at"] = monotonic_now()
                logger.info(
                    f"{trace_fields(message_metadata, session_id=thread_id, channel=channel, run_id=run_id)} "
                    f"event=run_finished total_ms={elapsed_ms(message_metadata.get('run_started_at'))} "
                    f"result_chars={len(direct_text)} direct_tool_response=True"
                )
                yield RunFinishedEvent(thread_id=thread_id, run_id=run_id, result_text=direct_text)
                return
            if interrupted:
                self._log_run_timing_summary(
                    thread_id=thread_id,
                    run_id=run_id,
                    channel=channel,
                    message_metadata=message_metadata,
                )
                message_metadata["run_finished_at"] = monotonic_now()
                logger.info(
                    f"{trace_fields(message_metadata, session_id=thread_id, channel=channel, run_id=run_id)} "
                    f"event=run_finished total_ms={elapsed_ms(message_metadata.get('run_started_at'))} result_chars=0"
                )
                yield RunFinishedEvent(thread_id=thread_id, run_id=run_id, result_text="")
                return

        yield RunErrorEvent(message="max iterations exceeded")

    async def _iter_provider_stream(self, request: LLMRequest):
        """兼容旧 ProviderManager，只要有 `call` 就能工作。"""

        call_stream = getattr(self.provider_manager, "call_stream", None)
        if call_stream is None:
            yield LLMStreamChunk(type="response", response=await self.provider_manager.call(request))
            return

        async for chunk in call_stream(request):
            yield chunk

    def _finalize_streamed_text(self, streamed_text: str, response_text: str | None) -> str:
        """以最终响应文本为准，并在可用时保留已流出的内容。"""

        if response_text is None:
            return streamed_text

        if not streamed_text:
            return response_text

        if response_text.startswith(streamed_text):
            return response_text

        return response_text

    def _coerce_response(self, response, *, text: str | None | object = None) -> LLMResponse:
        """兼容历史测试桩，只要求响应对象具备最小字段。"""

        resolved_text = getattr(response, "text", None) if text is None else text
        return LLMResponse(
            type=response.type,
            text=resolved_text,
            tool_calls=getattr(response, "tool_calls", None),
            provider=getattr(response, "provider", ""),
            model=getattr(response, "model", ""),
            usage=getattr(response, "usage", {}) or {},
        )

    def _stream_tail(self, streamed_text: str, response_text: str) -> str:
        """补齐流式过程中尚未发出的尾部文本。"""

        if not streamed_text:
            return response_text

        if response_text.startswith(streamed_text):
            return response_text[len(streamed_text) :]

        return ""

    def _is_tool_failure_result(self, result: str) -> bool:
        if (result or "").startswith("Tool execution failed:"):
            return True
        try:
            payload = json.loads(result or "")
        except json.JSONDecodeError:
            return False
        return isinstance(payload, dict) and bool(payload.get("error"))

    def _build_direct_tool_response(self, tool_calls: list[dict], context: list[dict]) -> str:
        if len(tool_calls) != 1:
            return ""

        tool_call = tool_calls[0]
        tool_name = tool_call.get("name", "")
        if tool_name not in {
            "cron_create_task",
            "cron_update_task",
            "cron_list_tasks",
            "cron_delete_task",
            "cron_run_now",
            "reminder_create",
            "reminder_list",
            "reminder_delete",
        }:
            return ""

        tool_result = self._find_tool_result_content(context, tool_call.get("id", ""))
        if not tool_result:
            return ""

        try:
            payload = json.loads(tool_result)
        except json.JSONDecodeError:
            if self._is_tool_failure_result(tool_result):
                return self._format_direct_tool_response(tool_name, {"error": tool_result})
            return ""

        return self._format_direct_tool_response(tool_name, payload)

    def _find_tool_result_content(self, context: list[dict], tool_call_id: str) -> str:
        for message in reversed(context):
            if message.get("role") == "tool" and message.get("tool_call_id") == tool_call_id:
                return message.get("content", "")
        return ""

    def _format_direct_tool_response(self, tool_name: str, payload: dict) -> str:
        if payload.get("error"):
            action = {
                "cron_create_task": "创建定时任务",
                "cron_update_task": "更新定时任务",
                "cron_delete_task": "删除定时任务",
                "cron_run_now": "触发定时任务",
                "reminder_create": "创建提醒",
                "reminder_delete": "删除提醒",
            }.get(tool_name, "执行工具")
            return f"{action}失败：{payload.get('error')}"

        if tool_name == "cron_list_tasks":
            tasks = payload.get("tasks", [])
            if not tasks:
                return "你现在没有定时任务。"
            enabled_count = sum(1 for task in tasks if task.get("enabled"))
            return f"你现在有 {len(tasks)} 个定时任务，其中 {enabled_count} 个已启用。"

        if tool_name == "reminder_list":
            reminders = payload.get("reminders", [])
            pending_count = sum(1 for item in reminders if item.get("status") == "pending")
            if not reminders:
                return "你现在没有一次性提醒。"
            return f"你现在有 {len(reminders)} 个一次性提醒，其中 {pending_count} 个待执行。"

        if tool_name in {"cron_create_task", "cron_update_task"}:
            verb = "已创建" if tool_name == "cron_create_task" else "已更新"
            next_run = payload.get("next_run_at") or "未计算"
            return f"{verb}定时任务：{payload.get('name', payload.get('task_id', '未命名'))}，下次执行时间：{next_run}。"

        if tool_name == "cron_delete_task":
            status = "已删除" if payload.get("deleted") else "没有找到"
            return f"{status}定时任务：{payload.get('task_id')}。"

        if tool_name == "cron_run_now":
            return f"已触发定时任务，本次 run_id：{payload.get('run_id')}。"

        if tool_name == "reminder_create":
            run_at = payload.get("run_at_display") or payload.get("run_at")
            return f"已设置提醒：{payload.get('title', '提醒')}，将在 {run_at} 执行。"

        if tool_name == "reminder_delete":
            status = "已删除" if payload.get("deleted") else "没有找到"
            return f"{status}提醒：{payload.get('reminder_id')}。"

        return ""

    def _log_run_timing_summary(
        self,
        *,
        thread_id: str,
        run_id: str,
        channel: str,
        message_metadata: dict,
    ) -> None:
        """输出一条更适合人工阅读的运行耗时汇总日志。"""

        logger.info(
            f"{trace_fields(message_metadata, session_id=thread_id, channel=channel, run_id=run_id)} "
            f"event=run_timing_summary iterations={message_metadata.get('timing_iterations', 0)} "
            f"first_token_model_ms={message_metadata.get('timing_first_token_model_ms', -1)} "
            f"first_token_run_ms={message_metadata.get('timing_first_token_run_ms', -1)} "
            f"model_total_ms={message_metadata.get('timing_model_ms_total', 0)} "
            f"tool_exec_ms_total={message_metadata.get('timing_tool_exec_ms_total', 0)} "
            f"tool_roundtrip_ms_total={message_metadata.get('timing_tool_roundtrip_ms_total', 0)} "
            f"tool_call_count={message_metadata.get('timing_tool_call_count', 0)}"
        )

    async def _execute_tool_calls(
        self,
        session,
        context: list[dict],
        *,
        tool_calls: list[dict],
        parent_message_id: str,
        thread_id: str,
        run_id: str,
        step_name: str,
        message_metadata: dict,
        channel: str,
        approval_store: PendingApprovalStore | None,
        thread_aliases: list[str],
        channel_instance: str,
        session_id: str,
        sender: str | None,
    ):
        for tool_call in tool_calls:
            if self._requires_approval(tool_call["name"]) and approval_store is None:
                yield RunErrorEvent(message="approval store unavailable", code="ApprovalStoreUnavailable")
                return

            if approval_store is not None and self._requires_approval(tool_call["name"]):
                approval = approval_store.create(
                    thread_id=thread_id,
                    run_id=run_id,
                    tool_call=tool_call,
                    context=context,
                    message=f"Approval required for {tool_call['name']}",
                    aliases=thread_aliases,
                )
                self.session_archive.persist_session(session)
                logger.info(
                    f"{trace_fields(message_metadata, session_id=thread_id, channel=channel, run_id=run_id)} "
                    f"event=tool_approval_requested step_name={step_name} tool_name={tool_call['name']} "
                    f"tool_call_id={tool_call['id']}"
                )
                yield CustomEvent(
                    name="on_interrupt",
                    value={
                        "approval_id": approval.approval_id,
                        "thread_id": thread_id,
                        "run_id": run_id,
                        "tool_name": tool_call["name"],
                        "tool_call_id": tool_call["id"],
                        "args": tool_call["input"],
                        "message": approval.message,
                    },
                )
                return

            args_json = json.dumps(tool_call["input"], ensure_ascii=False)
            tool_dispatch_started_at = monotonic_now()
            logger.info(
                f"{trace_fields(message_metadata, session_id=thread_id, channel=channel, run_id=run_id)} "
                f"event=tool_execution_started step_name={step_name} tool_name={tool_call['name']} "
                f"tool_call_id={tool_call['id']} args_chars={len(args_json)}"
            )
            self.react_logger.record(
                "tool_call",
                trace_id=message_metadata.get("trace_id"),
                thread_id=thread_id,
                run_id=run_id,
                step_name=step_name,
                tool_call_id=tool_call["id"],
                tool_name=tool_call["name"],
                args=tool_call["input"],
            )
            yield ToolCallStartEvent(
                tool_call_id=tool_call["id"],
                tool_call_name=tool_call["name"],
                parent_message_id=parent_message_id,
            )
            yield ToolCallArgsEvent(tool_call_id=tool_call["id"], delta=args_json)

            tool_exec_started_at = monotonic_now()
            tool_context = RuntimeToolContext(
                workspace_dir=self.workspace_dir,
                run_id=run_id,
                channel=channel,
                channel_instance=channel_instance,
                session_id=session_id,
                sender=sender,
                metadata=message_metadata,
            )
            result = self.tool_executor.execute(
                tool_call["name"],
                tool_call["input"],
                context=tool_context,
            )
            tool_exec_ms = elapsed_ms(tool_exec_started_at)
            tool_roundtrip_ms = elapsed_ms(tool_dispatch_started_at)
            message_metadata["timing_tool_exec_ms_total"] += tool_exec_ms
            message_metadata["timing_tool_roundtrip_ms_total"] += tool_roundtrip_ms
            message_metadata["timing_tool_call_count"] += 1
            tool_message_id = str(uuid4())
            tool_message = {
                "id": tool_message_id,
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": result,
            }
            yield ToolCallResultEvent(
                message_id=tool_message_id,
                tool_call_id=tool_call["id"],
                content=result,
            )
            yield ToolCallEndEvent(tool_call_id=tool_call["id"])
            logger.info(
                f"{trace_fields(message_metadata, session_id=thread_id, channel=channel, run_id=run_id)} "
                f"event=tool_execution_completed step_name={step_name} tool_name={tool_call['name']} "
                f"tool_call_id={tool_call['id']} exec_ms={tool_exec_ms} roundtrip_ms={tool_roundtrip_ms} "
                f"result_chars={len(result or '')} failed={self._is_tool_failure_result(result)}"
            )
            self.react_logger.record(
                "tool_result",
                trace_id=message_metadata.get("trace_id"),
                thread_id=thread_id,
                run_id=run_id,
                step_name=step_name,
                tool_call_id=tool_call["id"],
                tool_name=tool_call["name"],
                result=result,
                failed=self._is_tool_failure_result(result),
            )
            session.append(tool_message)
            context.append(tool_message)

    async def _resume_from_command(
        self,
        session,
        *,
        thread_id: str,
        run_id: str,
        command: dict,
        message_metadata: dict,
        channel: str,
        runtime_control: RunControl | None,
        approval_store: PendingApprovalStore | None,
        channel_instance: str,
        session_id: str,
        sender: str | None,
    ):
        if approval_store is None:
            yield RunErrorEvent(message="approval store unavailable", code="ApprovalStoreUnavailable")
            return

        interrupt_event = command.get("interrupt_event") or {}
        approval_id = interrupt_event.get("approval_id")
        if not approval_id:
            yield RunErrorEvent(message="missing approval_id", code="MissingApprovalId")
            return

        pending = approval_store.resolve(approval_id)
        if pending is None:
            yield RunErrorEvent(message="pending approval not found", code="PendingApprovalNotFound")
            return

        self._ensure_active(runtime_control)
        yield StepStartedEvent(step_name="approval-resume")
        approved = bool((command.get("resume") or {}).get("approved"))
        logger.info(
            f"{trace_fields(message_metadata, session_id=thread_id, channel=channel, run_id=run_id)} "
            f"event=approval_resume_started tool_name={pending.tool_call['name']} approved={approved}"
        )
        yield ToolCallStartEvent(
            tool_call_id=pending.tool_call["id"],
            tool_call_name=pending.tool_call["name"],
        )
        yield ToolCallArgsEvent(
            tool_call_id=pending.tool_call["id"],
            delta=json.dumps(pending.tool_call["input"], ensure_ascii=False),
        )
        if approved:
            result = self.tool_executor.execute(pending.tool_call["name"], pending.tool_call["input"])
        else:
            result = f"Tool execution rejected by user: {pending.tool_call['name']}"

        tool_message_id = str(uuid4())
        tool_message = {
            "id": tool_message_id,
            "role": "tool",
            "tool_call_id": pending.tool_call["id"],
            "content": result,
        }
        yield ToolCallResultEvent(
            message_id=tool_message_id,
            tool_call_id=pending.tool_call["id"],
            content=result,
        )
        yield ToolCallEndEvent(tool_call_id=pending.tool_call["id"])
        yield StepFinishedEvent(step_name="approval-resume")
        session.append(tool_message)

        context = list(pending.context)
        context.append(tool_message)
        async for event in self._run_loop(
            session,
            context,
            thread_id=thread_id,
            run_id=run_id,
            message_metadata=message_metadata,
            channel=channel,
            runtime_control=runtime_control,
            approval_store=approval_store,
            thread_aliases=list(pending.thread_aliases[1:]),
            channel_instance="default",
            session_id=thread_id,
            sender=None,
            user_message="",
            source_message_id="",
            sender_id=None,
        ):
            yield event

    def _ensure_active(self, runtime_control: RunControl | None) -> None:
        if runtime_control is not None:
            runtime_control.ensure_active()

    def _requires_approval(self, tool_name: str) -> bool:
        if tool_name in {"file_write", "profile_write"}:
            return True
        return bool(self.shell_requires_confirmation and tool_name == "shell_exec")

    def _select_history_for_context(self, history: list[dict]) -> list[dict]:
        """Keep the model context bounded while preserving recent complete turns."""

        if self.context_history_turns <= 0 or not history:
            return []

        user_turns_seen = 0
        start_index = 0
        for index in range(len(history) - 1, -1, -1):
            if history[index].get("role") == "user":
                user_turns_seen += 1
                if user_turns_seen >= self.context_history_turns:
                    start_index = index
                    break
        selected = list(history[start_index:])
        while selected and selected[0].get("role") == "tool":
            selected.pop(0)
        return selected

    def _build_memory_items_text(self, user_message: str) -> str:
        if self.memory_store is None:
            return ""

        rows = []
        seen_ids: set[str] = set()
        for row in self.memory_store.list_recent(limit=5, kind="profile"):
            rows.append(row)
            seen_ids.add(row["id"])
        for row in self.memory_store.list_recent(limit=5, kind="preference"):
            if row["id"] in seen_ids:
                continue
            rows.append(row)
            seen_ids.add(row["id"])
        for row in self.memory_store.search(user_message, limit=5):
            if row["id"] in seen_ids:
                continue
            rows.append(row)
            seen_ids.add(row["id"])

        return "\n".join(
            f"- {row['kind']}: {row['title']} - {row['content']}"
            for row in rows[:8]
        )

    def _extract_memories(
        self,
        *,
        user_message: str,
        assistant_text: str,
        thread_id: str,
        source_message_id: str,
        sender_id: str | None,
    ) -> None:
        if self.memory_store is None or self.memory_extractor is None:
            return

        candidates = self.memory_extractor.extract(
            user_message=user_message,
            assistant_message=assistant_text,
            thread_id=thread_id,
            message_id=source_message_id,
            sender_id=sender_id,
        )
        for candidate in candidates:
            memory_id = self.memory_store.add_item(
                kind=candidate.kind,
                title=candidate.title,
                content=candidate.content,
                confidence=candidate.confidence,
                importance=candidate.importance,
                source_thread_id=candidate.source_thread_id,
                source_message_id=candidate.source_message_id,
                source_sender_id=candidate.source_sender_id,
            )
            self.react_logger.record(
                "profile_write",
                thread_id=thread_id,
                source_message_id=source_message_id,
                memory_id=memory_id,
                kind=candidate.kind,
                title=candidate.title,
                content=candidate.content,
            )

    async def _ingest_to_mflow(self, session, thread_id: str) -> None:
        """异步将最新一轮对话写入 M-flow（非阻塞）"""
        if self.mflow_bridge is None:
            return

        try:
            # 提取最新一轮的用户消息和助手回复
            messages = list(getattr(session, "history", []))
            if len(messages) < 2:
                return

            # 找到最后一个用户消息和助手回复
            user_msg = None
            assistant_msg = None
            tool_calls = []

            for i in range(len(messages) - 1, -1, -1):
                msg = messages[i]
                if msg.get("role") == "assistant" and assistant_msg is None:
                    assistant_msg = msg
                    if msg.get("tool_calls"):
                        tool_calls = msg["tool_calls"]
                elif msg.get("role") == "user" and user_msg is None:
                    user_msg = msg
                    break

            if not user_msg or not assistant_msg:
                return

            from datetime import datetime

            turn_data = TurnData(
                session_id=thread_id,
                turn_index=len(messages) // 2,  # 粗略估计轮次
                timestamp=datetime.now(),
                user_message=user_msg.get("content", ""),
                assistant_response=assistant_msg.get("content", ""),
                tool_calls=[
                    {
                        "name": tc.get("function", {}).get("name", ""),
                        "summary": f"{tc.get('function', {}).get('name', '')}(...)",
                    }
                    for tc in tool_calls
                ] if tool_calls else None,
            )

            # 异步写入，不等待结果
            asyncio.create_task(self.mflow_bridge.ingest_turn(turn_data))

        except Exception as e:
            # 写入失败不应该影响主流程
            import logging
            logging.getLogger(__name__).warning(f"M-flow ingestion failed: {e}")

    @classmethod
    def build_for_test(
        cls,
        workspace_dir: Path,
        provider_manager,
        *,
        memory_store: MemoryStore | None = None,
        runtime_services: RuntimeServices | None = None,
        enable_shell: bool = False,
        shell_requires_confirmation: bool = True,
    ) -> "AgentCore":
        """测试专用工厂。

        这样单元测试可以快速组一个最小可运行 Core，
        不需要走完整的 build_app 流程。
        """

        workspace = Path(workspace_dir)
        return cls(
            workspace_dir=workspace,
            provider_manager=provider_manager,
            always_on_memory=AlwaysOnMemory(
                workspace / "SOUL.md",
                workspace / "PROFILE.md",
                legacy_memory_file=workspace / "MEMORY.md",
            ),
            session_archive=SessionArchive(workspace / "agent.db"),
            session_manager=SessionManager(workspace / "agent.db"),
            skill_loader=SkillLoader(workspace / "skills"),
            ledger_store=LedgerStore(workspace / "agent.db"),
            note_store=NoteStore(workspace / "agent.db"),
            memory_store=memory_store,
            memory_extractor=MemoryExtractor(),
            runtime_services=runtime_services,
            enable_shell=enable_shell,
            shell_requires_confirmation=shell_requires_confirmation,
        )
