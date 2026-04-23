"""应用装配层。

如果把 `AgentCore` 看成发动机，
那这个文件就是把配置、Provider、Memory、Session、Skill 全部装配好的地方。
CLI、未来的 Feishu、甚至后续 Web 入口，都应该从这里拿到同一种应用对象。
"""

import asyncio
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from agent.config import load_settings
from agent.core.loop import AgentCore
from agent.core.provider import LLMResponse, ProviderConfig
from agent.core.provider_manager import ProviderManager
from agent.gateway.normalizer import NormalizedMessage
from agent.memory import AlwaysOnMemory, SessionArchive
from agent.memory.mflow_bridge import MflowBridge
from agent.session import SessionManager
from agent.skills import SkillLoader


class AgentApplication:
    """面向入口层的应用对象。"""

    def __init__(self, core: AgentCore) -> None:
        self.core = core

    def handle_text(self, text: str, session_id: str) -> str:
        """把一段文本包装成标准消息，再交给核心循环处理。"""

        return asyncio.run(self.handle_text_async(text, session_id=session_id))

    async def handle_text_async(self, text: str, session_id: str, channel: str = "cli") -> str:
        """给异步入口（例如 Web）提供同一套处理逻辑。"""

        return await self.core.run(self._build_message(text, session_id=session_id, channel=channel))

    async def stream_events(
        self,
        text: str,
        session_id: str,
        *,
        sender: str = "web-user",
        channel: str = "web",
        metadata: dict | None = None,
        runtime_control=None,
        approval_store=None,
    ):
        """把一次文本输入转换成 runtime event 流。"""

        message = self._build_message(
            text,
            session_id=session_id,
            sender=sender,
            channel=channel,
            metadata=metadata,
        )
        async for event in self.core.run_events(
            message,
            runtime_control=runtime_control,
            approval_store=approval_store,
        ):
            yield event

    def _build_message(
        self,
        text: str,
        *,
        session_id: str,
        sender: str = "cli-user",
        channel: str = "cli",
        metadata: dict | None = None,
    ) -> NormalizedMessage:
        return NormalizedMessage(
            message_id=str(uuid4()),
            session_id=session_id,
            sender=sender,
            body=text,
            attachments=[],
            channel=channel,
            metadata=metadata or {},
        )


class _TestingProviderManager:
    """测试模式下的伪 Provider。

    它的意义不是模拟真实模型智能，
    而是稳定地复现两个关键路径：
    1. 普通文本回复
    2. 工具调用 -> 工具结果 -> 最终回复
    """

    async def call(self, request):
        if any(message["role"] == "tool" for message in request.messages):
            return LLMResponse(type="text", text="已处理工具结果")

        user_messages = [message["content"] for message in request.messages if message["role"] == "user"]
        latest_user = user_messages[-1] if user_messages else ""
        if latest_user.startswith("读取 "):
            path = latest_user.split(maxsplit=1)[1]
            return LLMResponse(
                type="tool_calls",
                tool_calls=[{"id": "testing-tool-1", "name": "file_read", "input": {"path": path}}],
            )
        if latest_user.startswith("写入 "):
            path = latest_user.split(maxsplit=1)[1]
            return LLMResponse(
                type="tool_calls",
                text="准备写文件",
                tool_calls=[
                    {
                        "id": "testing-tool-2",
                        "name": "file_write",
                        "input": {"path": path, "content": "hello from testing approval"},
                    }
                ],
            )

        return LLMResponse(type="text", text="测试模式响应")


