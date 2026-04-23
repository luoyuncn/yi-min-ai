"""M-flow 认知记忆系统桥接层。

负责将会话数据写入 M-flow Cone Graph，以及通过图路由进行深度检索。
M-flow 使用 LanceDB 嵌入模式，零外部依赖。
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class EpisodeBundle:
    """M-flow 返回的 Episode 包（包含 Facet + Entity）"""

    episode_id: str
    summary: str
    facets: list[dict]
    entities: list[dict]
    score: float
    created_at: datetime


@dataclass
class TurnData:
    """单轮对话数据"""

    session_id: str
    turn_index: int
    timestamp: datetime
    user_message: str
    assistant_response: str
    tool_calls: list[dict] = None


class MflowBridge:
    """M-flow 集成桥接层。
    
    注意：当前实现为占位符，等待 M-flow SDK 正式安装后完善。
    一期策略：
    1. 写入路径异步执行，失败不阻塞主流程
    2. 检索路径同步执行，返回空列表作为降级
    """

    def __init__(self, data_dir: str | Path = "mflow_data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._initialized = False
        self._mflow_available = False

        # 尝试导入 M-flow（如果已安装）
        try:
            # import m_flow
            # 等待正式 SDK 发布后取消注释
            # self._mflow = m_flow
            self._mflow_available = False  # 临时设为 False
            logger.info("M-flow SDK detected but not yet configured")
        except ImportError:
            logger.warning(
                "M-flow SDK not installed. Memory ingestion and retrieval will be disabled. "
                "Install with: pip install m_flow"
            )
            self._mflow_available = False

    async def initialize(self) -> None:
        """初始化 M-flow 引擎"""
        if not self._mflow_available:
            logger.info("M-flow not available, skipping initialization")
            return

        # TODO: 等 M-flow SDK 正式发布后实现
        # m_flow.configure(
        #     storage_path=str(self.data_dir),
        #     db_type="lancedb",
        #     llm_provider="anthropic",
        #     llm_model="claude-sonnet",
        #     embedding_model="text-embedding-3-small",
        # )
        self._initialized = True
        logger.info(f"M-flow initialized with data_dir={self.data_dir}")

    async def ingest_turn(self, turn: TurnData) -> None:
        """异步将一轮对话增量写入 M-flow（非阻塞）"""
        if not self._mflow_available:
            return

        try:
            formatted = self._format_turn(turn)
            # TODO: 等 M-flow SDK 正式发布后实现
            # await self._mflow.add(data=formatted, dataset_name="conversations")
            # await self._mflow.memorize()
            logger.debug(f"M-flow ingested turn: session={turn.session_id}, turn={turn.turn_index}")
        except Exception as e:
            # 写入失败不应该影响主流程
            logger.warning(f"M-flow ingestion failed (non-blocking): {e}")

    async def query(self, question: str, top_k: int = 3) -> list[EpisodeBundle]:
        """图路由检索（同步执行，失败返回空列表）"""
        if not self._mflow_available:
            logger.debug("M-flow not available, returning empty results")
            return []

        try:
            # TODO: 等 M-flow SDK 正式发布后实现
            # results = await self._mflow.query(
            #     question=question,
            #     mode="EPISODIC",
            #     top_k=top_k,
            #     datasets=["conversations"],
            # )
            # return self._parse_results(results.context)
            logger.debug(f"M-flow query: {question}")
            return []  # 临时返回空
        except Exception as e:
            logger.error(f"M-flow query failed: {e}")
            return []

    def _format_turn(self, turn: TurnData) -> str:
        """将对话轮次格式化为 M-flow 输入格式"""
        parts = [
            f"[{turn.timestamp.isoformat()}] Session: {turn.session_id}",
            f"User: {turn.user_message}",
        ]

        if turn.tool_calls:
            for tc in turn.tool_calls:
                tool_summary = tc.get("summary") or f"{tc.get('name')}(...)"
                parts.append(f"Tool: {tool_summary}")

        parts.append(f"Assistant: {turn.assistant_response}")
        return "\n".join(parts)

    def _parse_results(self, context: list[Any]) -> list[EpisodeBundle]:
        """解析 M-flow 返回的 Episode bundles"""
        bundles = []
        for item in context:
            try:
                bundle = EpisodeBundle(
                    episode_id=item.get("episode_id", ""),
                    summary=item.get("summary", ""),
                    facets=item.get("facets", []),
                    entities=item.get("entities", []),
                    score=item.get("score", 0.0),
                    created_at=datetime.fromisoformat(
                        item.get("created_at", datetime.now().isoformat())
                    ),
                )
                bundles.append(bundle)
            except Exception as e:
                logger.warning(f"Failed to parse M-flow result: {e}")
                continue

        return bundles
