"""一期 ReAct 核心循环。

阅读这个文件时，可以把它理解成整个阶段一的“调度中心”：
1. 取会话
2. 拼上下文
3. 调模型
4. 如果模型要用工具，就执行工具再继续
5. 如果模型直接回复，就归档并返回
"""

import asyncio
from pathlib import Path

from agent.core.context import ContextAssembler
from agent.core.provider import LLMRequest
from agent.memory import AlwaysOnMemory, SessionArchive
from agent.session import SessionManager
from agent.skills import SkillLoader
from agent.tools import ToolExecutor, build_stage1_registry


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

        session = await self.session_manager.get_or_create(message.session_id, channel=message.channel)
        context = self.context_assembler.assemble(
            soul_text=self.always_on_memory.load_soul(),
            memory_text=self.always_on_memory.load_memory(),
            skill_index=self.skill_loader.get_index(),
            history=session.history,
            user_message=message.body,
        )
        # 当前用户消息既要进入本次模型上下文，也要落进会话历史。
        session.append({"role": "user", "content": message.body})

        for _ in range(self.max_iterations):
            response = await self.provider_manager.call(
                LLMRequest(messages=context, tools=self.tool_registry.get_schemas())
            )
            if response.type == "text":
                # 文本回复意味着这一轮已经收束，可以持久化后直接返回。
                session.append({"role": "assistant", "content": response.text or ""})
                self.session_archive.persist_session(session)
                return response.text or ""

            # 如果模型决定调用工具，我们先把 assistant 的工具意图记下来，
            # 再逐个执行工具，并把工具结果追加回上下文。
            assistant_message = {
                "role": "assistant",
                "content": response.text or "",
                "tool_calls": response.tool_calls or [],
            }
            session.append(assistant_message)
            context.append(assistant_message)

            for tool_call in response.tool_calls or []:
                result = self.tool_executor.execute(tool_call["name"], tool_call["input"])
                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": result,
                }
                session.append(tool_message)
                context.append(tool_message)

        raise RuntimeError("max iterations exceeded")

    def run_sync(self, message) -> str:
        """给同步调用方（例如 CLI）提供一个方便入口。"""

        return asyncio.run(self.run(message))

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
