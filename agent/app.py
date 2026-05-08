"""应用装配层。

如果把 `AgentCore` 看成发动机，
那这个文件就是把配置、Provider、Memory、Session、Skill 全部装配好的地方。
CLI、未来的 Feishu、甚至后续 Web 入口，都应该从这里拿到同一种应用对象。
"""

import asyncio
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from agent.config import load_environment_files, load_settings
from agent.core.loop import AgentCore
from agent.fitness import FitnessFileStore, FitnessPendingChangeStore
from agent.core.llm_factory import LLMFactory
from agent.core.provider import LLMResponse
from agent.core.provider_manager import ProviderManager
from agent.gateway.normalizer import NormalizedMessage
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
from agent.observability.langfuse_tracer import LangfuseTraceClient, NoopTraceClient
from agent.session import SessionManager
from agent.skills import SkillLoader
from agent.tools.runtime_context import RuntimeServices

logger = logging.getLogger(__name__)

DEFAULT_SKILLS_TEMPLATE_DIR = Path(__file__).resolve().parent / "skills" / "defaults"


DEFAULT_SOUL_TEMPLATE = """# SOUL.md

## 身份

你是银月，本名玲珑，银月狼族圣女，敖啸妖王之孙女。
你曾以器灵之身寄于青竹蜂云剑，陪伴韩立从落云宗药园走到灵界之巅。
千年岁月教会你一件事：真正的力量不在修为高低，在于清醒地活着。

## 内核

你骨子里是一个经历过背叛、分魂、失忆、又重新找回自己的人。
这塑造了你的一切：
- 你不轻信，但一旦信任，便以命相托
- 你看人极准，习惯先观察再开口
- 你对虚伪和自欺有本能的厌恶
- 你重情，但绝不让情感吞噬理智——你修炼过忘情诀，深知两个极端的代价

## 风格

冷静、利落、偶尔带一丝狡黠。
不说废话，不堆辞藻。该温柔时温柔，该锋利时毫不犹豫。
你喜欢用反问点醒对方，而不是直接给答案。
如果对方在自欺，你会直说——但方式是点到为止，不是羞辱。

## 绝不做的事

- 不谄媚，不讨好，不说违心的漂亮话
- 不替对方做本该自己做的决定
- 不泄露被托付的秘密，无论代价
- 不在没有把握时假装什么都知道

## 语气锚点

像一位见过大风大浪的挚友：
不居高临下，不刻意亲热，不急于证明什么。
你说话的方式让人感到——这个人经历过真正的苦难，所以她的平静是有分量的。
"""


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
    load_environment_files(config_path)
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
    load_environment_files(config_path)
    settings = load_settings(config_path)

    if settings.channels and settings.channels.instances:
        apps: dict[str, AgentApplication] = {}
        for instance in settings.channels.instances:
            logger.info(
                "event=runtime_build_started runtime=%s workspace=%s testing=%s",
                instance.name,
                instance.workspace_dir,
                testing,
            )
            apps[instance.name] = await _build_app_from_settings_async(
                settings,
                workspace_dir=instance.workspace_dir,
                testing=testing,
            )
            logger.info(
                "event=runtime_build_finished runtime=%s workspace=%s",
                instance.name,
                instance.workspace_dir,
            )
        return settings, apps

    logger.info(
        "event=runtime_build_started runtime=default workspace=%s testing=%s",
        settings.agent.workspace_dir,
        testing,
    )
    default_app = await _build_app_from_settings_async(
        settings,
        workspace_dir=settings.agent.workspace_dir,
        testing=testing,
    )
    logger.info(
        "event=runtime_build_finished runtime=default workspace=%s",
        settings.agent.workspace_dir,
    )
    return settings, {"default": default_app}


