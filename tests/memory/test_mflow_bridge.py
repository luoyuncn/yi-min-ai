"""M-flow bridge 测试。"""

import asyncio
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent.memory.mflow_bridge as mflow_bridge_module
from agent.memory.mflow_bridge import (
    MflowBridge,
    MflowEmbeddingConfig,
    MflowLLMConfig,
    MflowRuntimeConfig,
    TurnData,
)


def _build_runtime_config() -> MflowRuntimeConfig:
    return MflowRuntimeConfig(
        dataset_name="workspace-memory",
        llm=MflowLLMConfig(
            provider="custom",
            model="deepseek-v4-flash",
            api_key_env="DEEPSEEK_API_KEY",
            base_url="https://api.deepseek.com/v1",
        ),
        embedding=MflowEmbeddingConfig(
            provider="openai",
            model="text-embedding-v4",
            api_key_env="DASHSCOPE_API_KEY",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            dimensions=1024,
        ),
    )


def test_mflow_bridge_stays_unavailable_when_sdk_is_missing(monkeypatch, tmp_path: Path, caplog) -> None:
    """缺少 SDK 时应诚实降级，而不是误报已检测到。"""

    def _raise_import_error(name: str):
        raise ImportError(name)

    monkeypatch.setattr("agent.memory.mflow_bridge.importlib.import_module", _raise_import_error)
    caplog.set_level("WARNING")

    bridge = MflowBridge(data_dir=tmp_path, runtime_config=_build_runtime_config())
    asyncio.run(bridge.initialize())

    assert bridge.is_available is False
    assert "not installed" in caplog.text
    assert "detected but not yet configured" not in caplog.text


def test_mflow_bridge_initializes_with_runtime_configuration(monkeypatch, tmp_path: Path) -> None:
    """初始化时应把目录、LLM 和 embedding 配置全部传给 M-flow。"""

    class FakeConfig:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def clear_caches(self) -> int:
            self.calls.append(("clear_caches",))
            return 1

        def system_root_directory(self, value: str) -> None:
            self.calls.append(("system_root_directory", value))

        def data_root_directory(self, value: str) -> None:
            self.calls.append(("data_root_directory", value))

        def set_llm_provider(self, value: str) -> None:
            self.calls.append(("set_llm_provider", value))

        def set_llm_model(self, value: str) -> None:
            self.calls.append(("set_llm_model", value))

        def set_llm_api_key(self, value: str) -> None:
            self.calls.append(("set_llm_api_key", value))

        def set_llm_endpoint(self, value: str) -> None:
            self.calls.append(("set_llm_endpoint", value))

        def set_graph_database_provider(self, value: str) -> None:
            self.calls.append(("set_graph_database_provider", value))

        def set_vector_db_provider(self, value: str) -> None:
            self.calls.append(("set_vector_db_provider", value))

    fake_mflow = SimpleNamespace(
        config=FakeConfig(),
        add=None,
        memorize=None,
        query=None,
    )

    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")
    monkeypatch.setattr("agent.memory.mflow_bridge.importlib.import_module", lambda name: fake_mflow)

    bridge = MflowBridge(data_dir=tmp_path, runtime_config=_build_runtime_config())
    asyncio.run(bridge.initialize())

    assert bridge.is_available is True
    assert ("system_root_directory", str(tmp_path)) in fake_mflow.config.calls
    assert ("set_llm_provider", "custom") in fake_mflow.config.calls
    assert ("set_llm_model", "deepseek-v4-flash") in fake_mflow.config.calls
    assert ("set_llm_endpoint", "https://api.deepseek.com/v1") in fake_mflow.config.calls
    assert ("set_vector_db_provider", "lancedb") in fake_mflow.config.calls
    assert ("set_graph_database_provider", "kuzu") in fake_mflow.config.calls
    assert bridge._env_overrides["MFLOW_EMBEDDING_MODEL"] == "text-embedding-v4"
    assert bridge._env_overrides["MFLOW_EMBEDDING_API_KEY"] == "dashscope-key"


