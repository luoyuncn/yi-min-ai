"""Command Queue - 保证同一 session 的消息串行执行"""

import asyncio
import logging
from typing import Callable, Awaitable

from agent.gateway.normalizer import NormalizedMessage
from agent.observability.tracing import elapsed_ms, ensure_trace_id, mark_monotonic, trace_fields

logger = logging.getLogger(__name__)


class CommandQueue:
    """每个 session_id 一个 FIFO 队列，跨 session 并发。
    
    这是一个刻意的设计约束——并发执行同一会话的消息会导致工具冲突和会话历史不一致。
    不同 session 之间可以并发。
    """

    def __init__(self, handler: Callable[[NormalizedMessage], Awaitable[str]]):
        """
        Args:
            handler: 消息处理函数（通常是 AgentCore.run）
        """
        self.handler = handler
        self._queues: dict[str, asyncio.Queue] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._running = False

    async def start(self) -> None:
        """启动队列处理"""
        self._running = True
        logger.info("Command queue started")

    async def stop(self) -> None:
        """停止队列处理"""
        self._running = False

        # 取消所有 worker 任务
        for worker in self._workers.values():
            worker.cancel()

        await asyncio.gather(*self._workers.values(), return_exceptions=True)
        logger.info("Command queue stopped")

    async def enqueue(self, message: NormalizedMessage) -> None:
        """将消息加入对应 session 的队列"""
        if not self._running:
            raise RuntimeError("Command queue not started")

        sid = message.session_id
        metadata = message.metadata
        ensure_trace_id(metadata, fallback_id=message.message_id)

        # 为新 session 创建队列和 worker
        if sid not in self._queues:
            self._queues[sid] = asyncio.Queue()
            self._workers[sid] = asyncio.create_task(self._process_lane(sid))
            logger.debug(f"Created queue for session: {sid}")

        lane = self._queues[sid]
        lane_depth_before = lane.qsize()
        mark_monotonic(metadata, "queue_enqueued_at")
        await self._queues[sid].put(message)
        logger.info(
            f"{trace_fields(metadata, session_id=sid, channel=message.channel)} "
            f"event=queue_enqueued lane_depth_before={lane_depth_before} lane_depth_after={lane.qsize()}"
        )

    async def _process_lane(self, session_id: str) -> None:
        """单个 session 的串行处理循环"""
        logger.debug(f"Worker started for session: {session_id}")

        try:
            while self._running:
                try:
                    # 等待消息，带超时以便定期检查 _running 状态
                    message = await asyncio.wait_for(
                        self._queues[session_id].get(), timeout=1.0
                    )
                    metadata = message.metadata
                    dequeued_at = mark_monotonic(metadata, "queue_dequeued_at")
                    logger.info(
                        f"{trace_fields(metadata, session_id=session_id, channel=message.channel)} "
                        f"event=queue_dequeued queue_wait_ms={elapsed_ms(metadata.get('queue_enqueued_at'), end=dequeued_at)} "
                        f"lane_depth_after_dequeue={self._queues[session_id].qsize()}"
                    )

                    try:
                        handler_started_at = mark_monotonic(metadata, "handler_started_at")
                        result = await self.handler(message)
                        logger.info(
                            f"{trace_fields(metadata, session_id=session_id, channel=message.channel)} "
                            f"event=queue_processed handler_ms={elapsed_ms(handler_started_at)} "
                            f"result_chars={len(result or '')}"
                        )
                    except Exception as e:
                        logger.error(f"Session {session_id} handler error: {e}", exc_info=True)

                    self._queues[session_id].task_done()

                except asyncio.TimeoutError:
                    # 队列空闲，继续循环
                    continue

        except asyncio.CancelledError:
            logger.debug(f"Worker cancelled for session: {session_id}")
        except Exception as e:
            logger.error(f"Worker error for session {session_id}: {e}", exc_info=True)
        finally:
            # 清理队列
            if session_id in self._queues:
                del self._queues[session_id]
            if session_id in self._workers:
                del self._workers[session_id]
            logger.debug(f"Worker stopped for session: {session_id}")
