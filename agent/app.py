"""应用装配层。

如果把 `AgentCore` 看成发动机，
那这个文件就是把配置、Provider、Memory、Session、Skill 全部装配好的地方。
CLI、未来的 Feishu、甚至后续 Web 入口，都应该从这里拿到同一种应用对象。
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from agent.config import load_environment_files, load_settings
from agent.core.loop import AgentCore
from agent.core.llm_factory import LLMFactory
from agent.core.provider import LLMResponse
from agent.core.provider_manager import ProviderManager
from agent.gateway.normalizer import NormalizedMessage
from agent.memory import AlwaysOnMemory, LedgerStore, MemoryExtractor, MemoryStore, NoteStore, SessionArchive
from agent.memory.mflow_bridge import (
    MflowBridge,
    MflowEmbeddingConfig,
    MflowLLMConfig,
    MflowRuntimeConfig,
)
from agent.observability.langfuse_tracer import LangfuseTraceClient, NoopTraceClient
from agent.session import SessionManager
from agent.skills import SkillLoader
from agent.tools.runtime_context import RuntimeServices

logger = logging.getLogger(__name__)


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

    # 初始化 M-flow（可选，失败不阻塞启动）
    mflow_bridge = None
    try:
        logger.info("event=mflow_bridge_starting workspace=%s", workspace_dir)
        mflow_bridge = await _build_mflow_bridge_async(settings, workspace_dir=workspace_dir)
        logger.info(
            "event=mflow_bridge_ready workspace=%s available=%s",
            workspace_dir,
            getattr(mflow_bridge, "is_available", False) if mflow_bridge is not None else False,
        )
    except Exception as e:
        logger.warning("event=mflow_bridge_failed workspace=%s error=%s", workspace_dir, e)
        print(f"Warning: M-flow initialization failed: {e}")

    db_path = workspace_dir / "agent.db"
    session_archive = SessionArchive(db_path)
    runtime_services = RuntimeServices()
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
        session_archive=session_archive,
        session_manager=SessionManager(db_path, archive=session_archive),
        skill_loader=SkillLoader(workspace_dir / "skills"),
        ledger_store=LedgerStore(db_path),
        note_store=NoteStore(db_path),
        memory_store=MemoryStore(db_path),
        memory_extractor=MemoryExtractor(provider_manager=provider_manager),
        mflow_bridge=mflow_bridge,
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


def _ensure_default_skills(skills_dir: Path) -> None:
    """确保新 workspace 自带基础业务 skill 模板。"""

    # 这些默认 skill 会直接进入模型上下文，因此用中文描述业务规则；
    # 工具名保持英文，避免影响 function calling 的精确匹配。
    defaults = {
        "bookkeeping": (
            "---\n"
            "name: bookkeeping\n"
            "description: 主动使用账本工具处理记账请求，在必要字段完整后才提交正式账目。\n"
            "---\n"
            "# 账本处理\n"
            "\n"
            "- 用户表达收入、支出、报销、转账、预算，或询问账本统计时，视为账本工作流。\n"
            "- 使用 `ledger_upsert_draft` 保存已知但尚未完整的账目字段。\n"
            "- 当收支方向、金额或发生时间仍不明确时，先追问用户。\n"
            "- 只有必要字段完整后，才调用 `ledger_commit_draft` 写入正式账本。\n"
            "- 查询和汇总账本时使用 `ledger_query_entries` 与 `ledger_summary`。\n"
            "- 不要提交猜测值；遇到歧义先澄清。\n"
            "- 账本事实优先使用账本工具，不要写入 `profile_write` 或任意文件。\n"
            "- 触发示例：`今天午饭 32`、`帮我记一笔报销 120`、`这个月餐饮花了多少`。\n"
        ),
        "note-taking": (
            "---\n"
            "name: note-taking\n"
            "description: 将用户明确要求记住的内容和长期有效事实保存为结构化笔记。\n"
            "---\n"
            "# 笔记记录\n"
            "\n"
            "- 用户明确要求记住某事时，必须保存。\n"
            "- 自动保存仅限长期有效事实，例如偏好、计划、约束和联系人。\n"
            "- 新事实使用 `note_add`；用户纠正已保存事实时使用 `note_update`；新增前先用 `note_search` 避免重复。\n"
            "- 对明确保存请求和重要长期笔记，给出简短确认。\n"
            "- 创建新笔记前先搜索已有笔记。\n"
            "- 不要自动保存一次性闲聊、临时情绪或把握不足的猜测。\n"
            "- 保存长期用户事实时优先使用笔记工具，不要写入 `profile_write`。\n"
            "- 长期事实示例：`我乳糖不耐受`、`以后默认中文回答`、`我更喜欢美式`、`六月计划去日本`。\n"
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
    # 这段文本会作为最高优先级的 system prompt 进入每次 LLM 调用。
    # 组织顺序刻意分成：身份 -> 时间 -> 工具路由 -> 记忆/笔记边界 -> 输出约束，
    # 方便后续排查模型为什么选择某个工具或某种回复风格。
    return "\n".join(
        [
            f"你是 {agent_name}。",
            "必须以系统提供的当前时间作为日期、时间和年份判断的事实来源。",
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
                "回答“比如呢”“这个呢”“那呢”等省略式追问时，"
                "必须优先承接上一轮助手回复中的话题和问题，再参考更早的上下文；"
                "除非用户明确切换话题，不要被更早出现的地点、计划或实体牵走。"
            ),
            "提醒或 cron 任务创建成功后，最终可见回复要简短；除非用户询问，不要解释内部调度推理。",
            "对明确保存请求和重要自动笔记保存，给出简短确认；其他自动保存保持安静。",
            (
                "当用户询问你有哪些工具或技能时，只能依据当前上下文里的 [工具索引] 与 [技能索引] 回答，"
                "不要声称自己拥有未暴露的能力。"
            ),
            f"进程启动本地时间：{now.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        ]
    )


async def _build_mflow_bridge_async(settings, *, workspace_dir: Path) -> MflowBridge | None:
    """根据设置构建并初始化 M-flow bridge。"""

    mflow_settings = getattr(settings, "mflow", None)
    if mflow_settings is not None and not mflow_settings.enabled:
        return None

    runtime_config = MflowRuntimeConfig(
        enabled=True if mflow_settings is None else mflow_settings.enabled,
        dataset_name=(
            mflow_settings.dataset_name
            if mflow_settings is not None and mflow_settings.dataset_name
            else workspace_dir.name
        ),
        llm=_build_mflow_llm_config(settings, provider_name=getattr(mflow_settings, "llm_provider_name", None)),
        embedding=_build_mflow_embedding_config(settings, mflow_settings),
        graph_database_provider=(
            mflow_settings.graph_database_provider if mflow_settings is not None else "kuzu"
        ),
        vector_db_provider=(
            mflow_settings.vector_db_provider if mflow_settings is not None else "lancedb"
        ),
    )
    data_dir = (
        mflow_settings.data_dir
        if mflow_settings is not None and mflow_settings.data_dir is not None
        else workspace_dir / "mflow_data"
    )
    bridge = MflowBridge(data_dir=data_dir, runtime_config=runtime_config)
    await bridge.initialize()
    return bridge


def _build_mflow_llm_config(settings, *, provider_name: str | None = None) -> MflowLLMConfig:
    """把主 provider 配置映射为 M-flow 可识别的 LLM 配置。"""

    provider_item = _find_provider_item(settings, provider_name or settings.providers.default_primary)
    return MflowLLMConfig(
        provider=_map_provider_type_to_mflow(provider_item.provider_type, provider_item.base_url),
        model=_qualify_llm_model_for_mflow_litellm(
            provider_type=provider_item.provider_type,
            model=provider_item.model,
            base_url=provider_item.base_url,
        ),
        api_key_env=provider_item.api_key_env,
        base_url=provider_item.base_url,
    )


def _build_mflow_embedding_config(settings, mflow_settings) -> MflowEmbeddingConfig | None:
    """构建 M-flow embedding 配置。"""

    if mflow_settings is None or mflow_settings.embedding is None:
        return None

    embedding_settings = mflow_settings.embedding
    provider_item = (
        _find_provider_item(settings, embedding_settings.provider_name)
        if embedding_settings.provider_name
        else None
    )
    provider_type = embedding_settings.provider_type or (
        provider_item.provider_type if provider_item is not None else "openai"
    )
    if provider_type not in {"openai", "ollama", "fastembed"}:
        raise ValueError(
            "M-flow embedding provider must resolve to openai, ollama, or fastembed-compatible settings"
        )

    return MflowEmbeddingConfig(
        provider="openai" if provider_type == "openai" else provider_type,
        model=_qualify_embedding_model_for_mflow_litellm(
            provider_type=provider_type,
            model=embedding_settings.model or (provider_item.model if provider_item is not None else ""),
            base_url=embedding_settings.base_url if embedding_settings.base_url is not None else (
                provider_item.base_url if provider_item is not None else None
            ),
        ),
        api_key_env=embedding_settings.api_key_env or (
            provider_item.api_key_env if provider_item is not None else ""
        ),
        base_url=embedding_settings.base_url if embedding_settings.base_url is not None else (
            provider_item.base_url if provider_item is not None else None
        ),
        api_version=embedding_settings.api_version,
        dimensions=embedding_settings.dimensions,
        batch_size=embedding_settings.batch_size,
    )


def _find_provider_item(settings, provider_name: str):
    """按名称查找已配置 provider。"""

    for item in settings.providers.items:
        if item.name == provider_name:
            return item
    raise ValueError(f"Unknown provider: {provider_name}")


def _map_provider_type_to_mflow(provider_type: str, base_url: str | None) -> str:
    """把现有 provider 类型映射为 M-flow 支持的 provider 标识。"""

    if provider_type != "openai":
        return provider_type

    if not base_url:
        return "openai"

    host = urlsplit(base_url).netloc.lower()
    return "openai" if host.endswith("openai.com") else "custom"


def _qualify_llm_model_for_mflow_litellm(*, provider_type: str, model: str, base_url: str | None) -> str:
    """为 LiteLLM 的 chat/completion 路由补齐 provider 前缀。"""

    if provider_type != "openai" or not model or "/" in model:
        return model

    prefix = _infer_litellm_provider_prefix(base_url)
    if prefix is None:
        return model
    return f"{prefix}/{model}"


def _qualify_embedding_model_for_mflow_litellm(*, provider_type: str, model: str, base_url: str | None) -> str:
    """为 LiteLLM 的 embedding 路由补齐 provider 前缀。"""

    if provider_type != "openai" or not model or "/" in model:
        return model

    # LiteLLM 对 OpenAI-compatible embedding 端点会走 OpenAI 路由；
    # 某些 provider-specific 前缀（如 dashscope/...）在 embedding 上并不兼容。
    if _is_official_openai_endpoint(base_url):
        return model
    return f"openai/{model}"


def _infer_litellm_provider_prefix(base_url: str | None) -> str | None:
    """按 endpoint 主机推断 LiteLLM provider 前缀。"""

    if _is_official_openai_endpoint(base_url):
        return None

    host = urlsplit(base_url).netloc.lower()
    if host.endswith("deepseek.com"):
        return "deepseek"
    if host.endswith("dashscope.aliyuncs.com") or host.endswith("dashscope-intl.aliyuncs.com"):
        return "dashscope"
    return "openai"


def _is_official_openai_endpoint(base_url: str | None) -> bool:
    if not base_url:
        return True
    return urlsplit(base_url).netloc.lower().endswith("openai.com")
