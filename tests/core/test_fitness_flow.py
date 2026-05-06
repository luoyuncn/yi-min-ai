from pathlib import Path

from agent.core.loop import AgentCore
from agent.fitness.file_store import FitnessFileStore
from agent.fitness.change_store import FitnessPendingChangeStore
from agent.gateway.normalizer import NormalizedMessage
from agent.tools.runtime_context import RuntimeServices


class RecordingProviderManager:
    def __init__(self) -> None:
        self.requests = []

    async def call(self, request):
        self.requests.append(request)
        return type("Resp", (), {"type": "text", "text": "provider called", "tool_calls": None})()


def test_agent_core_confirms_pending_fitness_change_without_model_call(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (workspace / "PROFILE.md").write_text("# User Profile\n", encoding="utf-8")
    FitnessFileStore(workspace)

    runtime_services = RuntimeServices(fitness_change_store=FitnessPendingChangeStore())
    runtime_services.fitness_change_store.stage(
        "feishu:feishu:chat-fitness",
        sender="ou-user-1",
        target="profile",
        updates={"goal": "力量提升", "plan_style": "PPL"},
        summary="将长期目标改为力量提升，并把训练分化设为 PPL",
    )

    provider = RecordingProviderManager()
    core = AgentCore.build_for_test(workspace, provider, runtime_services=runtime_services)

    message = NormalizedMessage(
        message_id="msg-confirm",
        session_id="chat-fitness",
        sender="ou-user-1",
        body="确认",
        attachments=[],
        channel="feishu",
        channel_instance="feishu",
        metadata={"chat_type": "p2p"},
    )

    result = core.run_sync(message)

    assert "已更新健身档案" in result
    assert not provider.requests
    profile_text = (workspace / "fitness" / "PROFILE.json").read_text(encoding="utf-8")
    assert "力量提升" in profile_text
    assert runtime_services.fitness_change_store.get("feishu:feishu:chat-fitness", sender="ou-user-1") is None


def test_agent_core_routes_initial_fitness_setup_request_to_model(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (workspace / "PROFILE.md").write_text("# User Profile\n", encoding="utf-8")

    provider = RecordingProviderManager()
    core = AgentCore.build_for_test(workspace, provider)

    message = NormalizedMessage(
        message_id="msg-fitness-init",
        session_id="chat-init",
        sender="ou-user-1",
        body="今天练什么",
        attachments=[],
        channel="feishu",
        channel_instance="feishu",
        metadata={"chat_type": "p2p"},
    )

    result = core.run_sync(message)

    assert result == "provider called"
    assert len(provider.requests) == 2


def test_agent_core_allows_reply_to_fitness_setup_prompt_to_reach_model(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (workspace / "PROFILE.md").write_text("# User Profile\n", encoding="utf-8")

    provider = RecordingProviderManager()
    core = AgentCore.build_for_test(workspace, provider)

    first_message = NormalizedMessage(
        message_id="msg-fitness-start",
        session_id="chat-init",
        sender="ou-user-1",
        body="开始训练",
        attachments=[],
        channel="feishu",
        channel_instance="feishu",
        metadata={"chat_type": "p2p"},
    )
    first_result = core.run_sync(first_message)

    assert first_result == "provider called"
    assert len(provider.requests) == 2

    reply_message = NormalizedMessage(
        message_id="msg-fitness-reply",
        session_id="chat-init",
        sender="ou-user-1",
        body="我在健身房训练了1个多月，目标是减脂同时增肌，现在175cm，85-86kg。",
        attachments=[],
        channel="feishu",
        channel_instance="feishu",
        metadata={"chat_type": "p2p"},
    )
    reply_result = core.run_sync(reply_message)

    assert reply_result == "provider called"
    assert len(provider.requests) == 4


def test_agent_core_does_not_confirm_fitness_change_from_another_channel_thread(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (workspace / "PROFILE.md").write_text("# User Profile\n", encoding="utf-8")
    FitnessFileStore(workspace)

    runtime_services = RuntimeServices(fitness_change_store=FitnessPendingChangeStore())
    runtime_services.fitness_change_store.stage(
        "feishu:feishu-main:chat-fitness",
        sender="ou-user-1",
        target="profile",
        updates={"goal": "力量提升", "plan_style": "PPL"},
        summary="将长期目标改为力量提升，并把训练分化设为 PPL",
    )

    provider = RecordingProviderManager()
    core = AgentCore.build_for_test(workspace, provider, runtime_services=runtime_services)

    message = NormalizedMessage(
        message_id="msg-confirm-other-thread",
        session_id="chat-fitness",
        sender="ou-user-1",
        body="确认",
        attachments=[],
        channel="feishu",
        channel_instance="feishu-ops",
        metadata={"chat_type": "p2p"},
    )

    result = core.run_sync(message)

    assert result == "provider called"
    assert provider.requests
    profile_text = (workspace / "fitness" / "PROFILE.json").read_text(encoding="utf-8")
    assert "力量提升" not in profile_text