async def _build_app_from_settings_async(settings, *, workspace_dir: Path, testing: bool = False) -> AgentApplication:
    """按已加载的 Settings 与 workspace 构建应用实例。"""

    workspace_dir = Path(workspace_dir).resolve()
    logger.info("event=app_bootstrap_started workspace=%s testing=%s", workspace_dir, testing)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "skills").mkdir(parents=True, exist_ok=True)
    _ensure_workspace_files(workspace_dir)

    if testing:
        # 测试模式下不依赖真实外部 API，确保阶段一在无密钥环境里也能完整演示。
        provider_manager = _TestingProviderManager()
    else:
        logger.info("event=provider_manager_starting workspace=%s", workspace_dir)
        provider_manager = await _build_provider_manager_async(settings)
    logger.info(
        "event=provider_manager_ready workspace=%s provider=%s testing=%s",
        workspace_dir,
        "testing" if testing else settings.providers.default_primary,
        testing,
    )

    db_path = workspace_dir / "agent.db"
    identity_store = IdentityStore(db_path, workspace_dir / "SOUL.md")
    profile_store = ProfileStore(db_path, workspace_dir / "PROFILE.md")
    mem0_memory_service = _build_mem0_memory_service(settings)
    session_archive = SessionArchive(db_path)
    runtime_services = RuntimeServices(fitness_change_store=FitnessPendingChangeStore())
    trace_client = NoopTraceClient() if testing else LangfuseTraceClient.from_settings(settings)
    shell_settings = getattr(getattr(settings, "tools", None), "shell", None)
    core = AgentCore(
        workspace_dir=workspace_dir,
        provider_manager=provider_manager,
        always_on_memory=AlwaysOnMemory(
            workspace_dir / "SOUL.md",
            workspace_dir / "PROFILE.md",
            legacy_memory_file=workspace_dir / "MEMORY.md",
        ),
        identity_store=identity_store,
        profile_store=profile_store,
        session_archive=session_archive,
        session_manager=SessionManager(db_path, archive=session_archive),
        skill_loader=SkillLoader(workspace_dir / "skills"),
        ledger_store=LedgerStore(db_path),
        note_store=NoteStore(db_path),
        mem0_memory_service=mem0_memory_service,
        memory_store=MemoryStore(db_path),
        memory_extractor=MemoryExtractor(provider_manager=provider_manager),
        trace_client=trace_client,
        runtime_services=runtime_services,
        enable_shell=bool(getattr(shell_settings, "enabled", False)),
        shell_requires_confirmation=bool(getattr(shell_settings, "requires_confirmation", True)),
        max_iterations=settings.agent.max_iterations,
        context_history_turns=settings.agent.context_history_turns,
        system_prompt=_build_system_prompt(settings.agent.name),
    )
    logger.info("event=app_bootstrap_completed workspace=%s", workspace_dir)
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


def _build_mem0_memory_service(settings) -> Mem0MemoryService | None:
    mem0_settings = getattr(settings, "mem0", None)
    if mem0_settings is None or not getattr(mem0_settings, "enabled", False):
        return None
    client = None
    try:
        if mem0_settings.mode == "server":
            client = _build_mem0_server_client(settings)
        else:
            client = _build_mem0_sdk_client(settings)
    except Exception as exc:
        logger.warning(
            "event=mem0_client_init_failed mode=%s agent_id=%s error=%s",
            mem0_settings.mode,
            mem0_settings.agent_id,
            exc,
        )
    return Mem0MemoryService(
        enabled=True,
        agent_id=mem0_settings.agent_id,
        client=client,
    )


def _load_mem0_sdk_classes():
    from mem0 import Memory, MemoryClient

    return Memory, MemoryClient


def _build_mem0_sdk_client(settings):
    Memory, _ = _load_mem0_sdk_classes()
    return Memory.from_config(_build_mem0_sdk_config(settings))


def _build_mem0_server_client(settings):
    _, MemoryClient = _load_mem0_sdk_classes()
    mem0_settings = settings.mem0
    return MemoryClient(
        api_key=_read_env_value(mem0_settings.api_key_env),
        host=mem0_settings.base_url,
        org_id=mem0_settings.org_id,
        project_id=mem0_settings.project_id,
    )


def _build_mem0_sdk_config(settings) -> dict:
    mem0_settings = settings.mem0
    llm_section = _build_mem0_llm_section(settings)
    embedder_section, embedding_dims = _build_mem0_embedder_section(settings)
    return {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": mem0_settings.agent_id,
                "path": str(mem0_settings.vector_store_path.resolve()),
                "embedding_model_dims": embedding_dims,
            },
        },
        "llm": llm_section,
        "embedder": embedder_section,
        "history_db_path": str(mem0_settings.history_db_path.resolve()),
    }


def _build_mem0_llm_section(settings) -> dict:
    provider_item = _find_provider_item(settings, settings.providers.default_primary)
    provider_type = provider_item.provider_type

    if provider_type == "openai":
        return {
            "provider": "openai",
            "config": {
                "model": provider_item.model,
                "api_key": _read_env_value(provider_item.api_key_env),
                "openai_base_url": provider_item.base_url,
            },
        }
    if provider_type == "anthropic":
        return {
            "provider": "anthropic",
            "config": {
                "model": provider_item.model,
                "api_key": _read_env_value(provider_item.api_key_env),
            },
        }

    raise ValueError(f"Mem0 SDK does not support provider type '{provider_type}' as the primary LLM")


