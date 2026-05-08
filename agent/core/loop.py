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
import re
from pathlib import Path
from uuid import uuid4

from agent.core.compaction import CompactionEngine
from agent.core.context import ContextAssembler
from agent.core.provider import LLMRequest, LLMResponse, LLMStreamChunk
from agent.memory import (
    AlwaysOnMemory,
    IdentityStore,
    LedgerStore,
    MemoryExtractor,
    Mem0MemoryService,
    MemoryStore,
    NoteStore,
    ProfileStore,
    SessionArchive,
)
from agent.observability.react_log import ReactTraceLogger
from agent.observability.langfuse_tracer import NoopObservation
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


def _rrf_merge(
    mem0_rows: list[dict],
    local_rows: list[dict],
    *,
    k: int = 60,
    top_n: int = 5,
) -> list[str]:
    """Reciprocal Rank Fusion: merge two retrieval result lists into ranked text items."""
    scores: dict[str, float] = {}
    for rank, row in enumerate(mem0_rows):
        text = _rrf_text_from_mem0_row(row)
        if text:
            scores[text] = scores.get(text, 0.0) + 1.0 / (k + rank + 1)
    for rank, row in enumerate(local_rows):
        text = f"{row.get('title', '')} {row.get('content', '')}".strip()
        if text:
            scores[text] = scores.get(text, 0.0) + 1.0 / (k + rank + 1)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [text for text, _ in ranked[:top_n]]


