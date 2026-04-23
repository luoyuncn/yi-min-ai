"""Metrics 收集 - token/延迟/成本/成功率"""

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, UTC
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class LLMCallMetric:
    """单次 LLM 调用指标"""

    timestamp: str
    provider: str
    model: str
    success: bool
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    error: Optional[str] = None


@dataclass
class ToolCallMetric:
    """单次工具调用指标"""

    timestamp: str
    tool_name: str
    success: bool
    latency_ms: int
    error: Optional[str] = None


@dataclass
class SessionMetric:
    """会话级指标"""

    timestamp: str
    session_id: str
    message_count: int
    total_tokens: int
    total_cost_usd: float


class MetricsCollector:
    """指标收集器
    
    收集并持久化运行时指标：
    - LLM 调用：token、延迟、成本
    - 工具调用：成功率、延迟
    - 会话统计：消息数、总成本
    """

    def __init__(self, metrics_dir: Path):
        self.metrics_dir = Path(metrics_dir)
        self.metrics_dir.mkdir(parents=True, exist_ok=True)
        self.metrics_file = self.metrics_dir / "metrics.jsonl"

        # 内存缓存（用于实时查询）
        self._llm_calls: list[LLMCallMetric] = []
        self._tool_calls: list[ToolCallMetric] = []
        self._sessions: list[SessionMetric] = []

    def record_llm_call(
        self,
        provider: str,
        model: str,
        success: bool,
        latency_ms: int,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        error: Optional[str] = None,
    ) -> None:
        """记录 LLM 调用"""
        metric = LLMCallMetric(
            timestamp=datetime.now(UTC).isoformat(),
            provider=provider,
            model=model,
            success=success,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            error=error,
        )

        self._llm_calls.append(metric)
        self._persist_metric("llm_call", metric)

    def record_tool_call(
        self,
        tool_name: str,
        success: bool,
        latency_ms: int,
        error: Optional[str] = None,
    ) -> None:
        """记录工具调用"""
        metric = ToolCallMetric(
            timestamp=datetime.now(UTC).isoformat(),
            tool_name=tool_name,
            success=success,
            latency_ms=latency_ms,
            error=error,
        )

        self._tool_calls.append(metric)
        self._persist_metric("tool_call", metric)

    def record_session(
        self,
        session_id: str,
        message_count: int,
        total_tokens: int,
        total_cost_usd: float,
    ) -> None:
        """记录会话统计"""
        metric = SessionMetric(
            timestamp=datetime.now(UTC).isoformat(),
            session_id=session_id,
            message_count=message_count,
            total_tokens=total_tokens,
            total_cost_usd=total_cost_usd,
        )

        self._sessions.append(metric)
        self._persist_metric("session", metric)

    def _persist_metric(self, metric_type: str, metric: any) -> None:
        """持久化指标到 JSONL 文件"""
        try:
            with open(self.metrics_file, "a", encoding="utf-8") as f:
                record = {
                    "type": metric_type,
                    "data": asdict(metric),
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        except Exception as e:
            logger.error(f"Failed to persist metric: {e}")

    def get_summary(self) -> dict:
        """获取指标摘要"""
        total_llm_calls = len(self._llm_calls)
        successful_llm = sum(1 for m in self._llm_calls if m.success)
        total_tokens = sum(
            m.input_tokens + m.output_tokens for m in self._llm_calls
        )
        total_cost = sum(m.cost_usd for m in self._llm_calls)

        total_tool_calls = len(self._tool_calls)
        successful_tools = sum(1 for m in self._tool_calls if m.success)

        return {
            "llm_calls": {
                "total": total_llm_calls,
                "successful": successful_llm,
                "success_rate": (
                    successful_llm / total_llm_calls if total_llm_calls > 0 else 0
                ),
                "total_tokens": total_tokens,
                "total_cost_usd": round(total_cost, 4),
            },
            "tool_calls": {
                "total": total_tool_calls,
                "successful": successful_tools,
                "success_rate": (
                    successful_tools / total_tool_calls
                    if total_tool_calls > 0
                    else 0
                ),
            },
            "sessions": {
                "total": len(self._sessions),
            },
        }


# 全局单例
metrics: Optional[MetricsCollector] = None


def init_metrics(metrics_dir: Path) -> MetricsCollector:
    """初始化全局 Metrics 收集器"""
    global metrics
    metrics = MetricsCollector(metrics_dir)
    return metrics
