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
from pathlib import Path
from uuid import uuid4

from agent.core.context import ContextAssembler
from agent.core.provider import LLMRequest
from agent.memory import AlwaysOnMemory, SessionArchive
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
        max_iterations: int = 8,
        system_prompt: str = "You are Atlas.",
    ) -> None:
        # 这些依赖都在 build_app 或测试工厂中注入；
        # AgentCore 自己不负责创建它们，只负责调度它们协作。
        self.workspace_dir = Path(workspace_dir)
        self.provider_manager = provider_manager
        self.always_on_memory = always_on_memory
        self.session_archive = session_archive
        self.session_manager = session_manager
        self.skill_loader = skill_loader
        self.max_iterations = max_iterations
        self.context_assembler = ContextAssembler(system_prompt=system_prompt)
        self.tool_registry = build_stage1_registry(
            workspace_dir=self.workspace_dir,
            always_on_memory=self.always_on_memory,
            session_archive=self.session_archive,
            skill_loader=self.skill_loader,
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

        thread_id = message.session_id
        run_id = message.metadata.get("run_id") or message.message_id
        yield RunStartedEvent(thread_id=thread_id, run_id=run_id)

        try:
            session = await self.session_manager.get_or_create(message.session_id, channel=message.channel)
            command = message.metadata.get("command") or {}
            if command:
                async for event in self._resume_from_command(
                    session,
                    thread_id=thread_id,
                    run_id=run_id,
                    command=command,
                    runtime_control=runtime_control,
                    approval_store=approval_store,
                ):
                    yield event
                return

            context = self.context_assembler.assemble(
                soul_text=self.always_on_memory.load_soul(),
                memory_text=self.always_on_memory.load_memory(),
                skill_index=self.skill_loader.get_index(),
                history=session.history,
                user_message=message.body,
            )
            # 当前用户消息既要进入本次模型上下文，也要落进会话历史。
            session.append({"id": message.message_id, "role": "user", "content": message.body})

            async for event in self._run_loop(
                session,
                context,
                thread_id=thread_id,
                run_id=run_id,
                runtime_control=runtime_control,
                approval_store=approval_store,
            ):
                yield event
        except Exception as exc:
            code = "RunInterrupted" if isinstance(exc, RunInterrupted) else type(exc).__name__
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
        runtime_control: RunControl | None,
        approval_store: PendingApprovalStore | None,
    ):
        for index in range(self.max_iterations):
            step_name = f"iteration-{index + 1}"
            self._ensure_active(runtime_control)
            yield StepStartedEvent(step_name=step_name)
            response = await self.provider_manager.call(
                LLMRequest(messages=context, tools=self.tool_registry.get_schemas())
            )
            self._ensure_active(runtime_control)
            if response.type == "text":
                assistant_text = response.text or ""
                assistant_message_id = str(uuid4())
                yield AssistantTextStartEvent(message_id=assistant_message_id)
                if assistant_text:
                    yield AssistantTextDeltaEvent(message_id=assistant_message_id, delta=assistant_text)
                yield AssistantTextEndEvent(message_id=assistant_message_id)
                session.append({"id": assistant_message_id, "role": "assistant", "content": assistant_text})
                self.session_archive.persist_session(session)
                yield StepFinishedEvent(step_name=step_name)
                yield RunFinishedEvent(thread_id=thread_id, run_id=run_id, result_text=assistant_text)
                return

            assistant_message_id = str(uuid4())
            if response.text:
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
                approval_store=approval_store,
            ):
                if isinstance(event, CustomEvent):
                    interrupted = True
                yield event

            yield StepFinishedEvent(step_name=step_name)
            if interrupted:
                yield RunFinishedEvent(thread_id=thread_id, run_id=run_id, result_text="")
                return

        yield RunErrorEvent(message="max iterations exceeded")

    async def _execute_tool_calls(
        self,
        session,
        context: list[dict],
        *,
        tool_calls: list[dict],
        parent_message_id: str,
        thread_id: str,
        run_id: str,
        approval_store: PendingApprovalStore | None,
    ):
        for tool_call in tool_calls:
            if approval_store is not None and self._requires_approval(tool_call["name"]):
                approval = approval_store.create(
                    thread_id=thread_id,
                    run_id=run_id,
                    tool_call=tool_call,
                    context=context,
                    message=f"Approval required for {tool_call['name']}",
                )
                self.session_archive.persist_session(session)
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
            yield ToolCallStartEvent(
                tool_call_id=tool_call["id"],
                tool_call_name=tool_call["name"],
                parent_message_id=parent_message_id,
            )
            yield ToolCallArgsEvent(tool_call_id=tool_call["id"], delta=args_json)

            result = self.tool_executor.execute(tool_call["name"], tool_call["input"])
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
            session.append(tool_message)
            context.append(tool_message)

    async def _resume_from_command(
        self,
        session,
        *,
        thread_id: str,
        run_id: str,
        command: dict,
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
            runtime_control=runtime_control,
            approval_store=approval_store,
        ):
            yield event

    def _ensure_active(self, runtime_control: RunControl | None) -> None:
        if runtime_control is not None:
            runtime_control.ensure_active()

    def _requires_approval(self, tool_name: str) -> bool:
        return tool_name in {"file_write", "memory_write"}

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
            session_archive=SessionArchive(workspace / "sessions.db"),
            session_manager=SessionManager(workspace / "sessions.db"),
            skill_loader=SkillLoader(workspace / "skills"),
        )
