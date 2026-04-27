"""Tools for one-shot reminders."""

import json

from agent.scheduler.reminder import resolve_run_at
from agent.tools.runtime_context import RuntimeServices, RuntimeToolContext


def reminder_create(
    services: RuntimeServices,
    *,
    context: RuntimeToolContext,
    title: str,
    message: str,
    run_at: str | None = None,
    delay_seconds: int | None = None,
    timezone: str = "Asia/Shanghai",
    output_channel: str | None = None,
    output_session_id: str | None = None,
) -> str:
    scheduler = _require_scheduler(services)
    now = scheduler.now_provider()
    resolved_run_at = resolve_run_at(
        now=now,
        run_at=run_at,
        delay_seconds=delay_seconds,
        timezone=timezone,
    )
    if resolved_run_at <= now:
        from datetime import timezone as _tz, timedelta
        cst = _tz(timedelta(hours=8))
        now_str = now.astimezone(cst).strftime("%Y年%m月%d日 %H:%M (北京时间)")
        return json.dumps({"error": f"提醒时间已过去，当前时间是 {now_str}，请重新指定未来时间或使用 delay_seconds"}, ensure_ascii=False)
    reminder = scheduler.create_or_update_reminder(
        title=title,
        message=message,
        run_at=resolved_run_at,
        output=_resolve_output(context, output_channel, output_session_id),
        created_by_run_id=context.run_id,
    )
    return json.dumps(scheduler.serialize_reminder(reminder), ensure_ascii=False)


def reminder_list(services: RuntimeServices, *, context: RuntimeToolContext) -> str:
    scheduler = _require_scheduler(services)
    payload = {
        "requested_by_run_id": context.run_id,
        "reminders": [scheduler.serialize_reminder(reminder) for reminder in scheduler.list_reminders()],
    }
    return json.dumps(payload, ensure_ascii=False)


def reminder_delete(
    services: RuntimeServices,
    *,
    context: RuntimeToolContext,
    reminder_id: str,
) -> str:
    scheduler = _require_scheduler(services)
    deleted = scheduler.delete_reminder(reminder_id)
    return json.dumps(
        {"reminder_id": reminder_id, "deleted": deleted, "requested_by_run_id": context.run_id},
        ensure_ascii=False,
    )


def _resolve_output(
    context: RuntimeToolContext,
    output_channel: str | None,
    output_session_id: str | None,
) -> dict:
    return {
        "channel": output_channel or ("feishu" if context.channel == "feishu" else context.channel),
        "session_id": output_session_id or context.session_id,
    }


def _require_scheduler(services: RuntimeServices):
    scheduler = getattr(services, "reminder_scheduler", None)
    if scheduler is None:
        raise RuntimeError("Reminder scheduler is not available")
    return scheduler
