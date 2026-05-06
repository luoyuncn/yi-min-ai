from pathlib import Path

import agent.app as app_module
from agent.config.models import (
    AgentSettings,
    Mem0Settings,
    MflowEmbeddingSettings,
    MflowSettings,
    ProviderConfigItem,
    ProviderSettings,
    Settings,
)


def test_build_mem0_memory_service_initializes_local_sdk_client(tmp_path: Path, monkeypatch) -> None:
    """Mem0 SDK 模式应复用现有 provider 与 embedding 配置完成真实 client 装配。"""

    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-test-key")
    captured: dict[str, object] = {}
    fake_client = object()

    class DummyMemory:
        @classmethod
        def from_config(cls, config_dict):
            captured["config"] = config_dict
            return fake_client

    class DummyMemoryClient:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("SDK mode should not create MemoryClient")

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
                    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                )
            ],
        ),
        mflow=MflowSettings(
            enabled=False,
            embedding=MflowEmbeddingSettings(
                provider_name="qwen",
                model="text-embedding-v4",
                dimensions=1024,
            ),
        ),
        mem0=Mem0Settings(
            enabled=True,
            mode="sdk",
            agent_id="yi-min",
            vector_store_path=tmp_path / "mem0_qdrant",
            history_db_path=tmp_path / "mem0_history.db",
        ),
    )

    service = app_module._build_mem0_memory_service(settings)

    assert service is not None
    assert service.is_ready is True
    assert service.client is fake_client
    assert captured["config"] == {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": "yi-min",
                "path": str((tmp_path / "mem0_qdrant").resolve()),
                "embedding_model_dims": 1024,
            },
        },
        "llm": {
            "provider": "openai",
            "config": {
                "model": "qwen3.6-plus",
                "api_key": "dashscope-test-key",
                "openai_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            },
        },
        "embedder": {
            "provider": "openai",
            "config": {
                "model": "text-embedding-v4",
                "api_key": "dashscope-test-key",
                "openai_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "embedding_dims": 1024,
            },
        },
        "history_db_path": str((tmp_path / "mem0_history.db").resolve()),
    }


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
