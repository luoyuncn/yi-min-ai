"""主动性调度器 - 随机间隔唤醒 agent，自由探索后决定是否发消息"""

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from agent.gateway.normalizer import NormalizedMessage

logger = logging.getLogger(__name__)

_CST = timezone(timedelta(hours=8))
_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
_SKIP_SIGNAL = "[不打扰]"


class ProactiveScheduler:
    """随机间隔唤醒 agent，让它自由探索，完成后决定是否发消息。"""

    def __init__(
        self,
        agent_core,
        gateway,
        *,
        min_interval_minutes: int = 20,
        max_interval_minutes: int = 90,
        quiet_hours: list[int] | None = None,
        session_id: str = "",
        channel: str = "feishu",
        channel_instance: str = "feishu",
    ):
        self.agent_core = agent_core
        self.gateway = gateway
        self.min_interval = min_interval_minutes * 60
        self.max_interval = max_interval_minutes * 60
        self.quiet_hours = set(quiet_hours or [])
        self.session_id = session_id
        self.channel = channel
        self.channel_instance = channel_instance
        self._running = False
        self._task: asyncio.Task | None = None
        self._send_count: int = 0
        self._send_count_date = datetime.now(_CST).date()

    async def start(self) -> None:
        if self._running:
            logger.warning("Proactive scheduler already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        logger.info(
            "Proactive scheduler started (interval: %d-%dmin)",
            self.min_interval // 60,
            self.max_interval // 60,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Proactive scheduler stopped")

    def _today_send_count(self) -> int:
        today = datetime.now(_CST).date()
        if today != self._send_count_date:
            self._send_count = 0
            self._send_count_date = today
        return self._send_count

    def _is_quiet_hour(self) -> bool:
        if not self.quiet_hours:
            return False
        return datetime.now(_CST).hour in self.quiet_hours

    async def _scheduler_loop(self) -> None:
        while self._running:
            try:
                interval = random.uniform(self.min_interval, self.max_interval)
                await asyncio.sleep(interval)
                if not self._running:
                    break
                if self._is_quiet_hour():
                    logger.debug("Proactive: quiet hour, skipping")
                    continue
                await self._execute_proactive()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Proactive execution error: %s", e, exc_info=True)

    async def _execute_proactive(self) -> None:
        # Snapshot current CST date and send count atomically at cycle start.
        # If this cycle straddles midnight, we treat it as belonging to the day it started.
        cycle_date = datetime.now(_CST).date()
        if cycle_date != self._send_count_date:
            self._send_count = 0
            self._send_count_date = cycle_date
        send_count = self._send_count
        now = datetime.now(_CST)
        weekday = _WEEKDAYS[now.weekday()]
        body = (
            f"[自由时间] 现在是 {weekday} {now.strftime('%H:%M')}，你有一些空闲。\n"
            f"今天已经主动联系过用户 {send_count} 次。\n\n"
            f"做任何你想做的事——搜索感兴趣的内容、回顾用户最近的状态、\n"
            f"或者只是想想有没有什么值得分享的。\n\n"
            f"完成之后：\n"
            f"- 如果有值得告诉用户的，直接写出来\n"
            f"- 如果没有什么要说的，回复：{_SKIP_SIGNAL}"
        )
        message = NormalizedMessage(
            message_id=f"proactive-{uuid4()}",
            session_id="__proactive__",
            sender="proactive",
            body=body,
            channel="internal",
            channel_instance=self.channel_instance,
            metadata={"type": "proactive"},
            timestamp=datetime.now(timezone.utc),
        )
        logger.info("Executing proactive cycle...")
        try:
            result = await self.agent_core.run(message)
            if not result or not result.strip() or result.strip() == _SKIP_SIGNAL:
                logger.debug("Proactive: agent chose not to send")
                return
            logger.info("Proactive: sending message (%d chars)", len(result))
            await self.gateway.send_to_channel(
                self.channel, self.session_id, result,
                channel_instance=self.channel_instance,
            )
            self._send_count += 1
        except Exception as e:
            logger.error("Proactive execution failed: %s", e, exc_info=True)
