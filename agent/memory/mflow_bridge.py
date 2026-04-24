"""M-flow 认知记忆系统桥接层。"""

from __future__ import annotations

import importlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)
_LITELLM_EMBEDDING_PATCH_FLAG = "_yi_min_openai_compatible_embedding_patch_applied"


@dataclass(slots=True)
class EpisodeBundle:
    """M-flow 返回的 Episode 包（包含 Facet + Entity）。"""

    episode_id: str
    summary: str
    facets: list[dict]
    entities: list[dict]
    score: float
    created_at: datetime


@dataclass(slots=True)
class TurnData:
    """单轮对话数据。"""

    session_id: str
    turn_index: int
    timestamp: datetime
    user_message: str
    assistant_response: str
    tool_calls: list[dict] | None = None


@dataclass(slots=True)
class MflowLLMConfig:
    """M-flow 使用的 LLM 配置。"""

    provider: str
    model: str
    api_key_env: str
    base_url: str | None = None


@dataclass(slots=True)
class MflowEmbeddingConfig:
    """M-flow 使用的 embedding 配置。"""

    provider: str
    model: str
    api_key_env: str
    base_url: str | None = None
    api_version: str | None = None
    dimensions: int | None = None
    batch_size: int | None = None


@dataclass(slots=True)
class MflowRuntimeConfig:
    """M-flow 运行时配置。"""

    enabled: bool = True
    dataset_name: str = "conversations"
    llm: MflowLLMConfig | None = None
    embedding: MflowEmbeddingConfig | None = None
    graph_database_provider: str = "kuzu"
    vector_db_provider: str = "lancedb"


