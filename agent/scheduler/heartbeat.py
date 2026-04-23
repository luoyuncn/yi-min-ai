"""Heartbeat 主动调度 - 定期轮询任务清单"""

import asyncio
import logging
from datetime import datetime, UTC
from pathlib import Path
from uuid import uuid4

from agent.gateway.normalizer import NormalizedMessage

logger = logging.getLogger(__name__)


class HeartbeatScheduler:
    """Heartbeat 定时触发器。
    
    职责:
    - 每隔固定时间读取 HEARTBEAT.md
    - 将任务清单包装成内部消息发送给 Agent
    - Agent 判断是否有事需要做
    - 如果有输出，推送到默认通道
    """

    def __init__(
        self,
        workspace_dir: Path,
        agent_core,
        gateway,
        interval_minutes: int = 30,
    ):
        """
        Args:
            workspace_dir: 工作区目录（包含 HEARTBEAT.md）
            agent_core: AgentCore 实例
            gateway: Gateway 实例（用于发送通知）
            interval_minutes: 心跳间隔（分钟）
        """
        self.workspace_dir = Path(workspace_dir)
        self.agent_core = agent_core
        self.gateway = gateway
        self.interval = interval_minutes * 60
        self.heartbeat_file = self.workspace_dir / "HEARTBEAT.md"
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """启动 Heartbeat 调度器"""
        if self._running:
            logger.warning("Heartbeat scheduler already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        logger.info(f"Heartbeat scheduler started (interval: {self.interval}s)")

    async def stop(self) -> None:
        """停止 Heartbeat 调度器"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info("Heartbeat scheduler stopped")

    async def _scheduler_loop(self) -> None:
        """调度主循环"""
        while self._running:
            try:
                await asyncio.sleep(self.interval)

                if not self._running:
                    break

                await self._execute_heartbeat()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat execution error: {e}", exc_info=True)

    async def _execute_heartbeat(self) -> None:
        """执行一次心跳检查"""
        heartbeat_content = self._load_heartbeat_md()

        if not heartbeat_content.strip():
            logger.debug("HEARTBEAT.md is empty, skipping")
            return

        # 构造内部消息
        internal_message = NormalizedMessage(
            message_id=f"heartbeat-{uuid4()}",
            session_id="__heartbeat__",
            sender="system",
            body=(
                f"[HEARTBEAT] Current time: {datetime.now(UTC).isoformat()}\n\n"
                f"Review the following task list and take action on anything "
                f"that needs attention right now. If nothing needs doing, "
                f"respond with exactly 'HEARTBEAT_OK'.\n\n"
                f"{heartbeat_content}"
            ),
            channel="internal",
            metadata={"type": "heartbeat"},
            timestamp=datetime.now(UTC),
        )

        logger.info("Executing heartbeat check...")

        try:
            result = await self.agent_core.run(internal_message)

            if result.strip() == "HEARTBEAT_OK":
                logger.debug("Heartbeat: nothing to do")
            else:
                # 有输出，推送到默认通道（飞书）
                logger.info(f"Heartbeat action taken: {result[:100]}...")
                if self.gateway and "feishu" in self.gateway.adapters:
                    # TODO: 从配置读取默认推送 session_id
                    await self.gateway.send_to_channel(
                        "feishu", "default_session", result
                    )

        except Exception as e:
            logger.error(f"Heartbeat execution failed: {e}", exc_info=True)

    def _load_heartbeat_md(self) -> str:
        """读取 HEARTBEAT.md 内容"""
        if not self.heartbeat_file.exists():
            return ""

        try:
            return self.heartbeat_file.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to read HEARTBEAT.md: {e}")
            return ""
