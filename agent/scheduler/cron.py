"""Cron 精确时间调度 - 基于 Cron 表达式的定时任务"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

import yaml

from agent.gateway.normalizer import NormalizedMessage

logger = logging.getLogger(__name__)


class CronTask:
    """Cron 任务定义"""

    def __init__(
        self,
        name: str,
        description: str,
        schedule: str,
        timezone: str,
        action: dict,
        output: dict,
        enabled: bool = True,
    ):
        self.name = name
        self.description = description
        self.schedule = schedule  # Cron 表达式
        self.timezone = timezone
        self.action = action  # {"type": "skill|prompt|tool", ...}
        self.output = output  # {"channel": "feishu", "session_id": "..."}
        self.enabled = enabled
        self.last_run: Optional[datetime] = None
        self.next_run: Optional[datetime] = None


class CronScheduler:
    """Cron 定时任务调度器"""

    def __init__(
        self,
        config_path: Path,
        workspace_dir: Path,
        agent_core,
        gateway,
        channel_instance: str = "default",
    ):
        """
        Args:
            config_path: CRON.yaml 配置文件路径
            workspace_dir: 工作区目录
            agent_core: AgentCore 实例
            gateway: Gateway 实例
        """
        self.config_path = Path(config_path)
        self.workspace_dir = Path(workspace_dir)
        self.agent_core = agent_core
        self.gateway = gateway
        self.channel_instance = channel_instance
        self._tasks: list[CronTask] = []
        self._running = False
        self._task: asyncio.Task | None = None

        # 尝试导入 croniter
        try:
            from croniter import croniter
            import pytz

            self._croniter = croniter
            self._pytz = pytz
            self._available = True
        except ImportError:
            logger.warning(
                "croniter not installed. Cron scheduler will be disabled. "
                "Install with: pip install python-croniter pytz"
            )
            self._available = False

    def load_tasks(self) -> None:
        """加载 Cron 配置"""
        if not self._available:
            return

        if not self.config_path.exists():
            logger.warning(f"Cron config not found: {self.config_path}")
            return

        try:
            with open(self.config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)

            self._tasks = []
            for task_config in config.get("tasks", []):
                task = CronTask(
                    name=task_config["name"],
                    description=task_config.get("description", ""),
                    schedule=task_config["schedule"],
                    timezone=task_config.get("timezone", "UTC"),
                    action=task_config["action"],
                    output=task_config.get("output", {}),
                    enabled=task_config.get("enabled", True),
                )

                if task.enabled:
                    task.next_run = self._calculate_next_run(task)
                    self._tasks.append(task)

            logger.info(f"Loaded {len(self._tasks)} cron tasks")

        except Exception as e:
            logger.error(f"Failed to load cron config: {e}", exc_info=True)

    def _calculate_next_run(self, task: CronTask) -> datetime:
        """计算下次执行时间"""
        tz = self._pytz.timezone(task.timezone)
        now = datetime.now(tz)
        cron = self._croniter(task.schedule, now)
        return cron.get_next(datetime)

    async def start(self) -> None:
        """启动调度器"""
        if not self._available:
            logger.info("Cron scheduler not available (croniter not installed)")
            return

        if self._running:
            logger.warning("Cron scheduler already running")
            return

        self.load_tasks()
        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        logger.info("Cron scheduler started")

    async def stop(self) -> None:
        """停止调度器"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info("Cron scheduler stopped")

    async def _scheduler_loop(self) -> None:
        """调度主循环"""
        while self._running:
            try:
                now = datetime.now(self._pytz.UTC)

                for task in self._tasks:
                    if not task.enabled or not task.next_run:
                        continue

                    # 转换时区比较
                    task_tz = self._pytz.timezone(task.timezone)
                    next_run_utc = task.next_run.astimezone(self._pytz.UTC)

                    if now >= next_run_utc:
                        # 执行任务（不阻塞主循环）
                        asyncio.create_task(self._execute_task(task))
                        # 计算下次执行时间
                        task.last_run = now
                        task.next_run = self._calculate_next_run(task)

                # 每 10 秒检查一次
                await asyncio.sleep(10)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cron scheduler loop error: {e}", exc_info=True)

    async def _execute_task(self, task: CronTask) -> None:
        """执行单个任务"""
        logger.info(f"Executing cron task: {task.name}")

        try:
            action = task.action
            result = None

            if action["type"] == "skill":
                prompt = f"请使用 {action['skill']} skill 执行任务。"
                result = await self._run_agent(prompt, task)

            elif action["type"] == "prompt":
                result = await self._run_agent(action["prompt"], task)

            elif action["type"] == "tool":
                prompt = (
                    f"请执行 {action['tool']} 工具，"
                    f"参数：{action.get('params', {})}"
                )
                result = await self._run_agent(prompt, task)

            # 输出结果
            if result and task.output:
                await self._send_output(result, task.output)

            logger.info(f"Cron task completed: {task.name}")

        except Exception as e:
            logger.error(f"Cron task failed: {task.name}, error: {e}", exc_info=True)

    async def _run_agent(self, prompt: str, task: CronTask) -> str:
        """通过 Agent 执行任务"""
        message = NormalizedMessage(
            message_id=f"cron-{task.name}-{uuid4()}",
            session_id=f"__cron_{task.name}__",
            sender="cron",
            body=f"[CRON TASK: {task.name}]\n{prompt}",
            channel="internal",
            channel_instance=self.channel_instance,
            metadata={
                "type": "cron",
                "task_name": task.name,
                "channel_instance": self.channel_instance,
            },
            timestamp=datetime.now(self._pytz.UTC),
        )

        return await self.agent_core.run(message)

    async def _send_output(self, result: str, output_config: dict) -> None:
        """发送任务输出"""
        channel = output_config.get("channel", "feishu")
        session_id = output_config.get("session_id", "default")

        if self.gateway and channel in self.gateway.adapters:
            await self.gateway.send_to_channel(channel, session_id, result)
        else:
            logger.warning(f"Cannot send output: channel {channel} not available")
