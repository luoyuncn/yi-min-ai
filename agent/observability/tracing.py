"""Tracing 链路追踪 - 持久化 JSONL + 运行时 trace 工具。"""

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, UTC
from pathlib import Path
from typing import Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

TRACE_ID_KEY = "trace_id"


def ensure_trace_id(metadata: dict, *, fallback_id: str | None = None) -> str:
    """确保 metadata 上存在稳定 trace_id。"""

    trace_id = metadata.get(TRACE_ID_KEY)
    if trace_id:
        return str(trace_id)

    trace_id = fallback_id or str(uuid4())
    metadata[TRACE_ID_KEY] = trace_id
    return trace_id


def monotonic_now() -> float:
    """返回单调时钟，适合计算耗时。"""

    return time.perf_counter()


def mark_monotonic(metadata: dict, key: str) -> float:
    """记录一个单调时钟时间点。"""

    timestamp = monotonic_now()
    metadata[key] = timestamp
    return timestamp


def elapsed_ms(start: float | None, *, end: float | None = None) -> int:
    """把单调时钟区间转换成毫秒。"""

    if start is None:
        return -1

    finished_at = monotonic_now() if end is None else end
    return max(0, int((finished_at - start) * 1000))


def text_preview(text: str, *, limit: int = 80) -> str:
    """压缩文本预览，避免日志过长。"""

    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return json.dumps(compact, ensure_ascii=False)
    return json.dumps(f"{compact[:limit]}...", ensure_ascii=False)


def trace_fields(
    metadata: dict,
    *,
    session_id: str | None = None,
    run_id: str | None = None,
    channel: str | None = None,
) -> str:
    """生成统一 trace 字段前缀。"""

    parts = [f"trace_id={ensure_trace_id(metadata)}"]
    if run_id:
        parts.append(f"run_id={run_id}")
    if session_id:
        parts.append(f"session_id={session_id}")
    if channel:
        parts.append(f"channel={channel}")
    return " ".join(parts)


@dataclass
class Span:
    """链路追踪 Span"""

    span_id: str
    trace_id: str
    name: str
    start_time: str
    end_time: Optional[str] = None
    duration_ms: Optional[int] = None
    attributes: dict = field(default_factory=dict)
    error: Optional[str] = None

    def set_attribute(self, key: str, value: any) -> None:
        """设置 Span 属性"""
        self.attributes[key] = value

    def finish(self) -> None:
        """结束 Span"""
        end = datetime.now(UTC)
        self.end_time = end.isoformat()

        start = datetime.fromisoformat(self.start_time)
        self.duration_ms = int((end - start).total_seconds() * 1000)


@dataclass
class Trace:
    """链路追踪 Trace"""

    trace_id: str
    session_id: str
    message_id: str
    start_time: str
    end_time: Optional[str] = None
    spans: list[Span] = field(default_factory=list)

    def start_span(self, name: str) -> Span:
        """开始一个新 Span"""
        span = Span(
            span_id=str(uuid4()),
            trace_id=self.trace_id,
            name=name,
            start_time=datetime.now(UTC).isoformat(),
        )
        self.spans.append(span)
        return span

    def finish(self) -> None:
        """结束 Trace"""
        self.end_time = datetime.now(UTC).isoformat()


class Tracer:
    """链路追踪器
    
    记录每次消息处理的完整执行链路：
    - LLM 调用
    - 工具执行
    - 子任务
    """

    def __init__(self, traces_dir: Path):
        self.traces_dir = Path(traces_dir)
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        self._current_trace: Optional[Trace] = None

    def start_trace(self, session_id: str, message_id: str) -> Trace:
        """开始一个新 Trace"""
        trace = Trace(
            trace_id=str(uuid4()),
            session_id=session_id,
            message_id=message_id,
            start_time=datetime.now(UTC).isoformat(),
        )

        self._current_trace = trace
        return trace

    def end_trace(self) -> None:
        """结束当前 Trace 并持久化"""
        if not self._current_trace:
            return

        self._current_trace.finish()
        self._persist_trace(self._current_trace)
        self._current_trace = None

    def _persist_trace(self, trace: Trace) -> None:
        """持久化 Trace 到 JSONL 文件"""
        try:
            # 按日期分文件
            date_str = datetime.now(UTC).strftime("%Y-%m-%d")
            trace_file = self.traces_dir / f"{date_str}.jsonl"

            with open(trace_file, "a", encoding="utf-8") as f:
                record = asdict(trace)
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        except Exception as e:
            logger.error(f"Failed to persist trace: {e}")


# 全局单例
tracer: Optional[Tracer] = None


def init_tracer(traces_dir: Path) -> Tracer:
    """初始化全局 Tracer"""
    global tracer
    tracer = Tracer(traces_dir)
    return tracer