async def build_app_async(config_path: Path, testing: bool = False) -> AgentApplication:
    """按配置构建一个可运行的应用实例（异步版本）。"""

    config_path = config_path.resolve()
    _load_environment_files(config_path)
    settings = load_settings(config_path)
    workspace_dir = settings.agent.workspace_dir
    workspace_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "skills").mkdir(parents=True, exist_ok=True)
    _ensure_workspace_files(workspace_dir)

    if testing:
        # 测试模式下不依赖真实外部 API，确保阶段一在无密钥环境里也能完整演示。
        provider_manager = _TestingProviderManager()
    else:
        provider_manager = await _build_provider_manager_async(settings)

    # 初始化 M-flow（可选，失败不阻塞启动）
    mflow_bridge = None
    try:
        mflow_data_dir = workspace_dir.parent / "mflow_data"
        mflow_bridge = MflowBridge(data_dir=mflow_data_dir)
        await mflow_bridge.initialize()
    except Exception as e:
        print(f"Warning: M-flow initialization failed: {e}")

    session_archive = SessionArchive(workspace_dir / "sessions.db")
    core = AgentCore(
        workspace_dir=workspace_dir,
        provider_manager=provider_manager,
        always_on_memory=AlwaysOnMemory(workspace_dir / "SOUL.md", workspace_dir / "MEMORY.md"),
        session_archive=session_archive,
        session_manager=SessionManager(workspace_dir / "sessions.db", archive=session_archive),
        skill_loader=SkillLoader(workspace_dir / "skills"),
        mflow_bridge=mflow_bridge,
        max_iterations=settings.agent.max_iterations,
        system_prompt=_build_system_prompt(settings.agent.name),
    )
    return AgentApplication(core)


def build_app(config_path: Path, testing: bool = False) -> AgentApplication:
    """按配置构建一个可运行的应用实例（同步包装）。"""
    try:
        loop = asyncio.get_running_loop()
        # 如果在运行中的事件循环里，直接报错提示使用异步版本
        raise RuntimeError(
            "build_app() cannot be called from a running event loop. "
            "Use build_app_async() instead."
        )
    except RuntimeError as e:
        if "no running event loop" in str(e).lower():
            # 没有运行中的事件循环，可以安全地使用 asyncio.run()
            return asyncio.run(build_app_async(config_path, testing))
        else:
            # 其他 RuntimeError，重新抛出
            raise


def _build_provider_manager(settings) -> ProviderManager:
    """根据配置注册真实 Provider（同步包装）。"""

    return asyncio.run(_build_provider_manager_async(settings))


async def _build_provider_manager_async(settings) -> ProviderManager:
    """根据配置注册真实 Provider。"""

    manager = ProviderManager()
    primary = settings.providers.default_primary
    primary_item = next(item for item in settings.providers.items if item.name == primary)

    # 当前运行时只会调用 primary provider。
    # 在真正实现多 provider fallback 之前，不应因为未启用的 provider 缺少密钥而阻塞启动。
    await manager.register(
        ProviderConfig(
            name=primary_item.name,
            provider_type=primary_item.provider_type,
            model=primary_item.model,
            api_key_env=primary_item.api_key_env,
            base_url=primary_item.base_url,
            extra_body=primary_item.extra_body,
        ),
        make_primary=True,
    )
    return manager


def _load_environment_files(config_path: Path) -> None:
    """加载与当前配置相关的 .env 文件。

    优先读取配置目录下的 `.env`，再补充当前工作目录下的 `.env`。
    已存在于进程环境中的变量保持不变。
    """

    candidates = [config_path.parent / ".env", Path.cwd() / ".env"]
    loaded: set[Path] = set()

    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in loaded or not resolved.exists():
            continue
        load_dotenv(resolved, override=False)
        loaded.add(resolved)


def _ensure_workspace_files(workspace_dir: Path) -> None:
    """确保工作区里至少有基础人格和记忆文件。"""

    defaults = {
        "SOUL.md": "# Identity\n你是我的个人助理，名字叫 Atlas。\n",
        "MEMORY.md": "# User Profile\n",
    }
    for filename, content in defaults.items():
        target = workspace_dir / filename
        if not target.exists():
            target.write_text(content, encoding="utf-8")


def _build_system_prompt(agent_name: str) -> str:
    """构建基础系统提示词。"""

    now = datetime.now().astimezone()
    return "\n".join(
        [
            f"You are {agent_name}.",
            "Use the provided system time as the source of truth for dates, times, and years.",
            (
                "When the user asks to record, summarize, or reason about time-sensitive facts "
                "such as ledger entries, always anchor your answer to the current local date."
            ),
            f"Process boot local datetime: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        ]
    )