class MflowBridge:
    """M-flow 集成桥接层。"""

    def __init__(
        self,
        data_dir: str | Path = "mflow_data",
        runtime_config: MflowRuntimeConfig | None = None,
    ):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_config = runtime_config or MflowRuntimeConfig()
        self._initialized = False
        self._sdk_available = False
        self._available = False
        self._mflow = None
        self._env_overrides: dict[str, str] = {}

        if not self.runtime_config.enabled:
            logger.info("M-flow disabled by configuration")
            return

        self._seed_base_environment()

        try:
            self._mflow = _import_mflow_sdk_preserving_host_logging()
            self._sdk_available = True
            logger.info("M-flow SDK imported successfully")
        except ImportError:
            logger.warning(
                "M-flow SDK not installed. Memory ingestion and retrieval will be disabled. "
                "Install dependency `mflow-ai` and run `uv sync`."
            )

    @property
    def is_available(self) -> bool:
        """当前是否已完成初始化并可对外提供服务。"""

        return self._available

    @property
    def sdk_available(self) -> bool:
        """当前进程里是否成功导入了 m_flow。"""

        return self._sdk_available

    async def initialize(self) -> None:
        """初始化 M-flow 引擎。"""

        if not self.runtime_config.enabled or not self._sdk_available or self._mflow is None:
            logger.info("M-flow not available, skipping initialization")
            return

        if self._initialized:
            return

        _patch_litellm_openai_compatible_embedding_defaults(importlib.import_module("litellm"))

        llm_config = self.runtime_config.llm
        if llm_config is None:
            logger.warning("M-flow enabled but no LLM runtime config was provided; skipping initialization")
            return

        llm_api_key = self._resolve_api_key(llm_config.api_key_env)
        if llm_api_key is None:
            logger.warning(
                "M-flow LLM API key env `%s` is missing; skipping initialization",
                llm_config.api_key_env,
            )
            return

        embedding_config = self.runtime_config.embedding
        if embedding_config is None:
            logger.warning(
                "M-flow embedding is not configured. Set `mflow.embedding` in agent.yaml "
                "or provide MFLOW_EMBEDDING_* environment variables."
            )
            return

        embedding_api_key = self._resolve_api_key(embedding_config.api_key_env)
        if embedding_api_key is None:
            logger.warning(
                "M-flow embedding API key env `%s` is missing; skipping initialization",
                embedding_config.api_key_env,
            )
            return

        self._apply_env_override("MFLOW_LLM_PROVIDER", llm_config.provider)
        self._apply_env_override("MFLOW_LLM_MODEL", llm_config.model)
        self._apply_env_override("MFLOW_LLM_API_KEY", llm_api_key)
        if llm_config.base_url:
            self._apply_env_override("MFLOW_LLM_ENDPOINT", llm_config.base_url)

        self._apply_env_override("MFLOW_EMBEDDING_PROVIDER", embedding_config.provider)
        self._apply_env_override("MFLOW_EMBEDDING_MODEL", embedding_config.model)
        self._apply_env_override("MFLOW_EMBEDDING_API_KEY", embedding_api_key)
        if embedding_config.base_url:
            self._apply_env_override("MFLOW_EMBEDDING_ENDPOINT", embedding_config.base_url)
        if embedding_config.api_version:
            self._apply_env_override("MFLOW_EMBEDDING_API_VERSION", embedding_config.api_version)
        if embedding_config.dimensions is not None:
            self._apply_env_override("MFLOW_EMBEDDING_DIMENSIONS", str(embedding_config.dimensions))
        if embedding_config.batch_size is not None:
            self._apply_env_override("MFLOW_EMBEDDING_BATCH_SIZE", str(embedding_config.batch_size))

        data_root = self.data_dir / "data"
        data_root.mkdir(parents=True, exist_ok=True)

        clear_caches = getattr(self._mflow.config, "clear_caches", None)
        if callable(clear_caches):
            clear_caches()

        self._mflow.config.system_root_directory(str(self.data_dir))
        self._mflow.config.data_root_directory(str(data_root))
        self._mflow.config.set_graph_database_provider(self.runtime_config.graph_database_provider)
        self._mflow.config.set_vector_db_provider(self.runtime_config.vector_db_provider)
        self._mflow.config.set_llm_provider(llm_config.provider)
        self._mflow.config.set_llm_model(llm_config.model)
        self._mflow.config.set_llm_api_key(llm_api_key)
        if llm_config.base_url:
            self._mflow.config.set_llm_endpoint(llm_config.base_url)

        self._initialized = True
        self._available = True
        logger.info(
            "M-flow initialized with data_dir=%s dataset=%s vector_db=%s graph_db=%s",
            self.data_dir,
            self.runtime_config.dataset_name,
            self.runtime_config.vector_db_provider,
            self.runtime_config.graph_database_provider,
        )

    async def ingest_turn(self, turn: TurnData) -> None:
        """异步将一轮对话增量写入 M-flow（非阻塞）。"""

        if not self.is_available or self._mflow is None:
            return

        try:
            formatted = self._format_turn(turn)
            await self._mflow.add(
                data=formatted,
                dataset_name=self.runtime_config.dataset_name,
                created_at=turn.timestamp,
            )
            await self._mflow.memorize(datasets=[self.runtime_config.dataset_name])
            logger.debug("M-flow ingested turn: session=%s turn=%s", turn.session_id, turn.turn_index)
        except Exception as exc:
            logger.warning("M-flow ingestion failed (non-blocking): %s", exc)

    async def query(self, question: str, top_k: int = 3) -> list[EpisodeBundle]:
        """图路由检索（失败返回空列表）。"""

        if not self.is_available or self._mflow is None:
            logger.debug("M-flow not available, returning empty results")
            return []

        try:
            result = await self._mflow.query(
                question=question,
                datasets=[self.runtime_config.dataset_name],
                mode="episodic",
                top_k=top_k,
            )
            logger.debug("M-flow query: %s", question)
            return self._parse_results(getattr(result, "context", []))
        except Exception as exc:
            logger.error("M-flow query failed: %s", exc)
            return []

    def _resolve_api_key(self, api_key_env: str) -> str | None:
        value = os.environ.get(api_key_env)
        if value is None or not value.strip():
            return None
        return value

    def _seed_base_environment(self) -> None:
        """在导入 m_flow 前先固定基础目录，避免 SDK 落到默认 site-packages 路径。"""

        data_root = self.data_dir / "data"
        logs_root = self.data_dir / "logs"
        data_root.mkdir(parents=True, exist_ok=True)
        logs_root.mkdir(parents=True, exist_ok=True)
        self._apply_env_override("MFLOW_SYSTEM_ROOT_DIRECTORY", str(self.data_dir))
        self._apply_env_override("MFLOW_DATA_ROOT_DIRECTORY", str(data_root))
        self._apply_env_override("MFLOW_LOGS_ROOT_DIRECTORY", str(logs_root))

    def _apply_env_override(self, name: str, value: str) -> None:
        os.environ[name] = value
        self._env_overrides[name] = value

    def _format_turn(self, turn: TurnData) -> str:
        """将对话轮次格式化为 M-flow 输入格式。"""

        parts = [
            f"[{turn.timestamp.isoformat()}] Session: {turn.session_id}",
            f"Turn: {turn.turn_index}",
            f"User: {turn.user_message}",
        ]

        if turn.tool_calls:
            for tc in turn.tool_calls:
                tool_summary = tc.get("summary") or f"{tc.get('name')}(...)"
                parts.append(f"Tool: {tool_summary}")

        parts.append(f"Assistant: {turn.assistant_response}")
        return "\n".join(parts)

    def _parse_results(self, context: list[Any]) -> list[EpisodeBundle]:
        """解析 M-flow 返回的 Episode bundles。"""

        bundles: list[EpisodeBundle] = []
        for item in context:
            raw = self._coerce_result_mapping(item)
            try:
                bundles.append(
                    EpisodeBundle(
                        episode_id=str(raw.get("episode_id") or raw.get("id") or ""),
                        summary=str(raw.get("summary") or raw.get("text") or raw.get("content") or ""),
                        facets=self._coerce_object_list(raw.get("facets")),
                        entities=self._coerce_object_list(raw.get("entities")),
                        score=float(raw.get("score") or 0.0),
                        created_at=self._coerce_datetime(raw.get("created_at")),
                    )
                )
            except Exception as exc:
                logger.warning("Failed to parse M-flow result: %s", exc)
                continue

        return bundles

    def _coerce_result_mapping(self, item: Any) -> dict[str, Any]:
        if isinstance(item, dict):
            return item

        search_result = getattr(item, "search_result", None)
        if isinstance(search_result, dict):
            return search_result

        if hasattr(item, "__dict__"):
            return dict(vars(item))

        return {"summary": str(item)}

    def _coerce_object_list(self, value: Any) -> list[dict]:
        if not value:
            return []
        if isinstance(value, list):
            return [self._coerce_entity(item) for item in value]
        return [self._coerce_entity(value)]

    def _coerce_entity(self, item: Any) -> dict:
        if isinstance(item, dict):
            return item
        if hasattr(item, "__dict__"):
            return dict(vars(item))
        return {"name": str(item)}

    def _coerce_datetime(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str) and value:
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                pass
        return datetime.now()