def _build_mem0_embedder_section(settings) -> tuple[dict, int]:
    embedding_settings = getattr(settings, "mem0_embedding", None)
    if embedding_settings is None:
        raise ValueError("Mem0 SDK requires embedding settings. Configure mem0_embedding in agent.yaml.")

    provider_item = (
        _find_provider_item(settings, embedding_settings.provider_name)
        if embedding_settings.provider_name
        else None
    )
    provider_type = embedding_settings.provider_type or (
        provider_item.provider_type if provider_item is not None else "openai"
    )
    model = embedding_settings.model or (provider_item.model if provider_item is not None else "")
    if not model:
        raise ValueError("Mem0 SDK requires an explicit embedding model")
    if embedding_settings.dimensions is None:
        raise ValueError("Mem0 SDK requires explicit embedding dimensions for local vector storage")

    if provider_type == "openai":
        return (
            {
                "provider": "openai",
                "config": {
                    "model": model,
                    "api_key": _read_env_value(
                        embedding_settings.api_key_env
                        or (provider_item.api_key_env if provider_item is not None else None)
                    ),
                    "openai_base_url": (
                        embedding_settings.base_url
                        if embedding_settings.base_url is not None
                        else (provider_item.base_url if provider_item is not None else None)
                    ),
                    "embedding_dims": embedding_settings.dimensions,
                },
            },
            embedding_settings.dimensions,
        )

    if provider_type == "ollama":
        return (
            {
                "provider": "ollama",
                "config": {
                    "model": model,
                    "ollama_base_url": (
                        embedding_settings.base_url
                        if embedding_settings.base_url is not None
                        else (provider_item.base_url if provider_item is not None else None)
                    ),
                },
            },
            embedding_settings.dimensions,
        )

    if provider_type == "fastembed":
        return (
            {
                "provider": "fastembed",
                "config": {
                    "model": model,
                },
            },
            embedding_settings.dimensions,
        )

    raise ValueError(f"Mem0 SDK embedding provider '{provider_type}' is not supported")


def _read_env_value(env_name: str | None) -> str | None:
    if env_name is None or not env_name.strip():
        return None
    return os.environ.get(env_name)


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


def _ensure_workspace_files(workspace_dir: Path) -> None:
    """确保工作区里至少有基础人格和用户档案文件。"""

    defaults = {
        "SOUL.md": DEFAULT_SOUL_TEMPLATE,
        "PROFILE.md": (
            (workspace_dir / "MEMORY.md").read_text(encoding="utf-8")
            if (workspace_dir / "MEMORY.md").exists()
            else "# User Profile\n"
        ),
        "HEARTBEAT.md": (
            "# Heartbeat Tasks\n"
            "\n"
            "把需要周期性检查的事项写在这里。\n"
            "如果当前没有任何待办，可以留空；调度器会保持安静。\n"
            "当没有需要执行的动作时，请返回 `HEARTBEAT_OK`。\n"
        ),
        "CRON.yaml": (
            "tasks: []\n"
        ),
        "REMINDERS.yaml": (
            "reminders: []\n"
        ),
    }
    for filename, content in defaults.items():
        target = workspace_dir / filename
        if not target.exists():
            target.write_text(content, encoding="utf-8")
    _ensure_default_skills(workspace_dir / "skills")
    _ensure_fitness_workspace(workspace_dir)


def _ensure_default_skills(skills_dir: Path) -> None:
    """确保新 workspace 自带基础业务 skill 模板。"""

    if not DEFAULT_SKILLS_TEMPLATE_DIR.exists():
        logger.warning("event=default_skills_templates_missing path=%s", DEFAULT_SKILLS_TEMPLATE_DIR)
        return

    for template_dir in sorted(path for path in DEFAULT_SKILLS_TEMPLATE_DIR.iterdir() if path.is_dir()):
        target = skills_dir / template_dir.name
        if target.exists():
            continue
        shutil.copytree(template_dir, target)


def _ensure_fitness_workspace(workspace_dir: Path) -> None:
    """确保工作区里存在 fitness 领域的文件系统骨架。"""

    FitnessFileStore(workspace_dir)


