"""Tools for managing the live cron scheduler."""

import json

from agent.tools.runtime_context import RuntimeServices, RuntimeToolContext


def cron_create_task(
    services: RuntimeServices,
    *,
    context: RuntimeToolContext,
    name: str,
    schedule: str,
    prompt: str,
    timezone: str = "Asia/Shanghai",
    description: str = "",
    output_channel: str | None = None,
    output_session_id: str | None = None,
    enabled: bool = True,
) -> str:
    scheduler = _require_scheduler(services)
    output = _resolve_output(context, output_channel, output_session_id)
    task = scheduler.create_or_update_task(
        name=name,
        schedule=schedule,
        timezone=timezone,
        action={"type": "prompt", "prompt": prompt},
        output=output,
        description=description,
        enabled=enabled,
        created_by_run_id=context.run_id,
    )
    return json.dumps(scheduler.serialize_task(task), ensure_ascii=False)


def cron_update_task(
    services: RuntimeServices,
    *,
    context: RuntimeToolContext,
    task_id: str,
    name: str,
    schedule: str,
    prompt: str,
    timezone: str = "Asia/Shanghai",
    description: str = "",
    output_channel: str | None = None,
    output_session_id: str | None = None,
    enabled: bool = True,
) -> str:
    scheduler = _require_scheduler(services)
    output = _resolve_output(context, output_channel, output_session_id)
    task = scheduler.create_or_update_task(
        task_id=task_id,
        name=name,
        schedule=schedule,
        timezone=timezone,
        action={"type": "prompt", "prompt": prompt},
        output=output,
        description=description,
        enabled=enabled,
        created_by_run_id=context.run_id,
    )
    return json.dumps(scheduler.serialize_task(task), ensure_ascii=False)


def cron_list_tasks(services: RuntimeServices, *, context: RuntimeToolContext) -> str:
    scheduler = _require_scheduler(services)
    payload = {
        "created_by_run_id": context.run_id,
        "tasks": [scheduler.serialize_task(task) for task in scheduler.list_tasks()],
    }
    return json.dumps(payload, ensure_ascii=False)


def cron_delete_task(
    services: RuntimeServices,
    *,
    context: RuntimeToolContext,
    task_id: str,
) -> str:
    scheduler = _require_scheduler(services)
    deleted = scheduler.delete_task(task_id)
    return json.dumps(
        {"task_id": task_id, "deleted": deleted, "created_by_run_id": context.run_id},
        ensure_ascii=False,
    )


def cron_run_now(
    services: RuntimeServices,
    *,
    context: RuntimeToolContext,
    task_id: str,
) -> str:
    scheduler = _require_scheduler(services)
    payload = scheduler.run_task_now_sync(task_id)
    payload["requested_by_run_id"] = context.run_id
    return json.dumps(payload, ensure_ascii=False)


def _resolve_output(
    context: RuntimeToolContext,
    output_channel: str | None,
    output_session_id: str | None,
) -> dict:
    channel = output_channel or ("feishu" if context.channel == "feishu" else context.channel)
    return {
        "channel": channel,
        "session_id": output_session_id or context.session_id,
    }


def _require_scheduler(services: RuntimeServices):
    scheduler = getattr(services, "cron_scheduler", None)
    if scheduler is None:
        raise RuntimeError("Cron scheduler is not available")
    return scheduler