def _patch_litellm_openai_compatible_embedding_defaults(litellm_module: Any) -> None:
    """为 OpenAI-compatible embedding 端点补齐 encoding_format 默认值。"""

    if getattr(litellm_module, _LITELLM_EMBEDDING_PATCH_FLAG, False):
        return
    if not hasattr(litellm_module, "aembedding"):
        return

    original_aembedding = litellm_module.aembedding

    async def _patched_aembedding(*args, **kwargs):
        return await original_aembedding(*args, **_normalize_openai_compatible_embedding_kwargs(kwargs))

    litellm_module.aembedding = _patched_aembedding
    setattr(litellm_module, _LITELLM_EMBEDDING_PATCH_FLAG, True)


def _normalize_openai_compatible_embedding_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """按 endpoint 兼容性补齐 embedding 请求参数。"""

    normalized = dict(kwargs)
    if _should_force_openai_compatible_embedding_encoding_format(
        model=normalized.get("model"),
        api_base=normalized.get("api_base"),
        encoding_format=normalized.get("encoding_format"),
    ):
        normalized["encoding_format"] = "float"
    return normalized


def _should_force_openai_compatible_embedding_encoding_format(
    *,
    model: Any,
    api_base: Any,
    encoding_format: Any,
) -> bool:
    """判断是否需要为自定义 OpenAI-compatible embedding 显式传 float。"""

    if encoding_format is not None:
        return False
    if not isinstance(model, str) or not model.startswith("openai/"):
        return False
    if not isinstance(api_base, str) or not api_base.strip():
        return False

    host = urlsplit(api_base).netloc.lower()
    return bool(host) and not host.endswith("openai.com")


def _import_mflow_sdk_preserving_host_logging() -> Any:
    """导入 m_flow 后补回宿主进程已有的日志 handler。"""

    root_logger = logging.getLogger()
    original_handlers = tuple(root_logger.handlers)
    original_level = root_logger.level
    module = importlib.import_module("m_flow")
    _restore_missing_root_handlers(root_logger, original_handlers, original_level)
    return module


def _restore_missing_root_handlers(
    root_logger: logging.Logger,
    original_handlers: tuple[logging.Handler, ...],
    original_level: int,
) -> None:
    for handler in original_handlers:
        if _root_logger_has_equivalent_handler(root_logger, handler):
            continue
        if handler.level == logging.NOTSET and original_level != logging.NOTSET:
            handler.setLevel(original_level)
        root_logger.addHandler(handler)


def _root_logger_has_equivalent_handler(root_logger: logging.Logger, target: logging.Handler) -> bool:
    return any(_handlers_are_equivalent(existing, target) for existing in root_logger.handlers)


def _handlers_are_equivalent(left: logging.Handler, right: logging.Handler) -> bool:
    if left is right:
        return True

    if isinstance(left, logging.FileHandler) and isinstance(right, logging.FileHandler):
        return getattr(left, "baseFilename", None) == getattr(right, "baseFilename", None)

    if isinstance(left, logging.StreamHandler) and isinstance(right, logging.StreamHandler):
        return getattr(left, "stream", None) is getattr(right, "stream", None)

    return False
