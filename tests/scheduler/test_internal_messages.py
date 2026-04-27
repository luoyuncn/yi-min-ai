from pathlib import Path

import pytest

from agent.scheduler.cron import CronScheduler, CronTask
from agent.scheduler.heartbeat import HeartbeatScheduler


class CapturingCore:
    def __init__(self) -> None:
        self.messages = []

    async def run(self, message):
        self.messages.append(message)
        return "HEARTBEAT_OK"


@pytest.mark.asyncio
async def test_heartbeat_internal_message_uses_configured_channel_instance(tmp_path: Path) -> None:
    (tmp_path / "HEARTBEAT.md").write_text("检查待办", encoding="utf-8")
    core = CapturingCore()
    scheduler = HeartbeatScheduler(
        workspace_dir=tmp_path,
        agent_core=core,
        gateway=None,
        channel_instance="feishu",
    )

    await scheduler._execute_heartbeat()

    assert core.messages
    message = core.messages[0]
    assert message.channel == "internal"
    assert message.channel_instance == "feishu"
    assert message.sender == "heartbeat"


@pytest.mark.asyncio
async def test_cron_internal_message_uses_configured_channel_instance(tmp_path: Path) -> None:
    core = CapturingCore()
    scheduler = CronScheduler(
        config_path=tmp_path / "CRON.yaml",
        workspace_dir=tmp_path,
        agent_core=core,
        gateway=None,
        channel_instance="feishu",
    )
    task = CronTask(
        name="daily",
        description="",
        schedule="0 8 * * *",
        timezone="UTC",
        action={"type": "prompt", "prompt": "早报"},
        output={},
    )

    await scheduler._run_agent("早报", task)

    assert core.messages
    message = core.messages[0]
    assert message.channel == "internal"
    assert message.channel_instance == "feishu"
    assert message.sender == "cron"