def _rrf_text_from_mem0_row(row: dict) -> str:
    for key in ("memory", "content", "text", "summary"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


_TOOL_VISIBILITY_ROUTES = (
    "general",
    "fitness",
    "bookkeeping",
    "notes",
    "scheduling",
    "current_events",
    "identity",
)


class AgentCore:
    """把各个子系统串起来的核心运行器。"""

    def __init__(
        self,
        *,
        workspace_dir: Path,
        provider_manager,
        always_on_memory: AlwaysOnMemory,
        identity_store: IdentityStore | None = None,
        profile_store: ProfileStore | None = None,
        session_archive: SessionArchive,
        session_manager: SessionManager,
        skill_loader: SkillLoader,
        ledger_store: LedgerStore | None = None,
        note_store: NoteStore | None = None,
        mem0_memory_service: Mem0MemoryService | None = None,
        memory_store: MemoryStore | None = None,
        memory_extractor: MemoryExtractor | None = None,
        react_logger: ReactTraceLogger | None = None,
        trace_client=None,
        runtime_services: RuntimeServices | None = None,
        enable_shell: bool = False,
        shell_requires_confirmation: bool = True,
        max_iterations: int = 8,
        context_history_turns: int = 10,
        system_prompt: str = "",
    ) -> None:
        # 这些依赖都在 build_app 或测试工厂中注入；
        # AgentCore 自己不负责创建它们，只负责调度它们协作。
        self.workspace_dir = Path(workspace_dir)
        self.provider_manager = provider_manager
        self.always_on_memory = always_on_memory
        self.identity_store = identity_store
        self.profile_store = profile_store
        self.session_archive = session_archive
        self.session_manager = session_manager
        self.skill_loader = skill_loader
        self.ledger_store = ledger_store
        self.runtime_services = runtime_services or RuntimeServices()
        self.shell_requires_confirmation = shell_requires_confirmation
        self.max_iterations = max_iterations
        self.context_history_turns = context_history_turns
        self.note_store = note_store
        self.mem0_memory_service = mem0_memory_service
        self.memory_store = memory_store
        self.memory_extractor = memory_extractor
        self._background_tasks: set[asyncio.Task] = set()
        self.react_logger = react_logger or ReactTraceLogger(self.workspace_dir / "logs" / "react.log")
        self.trace_client = trace_client
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
            identity_store=self.identity_store,
            ledger_store=self.ledger_store,
            note_store=self.note_store,
            profile_store=self.profile_store,
            mem0_memory_service=self.mem0_memory_service,
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

        # 一个用户输入对应一个 Langfuse trace；后面的模型调用、工具调用都会挂在
        # 这个 trace 下。这里不要做业务特判，否则会绕过统一 ReAct 链路，导致
        # 工具选择、记忆提取和 tracing 行为不一致。
        trace_cm = self._trace_start_trace(
            "agent.run",
            input=message.body,
            metadata={
                "run_id": run_id,
                "trace_id": metadata.get("trace_id"),
                "thread_id": thread_id,
                "session_id": message.session_id,
                "channel": message.channel,
                "channel_instance": message.channel_instance,
                "sender": message.sender,
                "body_chars": len(message.body),
            },
        )
        trace_observation = trace_cm.__enter__()
        try:
            session = await self.session_manager.get_or_create(thread_id, channel=message.channel)
            logger.info(
                f"{trace_fields(metadata, session_id=thread_id, channel=message.channel, run_id=run_id)} "
                f"event=session_loaded history_messages={len(session.history)}"
            )
            command = message.metadata.get("command") or {}
            if command:
                # command 是 Web/飞书审批等“恢复执行”入口，不是普通用户新问题。
                # 它复用同一个 run_events 外壳，但不会重新组装一轮普通对话上下文。
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

            fitness_reply = self._handle_fitness_flow_guard(session, message)
            if fitness_reply is not None:
                session.append({"id": message.message_id, "role": "user", "content": message.body})
                assistant_message_id = str(uuid4())
                session.append({"id": assistant_message_id, "role": "assistant", "content": fitness_reply})
                yield RunFinishedEvent(thread_id=thread_id, run_id=run_id, result_text=fitness_reply)
                return

            with self._trace_start_span(
                "context.assemble",
                input={"user_message": message.body, "history_messages": len(session.history)},
                metadata={"run_id": run_id, "trace_id": metadata.get("trace_id")},
            ) as context_span:
                # 历史消息先裁剪再进入模型，避免长会话把旧身份、旧工具结果、
                # 大块 tool payload 全量带回当前轮，既省 token 也减少“旧人设复活”。
                selected_history = self._select_history_for_context(session.history, user_message=message.body)
                tool_route, visibility_tags = await self._select_tool_visibility(
                    selected_history,
                    user_message=message.body,
                    thread_id=thread_id,
                    run_id=run_id,
                    channel=message.channel,
                )
                visible_tools = self.tool_registry.get_schemas(visibility_tags=visibility_tags)
                active_skill_content = self._load_active_skill_for_route(tool_route)
                context = self.context_assembler.assemble(
                    soul_text=self.always_on_memory.load_soul(),
                    memory_text=self.always_on_memory.load_profile(),
                    memory_items_text=await self._build_memory_items_text(
                        user_message=message.body,
                        sender_id=message.sender,
                        thread_id=thread_id,
                    ),
                    tool_index="",
                    skill_index=self.skill_loader.get_index(),
                    history=selected_history,
                    user_message=message.body,
                    channel=message.channel,
                    channel_instance=message.channel_instance,
                    sender=message.sender,
                    metadata=message.metadata,
                    active_skill_content=active_skill_content,
                )
                context_span.update(
                    output={
                        "context_messages": len(context),
                        "history_messages_used": len(selected_history),
                        "context_tokens": self.context_assembler.count_context_tokens(context),
                        "tool_count": len(visible_tools),
                        "tool_route": tool_route,
                        "active_skill": tool_route if active_skill_content else "",
                    }
                )
            logger.info(
                f"{trace_fields(metadata, session_id=thread_id, channel=message.channel, run_id=run_id)} "
                f"event=context_assembled context_messages={len(context)} "
                f"history_messages_used={len(selected_history)} "
                f"context_tokens={self.context_assembler.count_context_tokens(context)} "
                f"tool_count={len(visible_tools)} tool_route={tool_route}"
            )
            # 当前用户消息既要进入本次模型上下文，也要落进会话历史。
            session.append({"id": message.message_id, "role": "user", "content": message.body})

            async for event in self._run_loop(
                session,
                context,
                visible_tools=visible_tools,
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
                if isinstance(event, RunFinishedEvent):
                    trace_observation.update(
                        output=event.result_text,
                        metadata={
                            "run_id": run_id,
                            "trace_id": metadata.get("trace_id"),
                            "total_ms": elapsed_ms(run_started_at),
                            "iterations": metadata.get("timing_iterations", 0),
                            "model_total_ms": metadata.get("timing_model_ms_total", 0),
                            "tool_exec_ms_total": metadata.get("timing_tool_exec_ms_total", 0),
                            "tool_roundtrip_ms_total": metadata.get("timing_tool_roundtrip_ms_total", 0),
                            "tool_call_count": metadata.get("timing_tool_call_count", 0),
                        },
                    )
                yield event
        except Exception as exc:
            code = "RunInterrupted" if isinstance(exc, RunInterrupted) else type(exc).__name__
            trace_observation.update(level="ERROR", status_message=str(exc), metadata={"code": code})
            logger.error(
                f"{trace_fields(metadata, session_id=thread_id, channel=message.channel, run_id=run_id)} "
                f"event=run_failed total_ms={elapsed_ms(run_started_at)} code={code} error={exc}",
                exc_info=True,
            )
            yield RunErrorEvent(message=str(exc), code=code)
        finally:
            trace_cm.__exit__(None, None, None)
            self._trace_flush()

    def run_sync(self, message) -> str:
        """给同步调用方（例如 CLI）提供一个方便入口。"""

        async def run_and_drain() -> str:
            result = await self.run(message)
            await self.drain_background_tasks()
            return result

        return asyncio.run(run_and_drain())

    async def _run_loop(
        self,
        session,
        context: list[dict],
        *,
        visible_tools: list[dict],
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
            # 每个 iteration 是一次标准 ReAct 回合：
            # 1. 把当前 context 发给模型；
            # 2. 如果模型直接给文本，就归档并结束；
            # 3. 如果模型请求工具，就执行工具，把工具结果追加进 context，再进入下一轮。
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

            # request.tools 是模型可见的 function schema；schema 文案会影响模型
            # 是否正确选择工具，所以工具描述尽量明确业务边界。
            request = LLMRequest(messages=context, tools=visible_tools)
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

            generation_cm = self._trace_start_generation(
                "llm.chat",
                input=request.messages,
                metadata={
                    "run_id": run_id,
                    "trace_id": message_metadata.get("trace_id"),
                    "thread_id": thread_id,
                    "step_name": step_name,
                    "context_messages": len(context),
                    "tool_count": len(request.tools),
                },
            )
            generation_observation = generation_cm.__enter__()
            try:
                async for chunk in self._iter_provider_stream(request):
                    self._ensure_active(runtime_control)

                    if chunk.type == "text_delta":
                        # 流式文本先逐块透传给上层 gateway/web UI；最终仍以 provider
                        # 返回的完整 response.text 为准，避免分块丢字或尾部缺失。
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
            except Exception as exc:
                generation_observation.update(level="ERROR", status_message=str(exc))
                generation_cm.__exit__(type(exc), exc, exc.__traceback__)
                raise

            if response is None:
                generation_observation.update(level="ERROR", status_message="Provider stream ended without a final response")
                generation_cm.__exit__(None, None, None)
                raise RuntimeError("Provider stream ended without a final response")

            streamed_text = "".join(streamed_text_parts)
            response = self._coerce_response(response)
            finalized_text = self._finalize_streamed_text(streamed_text, response.text)
            if finalized_text != response.text:
                response = self._coerce_response(response, text=finalized_text)
            safe_response_text = self._apply_truthfulness_guard(
                user_message=user_message,
                assistant_text=response.text or "",
                tool_calls=response.tool_calls or [],
            )
            if safe_response_text != (response.text or ""):
                response = self._coerce_response(response, text=safe_response_text)
            model_ms = elapsed_ms(model_started_at)
            message_metadata["timing_model_ms_total"] += model_ms
            usage = response.usage or {}
            generation_observation.update(
                output={"text": response.text or "", "tool_calls": response.tool_calls or []},
                metadata={
                    **(response.metadata or {}),
                    "run_id": run_id,
                    "trace_id": message_metadata.get("trace_id"),
                    "step_name": step_name,
                    "response_type": response.type,
                    "model_ms": model_ms,
                    "streamed_chars": len(streamed_text),
                    "output_chars": len(response.text or ""),
                    "tool_call_count": len(response.tool_calls or []),
                    "first_token_model_ms": message_metadata.get("timing_first_token_model_ms"),
                },
                usage_details=usage,
                model=response.model or None,
            )
            generation_cm.__exit__(None, None, None)
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
                # 普通文本回复是本轮 ReAct 的终点：归档 assistant 消息、
                # 触发记忆提取、非阻塞写入 M-flow，然后发出 RunFinishedEvent。
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
                self._schedule_memory_extraction(
                    user_message=user_message,
                    assistant_text=assistant_text,
                    thread_id=thread_id,
                    source_message_id=source_message_id,
                    sender_id=sender_id,
                )

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
            # 工具调用请求也必须进入 session/context。下一轮模型需要看到：
            # “我刚刚请求了哪些工具”以及“工具分别返回了什么”。
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
                    # 例如 shell 审批会发出中断事件：当前轮先停在等待确认状态，
                    # 不再继续让模型基于“尚未执行的工具”编造后续回答。
                    interrupted = True
                if isinstance(event, RunErrorEvent):
                    yield event
                    return
                yield event

            direct_text = "" if interrupted else self._build_direct_tool_response(response.tool_calls or [], context)
            if direct_text:
                # 某些工具结果已经足够面向用户，例如 reminder_create 或 cron_list_tasks。
                # 这些场景可以跳过第二次模型总结，降低延迟并避免模型改写关键时间/id。
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
                self._schedule_memory_extraction(
                    user_message=user_message,
                    assistant_text=direct_text,
                    thread_id=thread_id,
                    source_message_id=source_message_id,
                    sender_id=sender_id,
                )

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
            metadata=getattr(response, "metadata", {}) or {},
        )

    def _stream_tail(self, streamed_text: str, response_text: str) -> str:
        """补齐流式过程中尚未发出的尾部文本。"""

        if not streamed_text:
            return response_text

        if response_text.startswith(streamed_text):
            return response_text[len(streamed_text) :]

        return ""

    def _apply_truthfulness_guard(
        self,
        *,
        user_message: str,
        assistant_text: str,
        tool_calls: list[dict],
    ) -> str:
        if not assistant_text or tool_calls:
            return assistant_text

        if self._contains_fake_tool_claim(assistant_text):
            return (
                "我刚才是根据现有记录和这轮对话直接回答的，"
                "这一轮没有额外查别的。"
            )

        if self._should_soften_unverified_memory_confirmation(
            user_message=user_message,
            assistant_text=assistant_text,
        ):
            return "知道了。"

        return assistant_text

    async def _select_tool_visibility(
        self,
        history: list[dict],
        *,
        user_message: str,
        thread_id: str,
        run_id: str,
        channel: str,
    ) -> tuple[str, set[str]]:
        route = "general"
        router_messages = self._build_tool_router_messages(history, user_message=user_message)
        request = LLMRequest(
            messages=router_messages,
            max_tokens=24,
            temperature=0,
        )
        route_generation_cm = self._trace_start_generation(
            "llm.route",
            input=router_messages,
            metadata={
                "run_id": run_id,
                "thread_id": thread_id,
                "channel": channel,
                "purpose": "tool_visibility_routing",
            },
        )
        route_observation = route_generation_cm.__enter__()
        try:
            response = await self.provider_manager.call(request)
            route = self._parse_tool_route(response.text or "")
            route_observation.update(
                output=response.text or "",
                metadata={"route": route, "run_id": run_id},
            )
        except Exception as exc:
            route_observation.update(level="ERROR", status_message=str(exc))
            logger.warning(
                f"{trace_fields({'trace_id': ''}, session_id=thread_id, channel=channel, run_id=run_id)} "
                f"event=tool_visibility_route_failed error={exc}"
            )
        finally:
            route_generation_cm.__exit__(None, None, None)
        visibility_tags = {"always", route}
        visible_count = len(self.tool_registry.get_schemas(visibility_tags=visibility_tags))
        logger.info(
            f"{trace_fields({'trace_id': ''}, session_id=thread_id, channel=channel, run_id=run_id)} "
            f"event=tool_visibility_route_selected route={route} visible_tool_count={visible_count}"
        )
        return route, visibility_tags

    def _build_tool_router_messages(self, history: list[dict], *, user_message: str) -> list[dict]:
        recent_lines: list[str] = []
        for item in history[-4:]:
            role = item.get("role")
            content = (item.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            compact = " ".join(content.split())
            recent_lines.append(f"{role}: {compact[:200]}")
        history_block = "\n".join(recent_lines) if recent_lines else "无"
        return [
            {
                "role": "system",
                "content": (
                    "你是工具可见性路由器。"
                    "只根据当前用户请求和最近对话，选择一个最小工具域。"
                    "候选路由只有：general, fitness, bookkeeping, notes, scheduling, current_events, identity。\n"
                    "判定规则：\n"
                    "- fitness：训练、健身、恢复、动作、训练计划、训练记录。\n"
                    "- bookkeeping：收入、支出、报销、消费、转账、记账、账本统计。\n"
                    "- notes：明确要求记笔记、查笔记、更新笔记、长期笔记库。\n"
                    "- scheduling：提醒、闹钟、cron、定时执行、稍后提醒。\n"
                    "- current_events：当前新闻、最新价格、天气、政策、今日信息、需要联网核验的新鲜事实。\n"
                    "- identity：助手身份、用户称呼、用户核心资料、记忆来源追问。\n"
                    "- general：其他情况，包括通用问答、文件操作、普通任务。\n"
                    "输出要求：只输出一个路由名，不要解释。"
                ),
            },
            {
                "role": "user",
                "content": f"最近对话：\n{history_block}\n\n当前用户消息：{user_message}",
            },
        ]

    def _parse_tool_route(self, text: str) -> str:
        normalized = (text or "").strip().lower()
        patterns = {
            "current_events": r"\bcurrent[_ -]?events\b",
            "bookkeeping": r"\bbookkeeping\b",
            "scheduling": r"\bscheduling\b",
            "identity": r"\bidentity\b",
            "fitness": r"\bfitness\b",
            "notes": r"\bnotes\b",
            "general": r"\bgeneral\b",
        }
        for route in _TOOL_VISIBILITY_ROUTES:
            pattern = patterns[route]
            if re.search(pattern, normalized):
                return route
        return "general"

    _ROUTE_TO_SKILL: dict[str, str] = {
        "fitness": "fitness-coach",
    }

    def _load_active_skill_for_route(self, route: str) -> str:
        skill_name = self._ROUTE_TO_SKILL.get(route)
        if not skill_name:
            return ""
        try:
            return self.skill_loader.read_full(skill_name)
        except Exception as exc:
            logger.debug(f"event=active_skill_load_failed route={route} skill={skill_name} error={exc}")
            return ""

    def _contains_fake_tool_claim(self, assistant_text: str) -> bool:
        patterns = [
            r"通过\s*`?[\w-]+`?\s*工具查询",
            r"通过\s*`?[\w-]+`?\s*工具",
            r"调用了\s*`?[\w-]+`?\s*工具",
        ]
        return any(re.search(pattern, assistant_text) for pattern in patterns)

    def _should_soften_unverified_memory_confirmation(self, *, user_message: str, assistant_text: str) -> bool:
        phrases = (
            "记住了",
            "已记住",
            "我记下了",
            "这份牵挂，我记下了",
            "我会记住",
            "记在长期记忆里了",
            "写进长期记忆了",
            "存进长期记忆了",
            "存入长期记忆了",
            "已写入长期记忆",
            "已存入长期记忆",
            "未曾遗忘",
        )
        return any(phrase in assistant_text for phrase in phrases)

    def _is_explicit_memory_save_request(self, user_message: str) -> bool:
        patterns = (
            "记住",
            "帮我记",
            "替我记",
            "请记下",
            "记一下",
            "存成记忆",
            "写进长期记忆",
        )
        return any(pattern in (user_message or "") for pattern in patterns)

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
            tool_metadata = dict(message_metadata)
            tool_metadata["runtime_services"] = self.runtime_services
            tool_context = RuntimeToolContext(
                workspace_dir=self.workspace_dir,
                run_id=run_id,
                channel=channel,
                channel_instance=channel_instance,
                session_id=session_id,
                thread_key=thread_id,
                sender=sender,
                metadata=tool_metadata,
            )
            tool_trace_cm = self._trace_start_tool(
                f"tool.{tool_call['name']}",
                input=tool_call["input"],
                metadata={
                    "run_id": run_id,
                    "trace_id": message_metadata.get("trace_id"),
                    "thread_id": thread_id,
                    "step_name": step_name,
                    "tool_call_id": tool_call["id"],
                    "tool_name": tool_call["name"],
                    "channel": channel,
                    "channel_instance": channel_instance,
                    "session_id": session_id,
                },
            )
            tool_trace = tool_trace_cm.__enter__()
            try:
                result = self.tool_executor.execute(
                    tool_call["name"],
                    tool_call["input"],
                    context=tool_context,
                )
            except Exception as exc:
                tool_trace.update(level="ERROR", status_message=str(exc))
                tool_trace_cm.__exit__(type(exc), exc, exc.__traceback__)
                raise
            tool_exec_ms = elapsed_ms(tool_exec_started_at)
            tool_roundtrip_ms = elapsed_ms(tool_dispatch_started_at)
            message_metadata["timing_tool_exec_ms_total"] += tool_exec_ms
            message_metadata["timing_tool_roundtrip_ms_total"] += tool_roundtrip_ms
            message_metadata["timing_tool_call_count"] += 1
            tool_failed = self._is_tool_failure_result(result)
            tool_trace_update = {
                "output": result,
                "metadata": {
                    "run_id": run_id,
                    "trace_id": message_metadata.get("trace_id"),
                    "tool_call_id": tool_call["id"],
                    "tool_name": tool_call["name"],
                    "exec_ms": tool_exec_ms,
                    "roundtrip_ms": tool_roundtrip_ms,
                    "result_chars": len(result or ""),
                    "failed": tool_failed,
                },
            }
            if tool_failed:
                tool_trace_update["level"] = "ERROR"
            tool_trace.update(**tool_trace_update)
            tool_trace_cm.__exit__(None, None, None)
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
                f"result_chars={len(result or '')} failed={tool_failed}"
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
                failed=tool_failed,
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
            visible_tools=self.tool_registry.get_schemas(),
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

    def _trace_start_trace(self, name: str, **fields):
        if self.trace_client is None:
            return NoopObservation()
        return self.trace_client.start_trace(name, **fields)

    def _trace_start_span(self, name: str, **fields):
        if self.trace_client is None:
            return NoopObservation()
        return self.trace_client.start_span(name, **fields)

    def _trace_start_generation(self, name: str, **fields):
        if self.trace_client is None:
            return NoopObservation()
        return self.trace_client.start_generation(name, **fields)

    def _trace_start_tool(self, name: str, **fields):
        if self.trace_client is None:
            return NoopObservation()
        return self.trace_client.start_tool(name, **fields)

    def _trace_flush(self) -> None:
        if self.trace_client is None:
            return
        if getattr(self.trace_client, "flush_on_run_end", False):
            self.trace_client.flush()
            return
        flush_async = getattr(self.trace_client, "flush_async", None)
        if flush_async is not None:
            flush_async()

    def _requires_approval(self, tool_name: str) -> bool:
        if tool_name in {
            "assistant_identity_update",
            "file_write",
            "profile_core_update",
            "profile_write",
        }:
            return True
        return bool(self.shell_requires_confirmation and tool_name == "shell_exec")

    def _handle_fitness_flow_guard(self, session, message) -> str | None:
        change_store = getattr(self.runtime_services, "fitness_change_store", None)
        text = (message.body or "").strip()
        normalized = text.lower()

        if change_store is not None:
            pending = change_store.get(message.thread_key, sender=message.sender)
            if pending is not None:
                if normalized in {"确认", "同意", "confirm", "yes", "y"}:
                    from agent.fitness import FitnessFileStore

                    store = FitnessFileStore(self.workspace_dir)
                    if pending.target == "profile":
                        store.update_profile(**pending.updates)
                        message_text = f"已更新健身档案：{pending.summary}"
                    else:
                        store.update_settings(**pending.updates)
                        message_text = f"已更新健身设定：{pending.summary}"
                    change_store.clear(message.thread_key, sender=message.sender)
                    return message_text
                if normalized in {"取消", "算了", "cancel", "no", "n"}:
                    summary = pending.summary
                    change_store.clear(message.thread_key, sender=message.sender)
                    return f"已取消本次健身变更：{summary}"

        return None

    def _select_history_for_context(self, history: list[dict], *, user_message: str = "") -> list[dict]:
        """Keep the model context bounded while preserving recent complete turns."""

        if self.context_history_turns <= 0 or not history:
            return []
        if self._is_assistant_identity_query(user_message):
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
        return self._sanitize_history_for_context(selected)

    def _sanitize_history_for_context(self, history: list[dict]) -> list[dict]:
        sanitized: list[dict] = []
        stale_assistant_names = self._extract_stale_assistant_names(history)
        skip_assistant_until_next_user = False
        for message in history:
            role = message.get("role")
            if role == "tool":
                continue
            content = message.get("content") or ""
            if role == "user" and self._is_assistant_identity_or_persona_history_message(content):
                skip_assistant_until_next_user = True
                continue
            if role == "assistant" and skip_assistant_until_next_user:
                continue
            if role == "assistant" and self._contains_stale_assistant_name(content, stale_assistant_names):
                continue
            if role == "user":
                skip_assistant_until_next_user = False
            clean_message = {
                key: value
                for key, value in message.items()
                if key not in {"tool_calls", "tool_call_id"}
            }
            if role == "assistant" and not clean_message.get("content"):
                continue
            sanitized.append(clean_message)
        return sanitized

    def _is_assistant_identity_query(self, text: str) -> bool:
        normalized = (text or "").strip().lower()
        if not normalized:
            return False
        return normalized in {
            "你是谁",
            "你叫什么",
            "你叫什么名字",
            "你是谁？",
            "你叫什么？",
            "你叫什么名字？",
            "who are you",
            "what is your name",
        }

    def _is_assistant_identity_or_persona_history_message(self, text: str) -> bool:
        normalized = (text or "").strip().lower()
        if not normalized:
            return False
        if self._is_assistant_identity_query(normalized):
            return True
        if "soul" in normalized and any(marker in normalized for marker in {"你", "你的", "人设", "人格", "身份"}):
            return True
        if any(marker in normalized for marker in {"人设", "人格", "身份设定", "角色设定"}):
            return True
        if normalized.startswith(("你叫", "你的名字", "你名字")):
            return True
        if normalized.startswith("你是") and not normalized.startswith(("你是不是", "你是否")):
            return True
        return False

    def _extract_stale_assistant_names(self, history: list[dict]) -> set[str]:
        names: set[str] = set()
        patterns = [
            r"你叫\s*([^\s，,。.!！?？、]+)",
            r"你的名字(?:是|叫)?\s*([^\s，,。.!！?？、]+)",
            r"你名字(?:是|叫)?\s*([^\s，,。.!！?？、]+)",
        ]
        for message in history:
            if message.get("role") != "user":
                continue
            content = message.get("content") or ""
            if not self._is_assistant_identity_or_persona_history_message(content):
                continue
            for pattern in patterns:
                match = re.search(pattern, content, flags=re.IGNORECASE)
                if match:
                    name = match.group(1).strip()
                    if 1 < len(name) <= 20:
                        names.add(name)
                        if len(name) >= 3 and all("\u4e00" <= char <= "\u9fff" for char in name):
                            names.add(name[-2:])
        return names

    def _contains_stale_assistant_name(self, text: str, stale_names: set[str]) -> bool:
        if not text or not stale_names:
            return False
        return any(name in text for name in stale_names)

    async def _build_memory_items_text(self, *, user_message: str, sender_id: str | None, thread_id: str) -> str:
        mem0_rows: list[dict] = []
        local_rows: list[dict] = []
        user_scope = sender_id or "unknown"

        # Primary: mem0 semantic vector search — run in thread to avoid blocking event loop
        if self.mem0_memory_service is not None and self.mem0_memory_service.is_ready:
            logger.info(
                "event=memory_context_search_started backend=mem0 thread_id=%s sender=%s query=%r "
                "说明=开始 mem0 语义向量检索",
                thread_id,
                user_scope,
                user_message,
            )
            outcome = await asyncio.to_thread(
                self.mem0_memory_service.search,
                user_message,
                user_id=user_scope,
                run_id=thread_id,
                top_k=8,
            )
            if outcome.get("ok"):
                mem0_rows = outcome.get("results") or []
            else:
                logger.warning(
                    "event=memory_context_search_failed backend=mem0 error=%s",
                    outcome.get("error"),
                )
            if not mem0_rows:
                fallback = await asyncio.to_thread(
                    self.mem0_memory_service.get_all,
                    user_id=user_scope,
                    top_k=8,
                )
                if fallback.get("ok"):
                    mem0_rows = fallback.get("results") or []

        # Secondary: MemoryStore FTS5 keyword search (sync, < 1 ms)
        if self.memory_store is not None:
            try:
                local_rows = self.memory_store.search(user_message, limit=8)
            except Exception as exc:
                logger.warning("event=memory_fts_search_failed error=%s", exc, exc_info=True)

        # RRF merge
        merged = _rrf_merge(mem0_rows, local_rows, top_n=5)
        if not merged:
            logger.info(
                "event=memory_context_search_empty thread_id=%s sender=%s 说明=混合检索未找到任何长期记忆",
                thread_id,
                user_scope,
            )
            return ""

        logger.info(
            "event=memory_context_search_completed thread_id=%s sender=%s "
            "hit_count=%s mem0_count=%s local_count=%s 说明=混合检索完成并注入上下文",
            thread_id,
            user_scope,
            len(merged),
            len(mem0_rows),
            len(local_rows),
        )
        return "\n".join(f"- {text}" for text in merged)

    def _schedule_memory_extraction(
        self,
        *,
        user_message: str,
        assistant_text: str,
        thread_id: str,
        source_message_id: str,
        sender_id: str | None,
    ) -> None:
        if self.memory_extractor is None:
            logger.info(
                "event=memory_extraction_not_scheduled reason=extractor_unavailable "
                "thread_id=%s source_message_id=%s 说明=未调度长期记忆抽取，原因是抽取器不可用",
                thread_id,
                source_message_id,
            )
            return
        if self.memory_store is None and (self.mem0_memory_service is None or not self.mem0_memory_service.is_ready):
            logger.info(
                "event=memory_extraction_not_scheduled reason=no_memory_backend "
                "thread_id=%s source_message_id=%s 说明=未调度长期记忆抽取，原因是没有可用存储后端",
                thread_id,
                source_message_id,
            )
            return

        logger.info(
            "event=memory_extraction_scheduled thread_id=%s source_message_id=%s sender=%s "
            "user_preview=%s assistant_chars=%s 说明=已调度后台长期记忆抽取任务",
            thread_id,
            source_message_id,
            sender_id or "unknown",
            text_preview(user_message),
            len(assistant_text or ""),
        )
        task = asyncio.create_task(
            self._extract_memories(
                user_message=user_message,
                assistant_text=assistant_text,
                thread_id=thread_id,
                source_message_id=source_message_id,
                sender_id=sender_id,
            )
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def drain_background_tasks(self) -> None:
        """等待当前已调度的后台任务完成，主要供 CLI 和测试收尾使用。"""

        while self._background_tasks:
            tasks = list(self._background_tasks)
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _extract_memories(
        self,
        *,
        user_message: str,
        assistant_text: str,
        thread_id: str,
        source_message_id: str,
        sender_id: str | None,
    ) -> None:
        if self.memory_store is None and (self.mem0_memory_service is None or not self.mem0_memory_service.is_ready):
            return

        try:
            # Pre-filter: skip trivial messages to avoid unnecessary LLM calls
            text = (user_message or "").strip()
            if not text:
                return
            if self.memory_extractor is not None and not self.memory_extractor.may_contain_durable_memory(text):
                logger.info(
                    "event=memory_extraction_skipped reason=durability_heuristic "
                    "thread_id=%s source_message_id=%s 说明=跳过记忆抽取，内容不满足持久记忆条件",
                    thread_id,
                    source_message_id,
                )
                return

            # Primary path: mem0 infer=True (extraction + dedup + conflict resolution)
            if self.mem0_memory_service is not None and self.mem0_memory_service.is_ready:
                logger.info(
                    "event=memory_write_started target=mem0_infer thread_id=%s source_message_id=%s "
                    "说明=开始 mem0 infer=True 写入",
                    thread_id,
                    source_message_id,
                )
                outcome = await asyncio.to_thread(
                    self.mem0_memory_service.add_conversation,
                    user_message=user_message,
                    assistant_message=assistant_text,
                    user_id=sender_id or "unknown",
                    run_id=thread_id,
                )
                if outcome.get("ok"):
                    logger.info(
                        "event=memory_write_completed target=mem0_infer thread_id=%s source_message_id=%s "
                        "说明=mem0 infer=True 写入成功",
                        thread_id,
                        source_message_id,
                    )
                    self.react_logger.record(
                        "memory_write",
                        thread_id=thread_id,
                        source_message_id=source_message_id,
                        target="mem0_infer",
                    )
                    return
                logger.warning(
                    "event=memory_write_failed target=mem0_infer thread_id=%s source_message_id=%s error=%s "
                    "说明=mem0 infer=True 写入失败，回退规则提取",
                    thread_id,
                    source_message_id,
                    outcome.get("error"),
                )

            # Fallback: rule-based extraction → MemoryStore
            if self.memory_extractor is None or self.memory_store is None:
                return
            candidates = self.memory_extractor.extract(
                user_message=user_message,
                assistant_message=assistant_text,
                thread_id=thread_id,
                message_id=source_message_id,
                sender_id=sender_id,
            )
            if not candidates:
                logger.info(
                    "event=memory_extraction_completed thread_id=%s source_message_id=%s candidate_count=0 "
                    "说明=规则提取未命中任何记忆",
                    thread_id,
                    source_message_id,
                )
                return
            for candidate in candidates:
                self.memory_store.add_item(
                    kind=candidate.kind,
                    title=candidate.title,
                    content=candidate.content,
                    confidence=candidate.confidence,
                    importance=candidate.importance,
                    source_thread_id=candidate.source_thread_id,
                    source_message_id=candidate.source_message_id,
                    source_sender_id=candidate.source_sender_id,
                )
            logger.info(
                "event=memory_write_completed target=local_store thread_id=%s source_message_id=%s memory_count=%s "
                "说明=规则提取回退写入本地存储",
                thread_id,
                source_message_id,
                len(candidates),
            )
        except Exception as exc:
            logger.warning("Memory extraction failed: %s", exc, exc_info=True)

    @classmethod
    def build_for_test(
        cls,
        workspace_dir: Path,
        provider_manager,
        *,
        mem0_memory_service: Mem0MemoryService | None = None,
        memory_store: MemoryStore | None = None,
        runtime_services: RuntimeServices | None = None,
        enable_shell: bool = False,
        shell_requires_confirmation: bool = True,
        trace_client=None,
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
            identity_store=IdentityStore(workspace / "agent.db", workspace / "SOUL.md"),
            profile_store=ProfileStore(workspace / "agent.db", workspace / "PROFILE.md"),
            session_archive=SessionArchive(workspace / "agent.db"),
            session_manager=SessionManager(workspace / "agent.db"),
            skill_loader=SkillLoader(workspace / "skills"),
            ledger_store=LedgerStore(workspace / "agent.db"),
            note_store=NoteStore(workspace / "agent.db"),
            mem0_memory_service=mem0_memory_service,
            memory_store=memory_store,
            memory_extractor=MemoryExtractor(),
            runtime_services=runtime_services,
            enable_shell=enable_shell,
            shell_requires_confirmation=shell_requires_confirmation,
            trace_client=trace_client,
        )