def test_patch_litellm_openai_compatible_embedding_defaults_sets_float_for_custom_endpoint() -> None:
    """openai-compatible 自定义 embedding 端点默认应补齐 float 编码格式。"""

    captured: dict[str, object] = {}

    async def fake_aembedding(*args, **kwargs):
        captured["kwargs"] = kwargs
        return "ok"

    fake_litellm = SimpleNamespace(aembedding=fake_aembedding)

    mflow_bridge_module._patch_litellm_openai_compatible_embedding_defaults(fake_litellm)
    result = asyncio.run(
        fake_litellm.aembedding(
            model="openai/text-embedding-v4",
            input=["test"],
            api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
    )

    assert result == "ok"
    assert captured["kwargs"]["encoding_format"] == "float"


def test_patch_litellm_openai_compatible_embedding_defaults_keeps_explicit_encoding_format() -> None:
    """显式传入的 encoding_format 不应被默认补丁覆盖。"""

    captured: dict[str, object] = {}

    async def fake_aembedding(*args, **kwargs):
        captured["kwargs"] = kwargs
        return "ok"

    fake_litellm = SimpleNamespace(aembedding=fake_aembedding)

    mflow_bridge_module._patch_litellm_openai_compatible_embedding_defaults(fake_litellm)
    asyncio.run(
        fake_litellm.aembedding(
            model="openai/text-embedding-v4",
            input=["test"],
            api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
            encoding_format="base64",
        )
    )

    assert captured["kwargs"]["encoding_format"] == "base64"


def test_mflow_bridge_initialize_applies_litellm_embedding_patch(monkeypatch, tmp_path: Path) -> None:
    """初始化时应安装 LiteLLM embedding 兼容补丁。"""

    class FakeConfig:
        def clear_caches(self) -> int:
            return 1

        def system_root_directory(self, value: str) -> None:
            return None

        def data_root_directory(self, value: str) -> None:
            return None

        def set_llm_provider(self, value: str) -> None:
            return None

        def set_llm_model(self, value: str) -> None:
            return None

        def set_llm_api_key(self, value: str) -> None:
            return None

        def set_llm_endpoint(self, value: str) -> None:
            return None

        def set_graph_database_provider(self, value: str) -> None:
            return None

        def set_vector_db_provider(self, value: str) -> None:
            return None

    fake_mflow = SimpleNamespace(
        config=FakeConfig(),
        add=None,
        memorize=None,
        query=None,
    )
    fake_litellm = SimpleNamespace(aembedding=None)
    patch_calls: list[object] = []

    def fake_import(name: str):
        if name == "m_flow":
            return fake_mflow
        if name == "litellm":
            return fake_litellm
        raise ImportError(name)

    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")
    monkeypatch.setattr("agent.memory.mflow_bridge.importlib.import_module", fake_import)
    monkeypatch.setattr(
        "agent.memory.mflow_bridge._patch_litellm_openai_compatible_embedding_defaults",
        lambda module: patch_calls.append(module),
    )

    bridge = MflowBridge(data_dir=tmp_path, runtime_config=_build_runtime_config())
    asyncio.run(bridge.initialize())

    assert patch_calls == [fake_litellm]


def test_mflow_bridge_sets_storage_env_before_import(monkeypatch, tmp_path: Path) -> None:
    """导入 m_flow 之前就应写入基础存储目录，避免 SDK 先落默认路径。"""

    def _import_module(name: str):
        assert os.environ["MFLOW_SYSTEM_ROOT_DIRECTORY"] == str(tmp_path)
        assert os.environ["MFLOW_DATA_ROOT_DIRECTORY"] == str(tmp_path / "data")
        assert os.environ["MFLOW_LOGS_ROOT_DIRECTORY"] == str(tmp_path / "logs")
        return SimpleNamespace(config=SimpleNamespace())

    monkeypatch.setattr("agent.memory.mflow_bridge.importlib.import_module", _import_module)

    bridge = MflowBridge(data_dir=tmp_path, runtime_config=_build_runtime_config())

    assert bridge.sdk_available is True


def test_mflow_bridge_ingests_and_queries_after_initialization(monkeypatch, tmp_path: Path) -> None:
    """初始化完成后应能真正调用 add/memorize/query。"""

    class FakeConfig:
        def clear_caches(self) -> int:
            return 1

        def system_root_directory(self, value: str) -> None:
            return None

        def data_root_directory(self, value: str) -> None:
            return None

        def set_llm_provider(self, value: str) -> None:
            return None

        def set_llm_model(self, value: str) -> None:
            return None

        def set_llm_api_key(self, value: str) -> None:
            return None

        def set_llm_endpoint(self, value: str) -> None:
            return None

        def set_graph_database_provider(self, value: str) -> None:
            return None

        def set_vector_db_provider(self, value: str) -> None:
            return None

    add_calls: list[dict] = []
    memorize_calls: list[dict] = []
    query_calls: list[dict] = []

    async def fake_add(**kwargs):
        add_calls.append(kwargs)

    async def fake_memorize(**kwargs):
        memorize_calls.append(kwargs)

    async def fake_query(**kwargs):
        query_calls.append(kwargs)
        return SimpleNamespace(
            context=[
                {
                    "episode_id": "ep-1",
                    "summary": "用户说他想用阿里云 embedding。",
                    "facets": [{"name": "decision"}],
                    "entities": [{"name": "阿里云"}],
                    "score": 0.88,
                    "created_at": "2026-04-24T14:00:00",
                }
            ]
        )

    fake_mflow = SimpleNamespace(
        config=FakeConfig(),
        add=fake_add,
        memorize=fake_memorize,
        query=fake_query,
    )

    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")
    monkeypatch.setattr("agent.memory.mflow_bridge.importlib.import_module", lambda name: fake_mflow)

    bridge = MflowBridge(data_dir=tmp_path, runtime_config=_build_runtime_config())
    asyncio.run(bridge.initialize())
    asyncio.run(
        bridge.ingest_turn(
            TurnData(
                session_id="feishu-main:thread-1",
                turn_index=3,
                timestamp=datetime(2026, 4, 24, 14, 5, 37),
                user_message="我想把 embedding 也接成阿里的。",
                assistant_response="可以，走百炼兼容接口就行。",
                tool_calls=[{"name": "memory_write", "summary": "memory_write(...)"}],
            )
        )
    )
    bundles = asyncio.run(bridge.query("上次说的 embedding 方案是什么？", top_k=2))

    assert add_calls
    assert add_calls[0]["dataset_name"] == "workspace-memory"
    assert memorize_calls == [{"datasets": ["workspace-memory"]}]
    assert query_calls == [
        {
            "question": "上次说的 embedding 方案是什么？",
            "datasets": ["workspace-memory"],
            "mode": "episodic",
            "top_k": 2,
        }
    ]
    assert len(bundles) == 1
    assert bundles[0].episode_id == "ep-1"
    assert bundles[0].summary == "用户说他想用阿里云 embedding。"
