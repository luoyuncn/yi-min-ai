import sys
from types import SimpleNamespace

import pytest

import agent.app
import agent.gateway.server
import agent.main
import agent.scheduler
import agent.web.app


class FakeGateway:
    instances: list["FakeGateway"] = []

    def __init__(self, app):
        self.app = app
        self.runtime_apps = {}
        self.started = False
        self.stopped = False
        FakeGateway.instances.append(self)

    def register_runtime_app(self, runtime_id, app):
        self.runtime_apps[runtime_id] = app

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


class FakeProactiveScheduler:
    instances: list["FakeProactiveScheduler"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        FakeProactiveScheduler.instances.append(self)

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


def _fake_settings(tmp_path, *, proactive_enabled=True):
    return SimpleNamespace(
        agent=SimpleNamespace(workspace_dir=tmp_path),
        channels=SimpleNamespace(
            instances=[
                SimpleNamespace(
                    name="feishu",
                    channel_type="feishu",
                    app_id_env="FEISHU_APP_ID",
                    app_secret_env="FEISHU_APP_SECRET",
                )
            ]
        ),
        proactive=SimpleNamespace(
            enabled=proactive_enabled,
            min_interval_minutes=20,
            max_interval_minutes=90,
            quiet_hours=[0, 1, 2, 3, 4, 5, 6, 7],
            session_id="",
            channel="feishu",
            channel_instance="",
        ),
    )


def _fake_app():
    core = SimpleNamespace(
        runtime_services=SimpleNamespace(gateway=None),
        session_archive=object(),
    )
    return SimpleNamespace(core=core)


@pytest.fixture
def fake_runtime(monkeypatch, tmp_path):
    FakeGateway.instances = []
    FakeProactiveScheduler.instances = []
    app = _fake_app()
    settings = _fake_settings(tmp_path)

    async def build_channel_apps_async(config_path, testing=False):
        return settings, {"feishu": app}

    monkeypatch.setattr(agent.app, "build_channel_apps_async", build_channel_apps_async)
    monkeypatch.setattr(agent.gateway.server, "GatewayServer", FakeGateway)
    monkeypatch.setattr(agent.scheduler, "ProactiveScheduler", FakeProactiveScheduler)
    return settings, app


@pytest.mark.asyncio
async def test_gateway_mode_starts_proactive_scheduler(fake_runtime, tmp_path):
    _, app = fake_runtime

    await agent.main._run_gateway(
        config_path=tmp_path / "agent.yaml",
        testing=True,
        enable_feishu=False,
        enable_heartbeat=False,
        heartbeat_interval=30,
        enable_cron=False,
        enable_proactive=True,
    )

    assert len(FakeProactiveScheduler.instances) == 1
    scheduler = FakeProactiveScheduler.instances[0]
    assert scheduler.started is True
    assert scheduler.stopped is True
    assert scheduler.kwargs["agent_core"] is app.core
    assert scheduler.kwargs["gateway"] is FakeGateway.instances[0]
    assert app.core.runtime_services.gateway is FakeGateway.instances[0]


@pytest.mark.asyncio
async def test_all_mode_starts_proactive_scheduler(fake_runtime, monkeypatch, tmp_path):
    _, app = fake_runtime

    class FakeServer:
        def __init__(self, config):
            self.config = config

        async def serve(self):
            return None

    class FakeConfig:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    monkeypatch.setattr(agent.web.app, "create_web_app", lambda *args, **kwargs: object())
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(Config=FakeConfig, Server=FakeServer))

    await agent.main._run_all(
        config_path=tmp_path / "agent.yaml",
        testing=True,
        enable_feishu=False,
        enable_heartbeat=False,
        heartbeat_interval=30,
        enable_cron=False,
        enable_proactive=True,
        web_port=8000,
    )

    assert len(FakeProactiveScheduler.instances) == 1
    scheduler = FakeProactiveScheduler.instances[0]
    assert scheduler.started is True
    assert scheduler.stopped is True
    assert scheduler.kwargs["agent_core"] is app.core
    assert scheduler.kwargs["gateway"] is FakeGateway.instances[0]
    assert app.core.runtime_services.gateway is FakeGateway.instances[0]


@pytest.mark.asyncio
async def test_no_proactive_skips_scheduler(fake_runtime, tmp_path):
    await agent.main._run_gateway(
        config_path=tmp_path / "agent.yaml",
        testing=True,
        enable_feishu=False,
        enable_heartbeat=False,
        heartbeat_interval=30,
        enable_cron=False,
        enable_proactive=False,
    )

    assert FakeProactiveScheduler.instances == []