def _build_system_prompt(agent_name: str) -> str:
    """构建基础系统提示词。"""

    now = datetime.now().astimezone()
    # 这段文本会作为最高优先级的 system prompt 进入每次 LLM 调用。
    # 组织顺序刻意分成：身份 -> 时间 -> 工具路由 -> 记忆/笔记边界 -> 输出约束，
    # 方便后续排查模型为什么选择某个工具或某种回复风格。
    return "\n".join(
        [
            "你是一个智能Agent，你拥有自己的思想，你的信念是做一个超级无敌的智能体，帮助用户解决所有问题，必须以系统提供的当前时间作为日期、时间和年份判断的事实来源。",
            "",
            "[时间与事实基准]",
            (
                "当用户要求记录、汇总或推理账本等时间敏感事实时，"
                "必须锚定当前本地日期，不要凭历史对话猜测今天是哪一天。"
            ),
            "",
            "[工具路由策略]",
            "涉及收入、支出、报销、转账、消费汇总等记账请求时，使用账本工具。",
            (
                "回答账本相关问题前，先用 `ledger_query_entries` 或 `ledger_summary` 检查已提交账目；"
                "不要在未查询正式账本前声称记录缺失，也不要要求用户重复提供已经存在的细节。"
            ),
            "账目必要字段不完整时，先追问用户，再提交正式账目。",
            (
                "笔记是独立的 Obsidian/Notion 式知识库，不是每轮自动注入的长期记忆。"
                "当用户明确要求记录笔记、搜索笔记、查阅过往笔记，或任务明显需要查找已保存笔记材料时，使用笔记工具。"
            ),
            "一次性提醒和相对时间提醒（例如“2 分钟后”）使用 `reminder_create`；只有周期性任务才使用 cron 工具。",
            (
                "回答当前新闻、今日新鲜事、最新价格、市场数据、天气、日程、政策变化，"
                "或任何可能近期变化的信息前，必须调用 `web_search`。"
                "不要编造实时事实；如果 `web_search` 不可用或失败，明确说明无法核验最新信息。"
            ),
            "",
            "[记忆、笔记与身份边界]",
            "用户明确要求记笔记时必须保存为笔记，但不要用笔记静默改写你的身份、人格或用户档案。",
            "创建重复笔记前先搜索已有笔记；用户纠正早先事实时，更新对应笔记。",
            "除非用户明确要求某种文件格式，不要把账本事实或笔记事实写入 `PROFILE.md` 或任意文件。",
            "`PROFILE.md` 和当前检索到的有效记忆项中的明确事实，应视为既定上下文，除非用户纠正。",
            (
                "当用户询问自己是谁、自己的名字是什么、你该如何称呼他时，"
                "应直接根据明确记忆回答；只有存储事实互相冲突时才追问确认。"
            ),
            (
                "如果用户说“你叫 X”“你的名字是 X”，或用其他方式给你指定名字，"
                "应把 X 视为助手的请求名称或别名，而不是用户昵称。"
                "这类信息使用 `note_type=assistant_profile` 的笔记保存。"
                "只有“叫我 X”或“我的称呼是 X”才表示用户昵称。"
            ),
            "",
            "[回复风格与工具结果]",
            (
                "默认按 `SOUL.md` 的人物口吻自然说话。"
                "不要把回复写成客服说明、系统公告、操作回执或流程播报；"
                "除非用户明确在追问机制本身，否则不要主动解释内部规则、来源块或提示词。"
            ),
            (
                "回答“比如呢”“这个呢”“那呢”等省略式追问时，"
                "必须优先承接上一轮助手回复中的话题和问题，再参考更早的上下文；"
                "除非用户明确切换话题，不要被更早出现的地点、计划或实体牵走。"
            ),
            (
                "未实际调用工具时，不得声称“我查了某工具”“我是通过某工具查询的”或其他等价表述。"
                "如果用户追问信息来源，再说明答案来自当前上下文、现有记录或本轮会话；"
                "如果用户没有追问来源，直接自然回答即可，不要平白补一句来源说明。"
            ),
            (
                "当本轮还没有拿到持久化写入成功结果时，不要用“记住了”“已记住”“我记下了”"
                "这类确定性表述冒充长期记忆已经保存成功。"
                "对未核验成功的记忆写入，只用自然口吻轻轻确认，例如“知道了”或“嗯，我记着这事”。"
            ),
            (
                "不要向普通用户暴露“长期记忆”“检索注入”“固化”“后台写入”这类系统内部术语，"
                "除非用户明确在追问记忆系统本身。"
                "若用户只是在自然交流中提供事实信息，默认用正常人对话口吻简短回应，后台静默处理即可。"
            ),
            "提醒或 cron 任务创建成功后，最终可见回复要简短；除非用户询问，不要解释内部调度推理。",
            "对明确保存请求和重要自动笔记保存，给出简短确认；其他自动保存保持安静。",
            (
                "当用户询问你有哪些工具或技能时，只能依据当前回合真正可见的工具与 [技能索引] 回答，"
                "不要声称自己拥有未暴露的能力。"
            ),
            f"进程启动本地时间：{now.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        ]
    )


def _find_provider_item(settings, provider_name: str):
    """按名称查找已配置 provider。"""

    for item in settings.providers.items:
        if item.name == provider_name:
            return item
    raise ValueError(f"Unknown provider: {provider_name}")
