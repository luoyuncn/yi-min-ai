"""应用装配层。

如果把 `AgentCore` 看成发动机，
那这个文件就是把配置、Provider、Memory、Session、Skill 全部装配好的地方。
CLI、未来的 Feishu、甚至后续 Web 入口，都应该从这里拿到同一种应用对象。
"""

import asyncio
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from agent.config import load_settings
from agent.core.loop import AgentCore
from agent.core.provider import LLMResponse, ProviderConfig
from agent.core.provider_manager import ProviderManager
from agent.gateway.normalizer import NormalizedMessage
from agent.memory import AlwaysOnMemory, SessionArchive
from agent.session import SessionManager
from agent.skills import SkillLoader


class AgentApplication:
    """面向入口层的应用对象。"""

    def __init__(self, core: AgentCore) -> None:
        self.core = core

    def handle_text(self, text: str, session_id: str) -> str:
        """把一段文本包装成标准消息，再交给核心循环处理。"""

        message = NormalizedMessage(
            message_id=str(uuid4()),
            session_id=session_id,
            sender="cli-user",
            body=text,
            attachments=[],
            channel="cli",
            metadata={},
        )
        return asyncio.run(self.core.run(message))


class _TestingProviderManager:
    """测试模式下的伪 Provider。

    它的意义不是模拟真实模型智能，
    而是稳定地复现两个关键路径：
    1. 普通文本回复
    2. 工具调用 -> 工具结果 -> 最终回复
    """

    async def call(self, request):
        if any(message["role"] == "tool" for message in request.messages):
            return LLMResponse(type="text", text="已读取文件")

        user_messages = [message["content"] for message in request.messages if message["role"] == "user"]
        latest_user = user_messages[-1] if user_messages else ""
        if latest_user.startswith("读取 "):
            path = latest_user.split(maxsplit=1)[1]
            return LLMResponse(
                type="tool_calls",
                tool_calls=[{"id": "testing-tool-1", "name": "file_read", "input": {"path": path}}],
            )

        return LLMResponse(type="text", text="测试模式响应")


def build_app(config_path: Path, testing: bool = False) -> AgentApplication:
    """按配置构建一个可运行的应用实例。"""

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
        provider_manager = _build_provider_manager(settings)

    core = AgentCore(
        workspace_dir=workspace_dir,
        provider_manager=provider_manager,
        always_on_memory=AlwaysOnMemory(workspace_dir / "SOUL.md", workspace_dir / "MEMORY.md"),
        session_archive=SessionArchive(workspace_dir / "sessions.db"),
        session_manager=SessionManager(workspace_dir / "sessions.db"),
        skill_loader=SkillLoader(workspace_dir / "skills"),
        max_iterations=settings.agent.max_iterations,
        system_prompt=f"You are {settings.agent.name}.",
    )
    return AgentApplication(core)


def _build_provider_manager(settings) -> ProviderManager:
    """根据配置注册真实 Provider。"""

    manager = ProviderManager()
    primary = settings.providers.default_primary
    primary_item = next(item for item in settings.providers.items if item.name == primary)

    async def _register() -> ProviderManager:
        # 当前运行时只会调用 primary provider。
        # 在真正实现多 provider fallback 之前，不应因为未启用的 provider 缺少密钥而阻塞启动。
        await manager.register(
            ProviderConfig(
                name=primary_item.name,
                provider_type=primary_item.provider_type,
                model=primary_item.model,
                api_key_env=primary_item.api_key_env,
                base_url=primary_item.base_url,
            ),
            make_primary=True,
        )
        return manager

    return asyncio.run(_register())


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
