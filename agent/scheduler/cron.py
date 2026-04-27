"""Cron 精确时间调度 - 基于 Cron 表达式的定时任务"""

import asyncio
import hashlib
import logging
import re
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
        task_id: str | None = None,
        created_by_run_id: str | None = None,
    ):
        self.task_id = task_id or _derive_task_id(name)
        self.name = name
        self.description = description
        self.schedule = schedule  # Cron 表达式
        self.timezone = timezone
        self.action = action  # {"type": "skill|prompt|tool", ...}
        self.output = output  # {"channel": "feishu", "session_id": "..."}
        self.enabled = enabled
        self.created_by_run_id = created_by_run_id
        self.last_run: Optional[datetime] = None
        self.next_run: Optional[datetime] = None
        self.last_run_id: str | None = None


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
            migrated = False
            for task_config in (config or {}).get("tasks", []):
                if not task_config.get("task_id"):
                    migrated = True
                task = CronTask(
                    name=task_config["name"],
                    description=task_config.get("description", ""),
                    schedule=task_config["schedule"],
                    timezone=task_config.get("timezone", "UTC"),
                    action=task_config["action"],
                    output=task_config.get("output", {}),
                    enabled=task_config.get("enabled", True),
                    task_id=task_config.get("task_id"),
                    created_by_run_id=task_config.get("created_by_run_id"),
                )

                if task.enabled:
                    task.next_run = self._calculate_next_run(task)
                self._tasks.append(task)

            if migrated:
                self.persist_tasks()

            logger.info(f"Loaded {len(self._tasks)} cron tasks")

        except Exception as e:
            logger.error(f"Failed to load cron config: {e}", exc_info=True)

    def _calculate_next_run(self, task: CronTask) -> datetime:
        """计算下次执行时间"""
        tz = self._pytz.timezone(task.timezone)
        now = datetime.now(tz)
        cron = self._croniter(task.schedule, now)
        return cron.get_next(datetime)

    def list_tasks(self) -> list[CronTask]:
        return list(self._tasks)

    def get_task(self, task_id: str) -> CronTask | None:
        for task in self._tasks:
            if task.task_id == task_id:
                return task
        return None

    def create_or_update_task(
        self,
        *,
        name: str,
        schedule: str,
        timezone: str,
        action: dict,
        output: dict,
        description: str = "",
        enabled: bool = True,
        task_id: str | None = None,
        created_by_run_id: str | None = None,
    ) -> CronTask:
        task = CronTask(
            task_id=task_id,
            name=name,
            description=description,
            schedule=schedule,
            timezone=timezone,
            action=action,
            output=output,
            enabled=enabled,
            created_by_run_id=created_by_run_id,
        )
        if task.enabled:
            task.next_run = self._calculate_next_run(task)

        existing_index = next(
            (index for index, existing in enumerate(self._tasks) if existing.task_id == task.task_id),
            None,
        )
        if existing_index is None:
            self._tasks.append(task)
        else:
            self._tasks[existing_index] = task

        self.persist_tasks()
        logger.info("Cron task upserted: %s next_run=%s", task.task_id, task.next_run)
        return task

    def delete_task(self, task_id: str) -> bool:
        before = len(self._tasks)
        self._tasks = [task for task in self._tasks if task.task_id != task_id]
        deleted = len(self._tasks) != before
        if deleted:
            self.persist_tasks()
            logger.info("Cron task deleted: %s", task_id)
        return deleted

    def run_task_now_sync(self, task_id: str) -> dict:
        task = self.get_task(task_id)
        if task is None:
            raise ValueError(f"Cron task not found: {task_id}")

        run_id = self._build_execution_run_id(task)
        task.last_run_id = run_id
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            result = asyncio.run(self._execute_task(task, run_id=run_id))
            return {"task_id": task.task_id, "run_id": run_id, "result": result.get("result")}

        loop.create_task(self._execute_task(task, run_id=run_id))
        return {"task_id": task.task_id, "run_id": run_id, "scheduled": True}

    def persist_tasks(self) -> None:
        payload = {
            "tasks": [
                {
                    "task_id": task.task_id,
                    "name": task.name,
                    "description": task.description,
                    "schedule": task.schedule,
                    "timezone": task.timezone,
                    "action": task.action,
                    "output": task.output,
                    "enabled": task.enabled,
                    "created_by_run_id": task.created_by_run_id,
                }
                for task in self._tasks
            ]
        }
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def serialize_task(self, task: CronTask) -> dict:
        return {
            "task_id": task.task_id,
            "name": task.name,
            "description": task.description,
            "schedule": task.schedule,
            "timezone": task.timezone,
            "action": task.action,
            "output": task.output,
            "enabled": task.enabled,
            "created_by_run_id": task.created_by_run_id,
            "next_run_at": task.next_run.isoformat() if task.next_run else None,
            "last_run_at": task.last_run.isoformat() if task.last_run else None,
            "last_run_id": task.last_run_id,
        }

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
                        run_id = self._build_execution_run_id(task)
                        task.last_run_id = run_id
                        asyncio.create_task(self._execute_task(task, run_id=run_id))
                        # 计算下次执行时间
                        task.last_run = now
                        task.next_run = self._calculate_next_run(task)

                # 每 10 秒检查一次
                await asyncio.sleep(10)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cron scheduler loop error: {e}", exc_info=True)

    async def _execute_task(self, task: CronTask, *, run_id: str | None = None) -> dict:
        """执行单个任务"""
        run_id = run_id or self._build_execution_run_id(task)
        task.last_run_id = run_id
        logger.info("Executing cron task: %s run_id=%s", task.name, run_id)

        try:
            action = task.action
            result = None

            if action["type"] == "skill":
                prompt = f"请使用 {action['skill']} skill 执行任务。"
                result = await self._run_agent(prompt, task, run_id=run_id)

            elif action["type"] == "prompt":
                result = await self._run_agent(action["prompt"], task, run_id=run_id)

            elif action["type"] == "tool":
                prompt = (
                    f"请执行 {action['tool']} 工具，"
                    f"参数：{action.get('params', {})}"
                )
                result = await self._run_agent(prompt, task, run_id=run_id)

            # 输出结果
            if result and task.output:
                await self._send_output(result, task.output)

            logger.info("Cron task completed: %s run_id=%s", task.name, run_id)
            return {"task_id": task.task_id, "run_id": run_id, "result": result}

        except Exception as e:
            logger.error(f"Cron task failed: {task.name}, error: {e}", exc_info=True)
            return {"task_id": task.task_id, "run_id": run_id, "error": str(e)}

    async def _run_agent(self, prompt: str, task: CronTask, *, run_id: str | None = None) -> str:
        """通过 Agent 执行任务"""
        run_id = run_id or self._build_execution_run_id(task)
        message = NormalizedMessage(
            message_id=run_id,
            session_id=f"__cron_{task.task_id}__",
            sender="cron",
            body=f"[CRON TASK: {task.name}]\n{prompt}",
            channel="internal",
            channel_instance=self.channel_instance,
            metadata={
                "type": "cron",
                "run_id": run_id,
                "task_id": task.task_id,
                "task_name": task.name,
                "channel_instance": self.channel_instance,
            },
            timestamp=datetime.now(self._pytz.UTC),
        )

        return await self.agent_core.run(message)

    def _build_execution_run_id(self, task: CronTask) -> str:
        return f"cron-{task.task_id}-{uuid4()}"

    async def _send_output(self, result: str, output_config: dict) -> None:
        """发送任务输出"""
        channel = output_config.get("channel", "feishu")
        session_id = output_config.get("session_id", "default")

        if self.gateway and channel in self.gateway.adapters:
            await self.gateway.send_to_channel(channel, session_id, result)
        else:
            logger.warning(f"Cannot send output: channel {channel} not available")


def _derive_task_id(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", name.strip()).strip("-").lower()
    if slug:
        return slug[:48]
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"task-{digest}"
