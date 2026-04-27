from datetime import datetime, timedelta
from pathlib import Path
import json

import pytest

from agent.scheduler.reminder import ReminderScheduler
from agent.tools.builtin.reminder_tools import reminder_create
from agent.tools.runtime_context import RuntimeServices, RuntimeToolContext


class CapturingGateway:
    def __init__(self) -> None:
        self.sent = []

    async def send_to_channel(self, channel: str, session_id: str, text: str) -> None:
        self.sent.append((channel, session_id, text))


class CapturingCore:
    async def run(self, message):
        return message.body


def test_reminder_create_with_delay_uses_current_runtime_time(tmp_path: Path) -> None:
    now = datetime.fromisoformat("2026-04-27T12:37:00+08:00")
    scheduler = ReminderScheduler(
        config_path=tmp_path / "REMINDERS.yaml",
        workspace_dir=tmp_path,
        agent_core=CapturingCore(),
        gateway=CapturingGateway(),
        now_provider=lambda: now,
    )
    services = RuntimeServices(reminder_scheduler=scheduler)
    context = RuntimeToolContext(
        workspace_dir=tmp_path,
        run_id="run-create-reminder",
        channel="feishu",
        channel_instance="feishu",
        session_id="oc_current",
        sender="ou_user",
        metadata={},
    )

    payload = json.loads(
        reminder_create(
            services,
            context=context,
            title="起床",
            message="起床",
            delay_seconds=120,
        )
    )

    assert payload["reminder_id"]
    assert payload["created_by_run_id"] == "run-create-reminder"
    assert payload["run_at"] == "2026-04-27T12:39:00+08:00"
    assert payload["run_at_display"] == "2026年04月27日 12:39 (北京时间)"
    assert payload["output"]["session_id"] == "oc_current"
    assert scheduler.get_reminder(payload["reminder_id"]) is not None
    assert payload["reminder_id"] in (tmp_path / "REMINDERS.yaml").read_text(encoding="utf-8")


def test_reminder_create_accepts_string_delay_seconds(tmp_path: Path) -> None:
    now = datetime.fromisoformat("2026-04-27T14:35:00+08:00")
    scheduler = ReminderScheduler(
        config_path=tmp_path / "REMINDERS.yaml",
        workspace_dir=tmp_path,
        agent_core=CapturingCore(),
        gateway=CapturingGateway(),
        now_provider=lambda: now,
    )
    services = RuntimeServices(reminder_scheduler=scheduler)
    context = RuntimeToolContext(
        workspace_dir=tmp_path,
        run_id="run-create-reminder",
        channel="feishu",
        channel_instance="feishu",
        session_id="oc_current",
        sender="ou_user",
        metadata={},
    )

    payload = json.loads(
        reminder_create(
            services,
            context=context,
            title="写代码",
            message="该写代码了！",
            delay_seconds="120",
        )
    )

    assert payload["run_at"] == "2026-04-27T14:37:00+08:00"
    assert payload["run_at_display"] == "2026年04月27日 14:37 (北京时间)"
    assert "error" not in payload


def test_reminder_create_rejects_past_absolute_time_without_persisting(tmp_path: Path) -> None:
    now = datetime.fromisoformat("2026-04-27T14:30:00+08:00")
    scheduler = ReminderScheduler(
        config_path=tmp_path / "REMINDERS.yaml",
        workspace_dir=tmp_path,
        agent_core=CapturingCore(),
        gateway=CapturingGateway(),
        now_provider=lambda: now,
    )
    services = RuntimeServices(reminder_scheduler=scheduler)
    context = RuntimeToolContext(
        workspace_dir=tmp_path,
        run_id="run-create-reminder",
        channel="feishu",
        channel_instance="feishu",
        session_id="oc_current",
        sender="ou_user",
        metadata={},
    )

    payload = json.loads(
        reminder_create(
            services,
            context=context,
            title="喝水提醒",
            message="该喝水了！",
            run_at="2026-04-27T13:10:00+08:00",
        )
    )

    assert payload["error"]
    assert scheduler.list_reminders() == []


@pytest.mark.asyncio
async def test_due_reminder_executes_once_and_sends_to_configured_session(tmp_path: Path) -> None:
    now = datetime.fromisoformat("2026-04-27T12:39:00+08:00")
    gateway = CapturingGateway()
    scheduler = ReminderScheduler(
        config_path=tmp_path / "REMINDERS.yaml",
        workspace_dir=tmp_path,
        agent_core=CapturingCore(),
        gateway=gateway,
        now_provider=lambda: now,
    )
    reminder = scheduler.create_or_update_reminder(
        title="起床",
        message="起床",
        run_at=now - timedelta(seconds=1),
        output={"channel": "feishu", "session_id": "oc_current"},
        created_by_run_id="run-create",
    )

    result = await scheduler.run_due_once()

    assert result == [reminder.reminder_id]
    assert reminder.status == "completed"
    assert reminder.last_run_id.startswith(f"reminder-{reminder.reminder_id}-")
    assert gateway.sent == [("feishu", "oc_current", "起床")]
