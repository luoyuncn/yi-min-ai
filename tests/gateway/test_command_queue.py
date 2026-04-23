"""CommandQueue 日志测试。"""

import asyncio
import logging

from agent.gateway.command_queue import CommandQueue
from agent.gateway.normalizer import NormalizedMessage


def test_command_queue_logs_enqueue_dequeue_and_completion(caplog) -> None:
    """命令队列应记录入队、出队、等待时间和处理完成。"""

    handled: list[str] = []

    async def handler(message: NormalizedMessage) -> str:
        handled.append(message.message_id)
        await asyncio.sleep(0.01)
        return "ok"

    queue = CommandQueue(handler=handler)
    message = NormalizedMessage(
        message_id="queue-msg-1",
        session_id="queue-session-1",
        sender="user",
        body="hello queue",
        attachments=[],
        channel="feishu",
        metadata={"trace_id": "trace-queue-1"},
    )
    caplog.set_level(logging.INFO, logger="agent.gateway.command_queue")

    async def exercise() -> None:
        await queue.start()
        await queue.enqueue(message)
        await asyncio.sleep(0.05)
        await queue.stop()

    asyncio.run(exercise())

    assert handled == ["queue-msg-1"]
    log_text = caplog.text
    assert "event=queue_enqueued" in log_text
    assert "event=queue_dequeued" in log_text
    assert "queue_wait_ms=" in log_text
    assert "event=queue_processed" in log_text
    assert "trace_id=trace-queue-1" in log_text
