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
from agent.memory import AlwaysOnMemory, LedgerStore, NoteStore, SessionArchive, TurnData
from agent.observability.tracing import elapsed_ms, ensure_trace_id, monotonic_now, text_preview, trace_fields
from agent.session import SessionManager
from agent.skills import SkillLoader
from agent.tools import ToolExecutor, build_stage1_registry
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
        mflow_bridge=None,
        max_iterations: int = 8,
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
        self.max_iterations = max_iterations
        self.note_store = note_store
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
                ):
                    yield event
                return

            context = self.context_assembler.assemble(
                soul_text=self.always_on_memory.load_soul(),
                memory_text=self.always_on_memory.load_memory(),
                tool_index=self.tool_registry.get_index(),
                skill_index=self.skill_loader.get_index(),
                history=session.history,
                user_message=message.body,
                channel=message.channel,
                channel_instance=message.channel_instance,
            )
            logger.info(
                f"{trace_fields(metadata, session_id=thread_id, channel=message.channel, run_id=run_id)} "
                f"event=context_assembled context_messages={len(context)} "
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
                runtime_control=runtime_control,
                approval_store=approval_store,
                thread_aliases=thread_aliases,
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
        runtime_control: RunControl | None,
        approval_store: PendingApprovalStore | None,
        thread_aliases: list[str],
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
            self._ensure_active(runtime_control)
            if response.type == "text":
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
            ):
                if isinstance(event, CustomEvent):
                    interrupted = True
                yield event

            yield StepFinishedEvent(step_name=step_name)
            logger.info(
                f"{trace_fields(message_metadata, session_id=thread_id, channel=channel, run_id=run_id)} "
                f"event=step_finished step_name={step_name} step_ms={elapsed_ms(step_started_at)}"
            )
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
        return (result or "").startswith("Tool execution failed:")

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
    ):
        for tool_call in tool_calls:
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
            yield ToolCallStartEvent(
                tool_call_id=tool_call["id"],
                tool_call_name=tool_call["name"],
                parent_message_id=parent_message_id,
            )
            yield ToolCallArgsEvent(tool_call_id=tool_call["id"], delta=args_json)

            tool_exec_started_at = monotonic_now()
            result = self.tool_executor.execute(tool_call["name"], tool_call["input"])
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
        ):
            yield event

    def _ensure_active(self, runtime_control: RunControl | None) -> None:
        if runtime_control is not None:
            runtime_control.ensure_active()

    def _requires_approval(self, tool_name: str) -> bool:
        return tool_name in {"file_write", "memory_write"}

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
    def build_for_test(cls, workspace_dir: Path, provider_manager) -> "AgentCore":
        """测试专用工厂。

        这样单元测试可以快速组一个最小可运行 Core，
        不需要走完整的 build_app 流程。
        """

        workspace = Path(workspace_dir)
        return cls(
            workspace_dir=workspace,
            provider_manager=provider_manager,
            always_on_memory=AlwaysOnMemory(workspace / "SOUL.md", workspace / "MEMORY.md"),
            session_archive=SessionArchive(workspace / "agent.db"),
            session_manager=SessionManager(workspace / "agent.db"),
            skill_loader=SkillLoader(workspace / "skills"),
            ledger_store=LedgerStore(workspace / "agent.db"),
            note_store=NoteStore(workspace / "agent.db"),
        )
