from pathlib import Path
import asyncio
import json

import pytest

from agent.scheduler.cron import CronScheduler, CronTask
from agent.scheduler.heartbeat import HeartbeatScheduler
from agent.tools.builtin.cron_tools import cron_create_task, cron_delete_task, cron_list_tasks, cron_run_now, cron_update_task
from agent.tools.runtime_context import RuntimeServices, RuntimeToolContext


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


def test_cron_create_task_is_hot_loaded_and_persisted(tmp_path: Path) -> None:
    core = CapturingCore()
    scheduler = CronScheduler(
        config_path=tmp_path / "CRON.yaml",
        workspace_dir=tmp_path,
        agent_core=core,
        gateway=None,
        channel_instance="feishu",
    )
    services = RuntimeServices(cron_scheduler=scheduler)
    context = RuntimeToolContext(
        workspace_dir=tmp_path,
        run_id="run-create-1",
        channel="feishu",
        channel_instance="feishu",
        session_id="oc_current",
        sender="ou_user",
        metadata={},
    )

    payload = json.loads(
        cron_create_task(
            services,
            context=context,
            name="morning",
            schedule="0 9 * * *",
            prompt="生成早报",
            timezone="Asia/Shanghai",
        )
    )

    assert payload["task_id"]
    assert payload["created_by_run_id"] == "run-create-1"
    assert payload["next_run_at"]
    assert payload["output"]["session_id"] == "oc_current"
    assert scheduler.get_task(payload["task_id"]) is not None
    saved_text = (tmp_path / "CRON.yaml").read_text(encoding="utf-8")
    assert payload["task_id"] in saved_text
    assert "生成早报" in saved_text


def test_cron_load_tasks_migrates_legacy_task_without_task_id(tmp_path: Path) -> None:
    cron_file = tmp_path / "CRON.yaml"
    cron_file.write_text(
        "tasks:\n"
        "  - name: legacy\n"
        "    schedule: '0 9 * * *'\n"
        "    timezone: UTC\n"
        "    action:\n"
        "      type: prompt\n"
        "      prompt: 早报\n"
        "    output: {}\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    scheduler = CronScheduler(
        config_path=cron_file,
        workspace_dir=tmp_path,
        agent_core=CapturingCore(),
        gateway=None,
        channel_instance="feishu",
    )

    scheduler.load_tasks()

    assert scheduler.get_task("legacy") is not None
    assert "task_id: legacy" in cron_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_cron_run_now_generates_execution_run_id_without_restart(tmp_path: Path) -> None:
    core = CapturingCore()
    scheduler = CronScheduler(
        config_path=tmp_path / "CRON.yaml",
        workspace_dir=tmp_path,
        agent_core=core,
        gateway=None,
        channel_instance="feishu",
    )
    task = scheduler.create_or_update_task(
        name="daily",
        schedule="0 8 * * *",
        timezone="UTC",
        action={"type": "prompt", "prompt": "早报"},
        output={},
        enabled=True,
        created_by_run_id="run-create-2",
    )
    services = RuntimeServices(cron_scheduler=scheduler)
    context = RuntimeToolContext(
        workspace_dir=tmp_path,
        run_id="run-now-request",
        channel="feishu",
        channel_instance="feishu",
        session_id="oc_current",
        sender="ou_user",
        metadata={},
    )

    payload = json.loads(cron_run_now(services, context=context, task_id=task.task_id))

    assert payload["run_id"].startswith(f"cron-{task.task_id}-")
    assert scheduler.get_task(task.task_id).last_run_id == payload["run_id"]
    await asyncio.sleep(0)
    assert core.messages[0].message_id == payload["run_id"]


def test_cron_update_list_and_delete_task(tmp_path: Path) -> None:
    scheduler = CronScheduler(
        config_path=tmp_path / "CRON.yaml",
        workspace_dir=tmp_path,
        agent_core=CapturingCore(),
        gateway=None,
        channel_instance="feishu",
    )
    services = RuntimeServices(cron_scheduler=scheduler)
    context = RuntimeToolContext(
        workspace_dir=tmp_path,
        run_id="run-admin",
        channel="feishu",
        channel_instance="feishu",
        session_id="oc_current",
        sender="ou_user",
        metadata={},
    )
    created = json.loads(
        cron_create_task(
            services,
            context=context,
            name="daily",
            schedule="0 8 * * *",
            prompt="早报",
            timezone="UTC",
        )
    )

    updated = json.loads(
        cron_update_task(
            services,
            context=context,
            task_id=created["task_id"],
            name="daily",
            schedule="30 8 * * *",
            prompt="新版早报",
            timezone="UTC",
        )
    )
    listed = json.loads(cron_list_tasks(services, context=context))
    deleted = json.loads(cron_delete_task(services, context=context, task_id=created["task_id"]))

    assert updated["schedule"] == "30 8 * * *"
    assert listed["tasks"][0]["task_id"] == created["task_id"]
    assert deleted == {"task_id": created["task_id"], "deleted": True, "created_by_run_id": "run-admin"}
    assert scheduler.get_task(created["task_id"]) is None
