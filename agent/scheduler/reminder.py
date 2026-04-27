"""One-shot reminder scheduler."""

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

import yaml

logger = logging.getLogger(__name__)


class Reminder:
    """A one-shot reminder."""

    def __init__(
        self,
        *,
        title: str,
        message: str,
        run_at: datetime,
        output: dict,
        reminder_id: str | None = None,
        created_by_run_id: str | None = None,
        status: str = "pending",
    ) -> None:
        self.reminder_id = reminder_id or _derive_reminder_id(title)
        self.title = title
        self.message = message
        self.run_at = _ensure_aware(run_at)
        self.output = output
        self.created_by_run_id = created_by_run_id
        self.status = status
        self.last_run_id: str | None = None
        self.completed_at: datetime | None = None


class ReminderScheduler:
    """Runtime-manageable one-shot reminder scheduler."""

    def __init__(
        self,
        *,
        config_path: Path,
        workspace_dir: Path,
        agent_core,
        gateway,
        channel_instance: str = "default",
        check_interval_seconds: float = 1.0,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.config_path = Path(config_path)
        self.workspace_dir = Path(workspace_dir)
        self.agent_core = agent_core
        self.gateway = gateway
        self.channel_instance = channel_instance
        self.check_interval_seconds = check_interval_seconds
        self.now_provider = now_provider or (lambda: datetime.now().astimezone())
        self._reminders: list[Reminder] = []
        self._running = False
        self._task: asyncio.Task | None = None

    def load_reminders(self) -> None:
        if not self.config_path.exists():
            self._reminders = []
            return

        try:
            config = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
            reminders = []
            for item in config.get("reminders", []):
                reminders.append(
                    Reminder(
                        reminder_id=item.get("reminder_id"),
                        title=item["title"],
                        message=item["message"],
                        run_at=datetime.fromisoformat(item["run_at"]),
                        output=item.get("output", {}),
                        created_by_run_id=item.get("created_by_run_id"),
                        status=item.get("status", "pending"),
                    )
                )
            self._reminders = reminders
            logger.info("Loaded %d reminders", len(self._reminders))
        except Exception as exc:
            logger.error("Failed to load reminders: %s", exc, exc_info=True)

    def list_reminders(self) -> list[Reminder]:
        return list(self._reminders)

    def get_reminder(self, reminder_id: str) -> Reminder | None:
        for reminder in self._reminders:
            if reminder.reminder_id == reminder_id:
                return reminder
        return None

    def create_or_update_reminder(
        self,
        *,
        title: str,
        message: str,
        run_at: datetime,
        output: dict,
        reminder_id: str | None = None,
        created_by_run_id: str | None = None,
        status: str = "pending",
    ) -> Reminder:
        reminder = Reminder(
            reminder_id=reminder_id,
            title=title,
            message=message,
            run_at=run_at,
            output=output,
            created_by_run_id=created_by_run_id,
            status=status,
        )
        existing_index = next(
            (index for index, existing in enumerate(self._reminders) if existing.reminder_id == reminder.reminder_id),
            None,
        )
        if existing_index is None:
            self._reminders.append(reminder)
        else:
            self._reminders[existing_index] = reminder

        self.persist_reminders()
        logger.info("Reminder upserted: %s run_at=%s", reminder.reminder_id, reminder.run_at)
        return reminder

    def delete_reminder(self, reminder_id: str) -> bool:
        before = len(self._reminders)
        self._reminders = [reminder for reminder in self._reminders if reminder.reminder_id != reminder_id]
        deleted = len(self._reminders) != before
        if deleted:
            self.persist_reminders()
            logger.info("Reminder deleted: %s", reminder_id)
        return deleted

    def persist_reminders(self) -> None:
        payload = {"reminders": [self.serialize_reminder(reminder) for reminder in self._reminders]}
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def serialize_reminder(self, reminder: Reminder) -> dict:
        from datetime import timezone as _tz
        cst = _tz(timedelta(hours=8))
        run_at_cst = reminder.run_at.astimezone(cst)
        return {
            "reminder_id": reminder.reminder_id,
            "title": reminder.title,
            "message": reminder.message,
            "run_at": reminder.run_at.isoformat(),
            "run_at_display": run_at_cst.strftime("%Y年%m月%d日 %H:%M (北京时间)"),
            "output": reminder.output,
            "created_by_run_id": reminder.created_by_run_id,
            "status": reminder.status,
            "last_run_id": reminder.last_run_id,
            "completed_at": reminder.completed_at.isoformat() if reminder.completed_at else None,
        }

    async def start(self) -> None:
        if self._running:
            logger.warning("Reminder scheduler already running")
            return

        self.load_reminders()
        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        logger.info("Reminder scheduler started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Reminder scheduler stopped")

    async def run_due_once(self) -> list[str]:
        now = _ensure_aware(self.now_provider())
        completed_ids = []
        for reminder in self._reminders:
            if reminder.status != "pending" or reminder.run_at > now:
                continue
            await self._execute_reminder(reminder)
            completed_ids.append(reminder.reminder_id)

        if completed_ids:
            self.persist_reminders()
        return completed_ids

    async def _scheduler_loop(self) -> None:
        while self._running:
            try:
                await self.run_due_once()
                await asyncio.sleep(self.check_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Reminder scheduler loop error: %s", exc, exc_info=True)

    async def _execute_reminder(self, reminder: Reminder) -> None:
        run_id = f"reminder-{reminder.reminder_id}-{uuid4()}"
        reminder.last_run_id = run_id
        reminder.completed_at = _ensure_aware(self.now_provider())
        reminder.status = "completed"
        logger.info("Executing reminder: %s run_id=%s", reminder.reminder_id, run_id)
        await self._send_output(reminder.message, reminder.output)

    async def _send_output(self, text: str, output_config: dict) -> None:
        channel = output_config.get("channel", "feishu")
        session_id = output_config.get("session_id", "default")
        if self.gateway is not None:
            await self.gateway.send_to_channel(channel, session_id, text)
        else:
            logger.warning("Cannot send reminder output: gateway unavailable")


def resolve_run_at(
    *,
    now: datetime,
    run_at: str | None = None,
    delay_seconds: int | None = None,
    timezone: str = "Asia/Shanghai",
) -> datetime:
    if delay_seconds is not None:
        return _ensure_aware(now) + _seconds_delta(delay_seconds)

    if not run_at:
        raise ValueError("Either run_at or delay_seconds is required")

    parsed = datetime.fromisoformat(run_at)
    if parsed.tzinfo is not None:
        return parsed
    return parsed.replace(tzinfo=ZoneInfo(timezone))


def _seconds_delta(seconds: int | str):
    from datetime import timedelta

    if isinstance(seconds, str):
        stripped = seconds.strip()
        if not stripped.isdigit():
            raise ValueError("delay_seconds must be an integer number of seconds")
        seconds = int(stripped)

    if seconds <= 0:
        raise ValueError("delay_seconds must be greater than 0")
    return timedelta(seconds=seconds)


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.astimezone()
    return value


def _derive_reminder_id(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", title.strip()).strip("-").lower()
    digest = hashlib.sha1(f"{title}-{uuid4()}".encode("utf-8")).hexdigest()[:8]
    if slug:
        return f"reminder-{slug[:32]}-{digest}"
    return f"reminder-{digest}"
