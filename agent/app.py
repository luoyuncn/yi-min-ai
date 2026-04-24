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
from agent.core.llm_factory import LLMFactory
from agent.core.provider import LLMResponse
from agent.core.provider_manager import ProviderManager
from agent.gateway.normalizer import NormalizedMessage
from agent.memory import AlwaysOnMemory, LedgerStore, NoteStore, SessionArchive
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
    return await _build_app_from_settings_async(
        settings,
        workspace_dir=settings.agent.workspace_dir,
        testing=testing,
    )


async def build_channel_apps_async(
    config_path: Path,
    testing: bool = False,
) -> tuple[object, dict[str, AgentApplication]]:
    """按渠道实例配置构建多个 AgentApplication。"""

    config_path = config_path.resolve()
    _load_environment_files(config_path)
    settings = load_settings(config_path)

    if settings.channels and settings.channels.instances:
        apps: dict[str, AgentApplication] = {}
        for instance in settings.channels.instances:
            apps[instance.name] = await _build_app_from_settings_async(
                settings,
                workspace_dir=instance.workspace_dir,
                testing=testing,
            )
        return settings, apps

    return settings, {
        "default": await _build_app_from_settings_async(
            settings,
            workspace_dir=settings.agent.workspace_dir,
            testing=testing,
        )
    }


async def _build_app_from_settings_async(settings, *, workspace_dir: Path, testing: bool = False) -> AgentApplication:
    """按已加载的 Settings 与 workspace 构建应用实例。"""

    workspace_dir = Path(workspace_dir).resolve()
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

    db_path = workspace_dir / "agent.db"
    session_archive = SessionArchive(db_path)
    core = AgentCore(
        workspace_dir=workspace_dir,
        provider_manager=provider_manager,
        always_on_memory=AlwaysOnMemory(workspace_dir / "SOUL.md", workspace_dir / "MEMORY.md"),
        session_archive=session_archive,
        session_manager=SessionManager(db_path, archive=session_archive),
        skill_loader=SkillLoader(workspace_dir / "skills"),
        ledger_store=LedgerStore(db_path),
        note_store=NoteStore(db_path),
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


def _build_provider_manager(settings, **llm_overrides) -> ProviderManager:
    """根据配置注册真实 Provider（同步包装）。"""

    return asyncio.run(_build_provider_manager_async(settings, **llm_overrides))


async def _build_provider_manager_async(settings, **llm_overrides) -> ProviderManager:
    """根据配置注册真实 Provider。"""

    manager = ProviderManager()

    # 当前运行时只会调用 primary provider。
    # 在真正实现多 provider fallback 之前，不应因为未启用的 provider 缺少密钥而阻塞启动。
    await manager.register(
        LLMFactory.create_primary(settings, **llm_overrides),
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
    _ensure_default_skills(workspace_dir / "skills")


def _ensure_default_skills(skills_dir: Path) -> None:
    """确保新 workspace 自带基础业务 skill 模板。"""

    defaults = {
        "bookkeeping": (
            "---\n"
            "name: bookkeeping\n"
            "description: Proactively use ledger tools for bookkeeping, ask follow-up questions, and commit only after required fields are complete.\n"
            "---\n"
            "# Bookkeeping\n"
            "\n"
            "- If the user expresses income, expense, reimbursement, transfer, budget, or asks for bookkeeping statistics, treat it as a bookkeeping workflow.\n"
            "- Use `ledger_upsert_draft` to save any partially known ledger fields.\n"
            "- Ask follow-up questions when direction, amount, or occurrence time is still unclear.\n"
            "- Only call `ledger_commit_draft` after required fields are complete.\n"
            "- Use `ledger_query_entries` and `ledger_summary` for reporting.\n"
            "- Do not commit guessed values. Clarify ambiguity first.\n"
            "- Prefer ledger tools over `memory_write` or arbitrary files for bookkeeping facts.\n"
            "- Example triggers: `今天午饭 32`, `帮我记一笔报销 120`, `这个月餐饮花了多少`.\n"
        ),
        "note-taking": (
            "---\n"
            "name: note-taking\n"
            "description: Proactively save explicit remember requests and durable user facts as structured notes.\n"
            "---\n"
            "# Note Taking\n"
            "\n"
            "- Always save when the user explicitly asks to remember something.\n"
            "- Auto-save only durable facts such as preferences, plans, constraints, and contacts.\n"
            "- Use `note_add` for new facts, `note_update` when a saved fact is corrected, and `note_search` before duplicating.\n"
            "- Give a short acknowledgement for explicit saves and important long-lived notes.\n"
            "- Search existing notes before creating a new one.\n"
            "- Do not auto-save one-off small talk, temporary emotions, or weak guesses.\n"
            "- Prefer note tools over `memory_write` when saving long-lived user facts.\n"
            "- Example durable facts: `我乳糖不耐受`, `以后默认中文回答`, `我更喜欢美式`, `六月计划去日本`.\n"
        ),
    }

    for skill_name, content in defaults.items():
        target = skills_dir / skill_name / "SKILL.md"
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
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
            "[TOOL ROUTING POLICY]",
            "Use ledger tools for bookkeeping requests involving income, expense, reimbursement, transfer, and spending summaries.",
            "Ask follow-up questions before committing incomplete ledger entries.",
            "Use note tools for long-lived user facts such as preferences, plans, constraints, profile facts, and important contacts.",
            "Always save explicit remember requests as notes, and proactively save durable facts when confidence is high.",
            "Search existing notes before creating duplicate notes, and update notes when the user corrects an earlier fact.",
            "Do not store bookkeeping or note facts in MEMORY.md or arbitrary files unless the user explicitly asks for that format.",
            "Give a short acknowledgement for explicit saves and important automatic note saves; otherwise keep auto-save quiet.",
            f"Process boot local datetime: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        ]
    )
