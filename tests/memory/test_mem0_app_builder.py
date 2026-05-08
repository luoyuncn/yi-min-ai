from pathlib import Path

import agent.app as app_module
from agent.config.models import (
    AgentSettings,
    Mem0EmbeddingSettings,
    Mem0Settings,
    ProviderConfigItem,
    ProviderSettings,
    Settings,
)


def test_build_mem0_memory_service_initializes_server_client(tmp_path: Path, monkeypatch) -> None:
    """Mem0 server 模式应使用官方 MemoryClient，而不是本地 SDK。"""

    monkeypatch.setenv("MEM0_API_KEY", "mem0-test-key")
    captured: dict[str, object] = {}

    class DummyMemory:
        @classmethod
        def from_config(cls, config_dict):
            raise AssertionError("Server mode should not create local Memory SDK")

    class DummyMemoryClient:
        def __init__(self, *, api_key, host, org_id, project_id) -> None:
            captured["api_key"] = api_key
            captured["host"] = host
            captured["org_id"] = org_id
            captured["project_id"] = project_id

    monkeypatch.setattr(app_module, "_load_mem0_sdk_classes", lambda: (DummyMemory, DummyMemoryClient))

    settings = Settings(
        agent=AgentSettings(
            name="Yi Min",
            workspace_dir=tmp_path / "workspace",
            max_iterations=8,
        ),
        providers=ProviderSettings(
            config_file=tmp_path / "providers.yaml",
            default_primary="qwen",
            items=[
                ProviderConfigItem(
                    name="qwen",
                    provider_type="openai",
                    model="qwen3.6-plus",
                    api_key_env="DASHSCOPE_API_KEY",
                )
            ],
        ),
        mem0=Mem0Settings(
            enabled=True,
            mode="server",
            agent_id="yi-min",
            api_key_env="MEM0_API_KEY",
            base_url="https://api.mem0.example",
            org_id="org-1",
            project_id="project-1",
            vector_store_path=tmp_path / "mem0_qdrant",
            history_db_path=tmp_path / "mem0_history.db",
        ),
    )

    service = app_module._build_mem0_memory_service(settings)

    assert service is not None
    assert service.is_ready is True
    assert captured == {
        "api_key": "mem0-test-key",
        "host": "https://api.mem0.example",
        "org_id": "org-1",
        "project_id": "project-1",
    }


def test_build_mem0_sdk_config_uses_mem0_embedding_section(tmp_path: Path, monkeypatch) -> None:
    """_build_mem0_sdk_config should read embedding from settings.mem0.embedding."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    settings = Settings(
        agent=AgentSettings(
            name="test",
            workspace_dir=tmp_path,
            max_iterations=8,
        ),
        providers=ProviderSettings(
            config_file=tmp_path / "providers.yaml",
            default_primary="qwen",
            items=[
                ProviderConfigItem(
                    name="qwen",
                    provider_type="openai",
                    model="qwen-turbo",
                    api_key_env="OPENAI_API_KEY",
                    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                )
            ],
        ),
        mem0=Mem0Settings(
            enabled=True,
            mode="sdk",
            agent_id="test-agent",
            vector_store_path=tmp_path / "mem0_qdrant",
            history_db_path=tmp_path / "mem0_history.db",
            embedding=Mem0EmbeddingSettings(
                provider_name="qwen",
                model="text-embedding-v4",
                dimensions=1024,
            ),
        ),
    )

    config = app_module._build_mem0_sdk_config(settings)

    assert config["vector_store"]["provider"] == "qdrant"
    assert config["embedder"]["config"]["model"] == "text-embedding-v4"
    assert config["embedder"]["config"]["embedding_dims"] == 1024
