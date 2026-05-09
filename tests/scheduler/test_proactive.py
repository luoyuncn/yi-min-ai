from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import pytest

from agent.scheduler.proactive import ProactiveScheduler


class CapturingGateway:
    def __init__(self):
        self.sent = []

    async def send_to_channel(self, channel, session_id, content, **kwargs):
        self.sent.append((channel, session_id, content))


class CapturingCore:
    def __init__(self, response="[不打扰]"):
        self.messages = []
        self.response = response

    async def run(self, message):
        self.messages.append(message)
        return self.response


@pytest.mark.asyncio
async def test_skip_signal_suppresses_send() -> None:
    core = CapturingCore(response="[不打扰]")
    gateway = CapturingGateway()
    scheduler = ProactiveScheduler(core, gateway, session_id="oc_test")

    await scheduler._execute_proactive()

    assert len(core.messages) == 1
    assert len(gateway.sent) == 0


@pytest.mark.asyncio
async def test_content_response_sends_to_gateway() -> None:
    core = CapturingCore(response="今天发现一件有趣的事！")
    gateway = CapturingGateway()
    scheduler = ProactiveScheduler(
        core, gateway, session_id="oc_test", channel_instance="feishu"
    )

    await scheduler._execute_proactive()

    assert len(gateway.sent) == 1
    assert gateway.sent[0] == ("feishu", "oc_test", "今天发现一件有趣的事！")
    assert scheduler._send_count == 1


@pytest.mark.asyncio
async def test_empty_response_suppresses_send() -> None:
    core = CapturingCore(response="")
    gateway = CapturingGateway()
    scheduler = ProactiveScheduler(core, gateway, session_id="oc_test")

    await scheduler._execute_proactive()

    assert len(gateway.sent) == 0


def test_quiet_hour_detection() -> None:
    scheduler = ProactiveScheduler(
        CapturingCore(), CapturingGateway(), quiet_hours=[0, 1, 2, 3], session_id=""
    )
    mock_now = MagicMock()
    mock_now.hour = 2
    with patch("agent.scheduler.proactive.datetime") as mock_dt:
        mock_dt.now.return_value = mock_now
        assert scheduler._is_quiet_hour() is True

    mock_now.hour = 10
    with patch("agent.scheduler.proactive.datetime") as mock_dt:
        mock_dt.now.return_value = mock_now
        assert scheduler._is_quiet_hour() is False


def test_daily_counter_resets_on_new_day() -> None:
    scheduler = ProactiveScheduler(
        CapturingCore(), CapturingGateway(), session_id=""
    )
    scheduler._send_count = 5
    scheduler._send_count_date = date.today() - timedelta(days=1)

    count = scheduler._today_send_count()

    assert count == 0
    assert scheduler._send_count_date == date.today()


@pytest.mark.asyncio
async def test_trigger_message_has_sender_proactive() -> None:
    core = CapturingCore(response="[不打扰]")
    gateway = CapturingGateway()
    scheduler = ProactiveScheduler(core, gateway, session_id="oc_test")

    await scheduler._execute_proactive()

    msg = core.messages[0]
    assert msg.sender == "proactive"
    assert "[自由时间]" in msg.body
    assert "[不打扰]" in msg.body


@pytest.mark.asyncio
async def test_send_count_injected_in_trigger_message() -> None:
    core = CapturingCore(response="[不打扰]")
    gateway = CapturingGateway()
    scheduler = ProactiveScheduler(core, gateway, session_id="oc_test")
    scheduler._send_count = 3

    await scheduler._execute_proactive()

    assert "3 次" in core.messages[0].body


@pytest.mark.asyncio
async def test_start_is_idempotent(caplog) -> None:
    import logging
    core = CapturingCore(response="[不打扰]")
    gateway = CapturingGateway()
    scheduler = ProactiveScheduler(
        core, gateway, session_id="oc_test",
        min_interval_minutes=60, max_interval_minutes=120,
    )
    with caplog.at_level(logging.WARNING, logger="agent.scheduler.proactive"):
        await scheduler.start()
        await scheduler.start()  # second call should warn and not create second task
    await scheduler.stop()
    assert "already running" in caplog.text


@pytest.mark.asyncio
async def test_stop_cancels_running_task() -> None:
    core = CapturingCore(response="[不打扰]")
    gateway = CapturingGateway()
    scheduler = ProactiveScheduler(
        core, gateway, session_id="oc_test",
        min_interval_minutes=60, max_interval_minutes=120,
    )
    await scheduler.start()
    assert scheduler._task is not None
    assert not scheduler._task.done()

    await scheduler.stop()

    assert scheduler._task.done()
    assert not scheduler._running
