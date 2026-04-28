"""把 YAML 配置加载成运行时对象，并在入口处做强校验。

这一层的原则是：
1. 尽量早失败
2. 失败信息要稳定、可读
3. 不把原始 `KeyError`、`YAMLError` 等低层异常直接漏给上层
"""

import os
import re
from pathlib import Path

import yaml

from agent.config.models import (
    AgentSettings,
    ChannelInstanceSettings,
    ChannelSettings,
    LangfuseSettings,
    MflowEmbeddingSettings,
    MflowSettings,
    ObservabilitySettings,
    ProviderConfigItem,
    ProviderSettings,
    Settings,
    ShellToolSettings,
    ToolSettings,
)


class ConfigError(ValueError):
    """统一的配置异常类型。

    这样上层只需要捕获一种异常，就能处理“文件缺失 / YAML 损坏 / 字段缺失”
    这些一期会遇到的配置问题。
    """


_ENV_TOKEN_PATTERN = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}")


def load_settings(agent_config_path: Path) -> Settings:
    """读取主配置并拼出完整 Settings。

    这里做了三件事：
    1. 读取 `agent.yaml`
    2. 再根据其中的 `providers.config_file` 读取 provider 配置
    3. 把两份 YAML 组装成 dataclass
    """

    config_path = Path(agent_config_path).resolve()
    config_dir = config_path.parent

    raw = _read_yaml(config_path)
    agent_section = _require_mapping(raw, "agent")
    provider_section = _require_mapping(raw, "providers")
    provider_path = _resolve_path(
        config_dir,
        _require_str(provider_section, "config_file", "providers"),
        field_name="providers.config_file",
    )
    provider_raw = _read_yaml(provider_path)
    provider_items_raw = provider_raw.get("providers")

    if not isinstance(provider_items_raw, list) or not provider_items_raw:
        raise ConfigError("providers.providers must be a non-empty list")

    # 先把 provider 列表都转成强类型对象，后面再做交叉校验。
    provider_items = [_build_provider_item(item, index) for index, item in enumerate(provider_items_raw)]
    default_primary = _require_str(provider_section, "default_primary", "providers")
    provider_names = {item.name for item in provider_items}

    if default_primary not in provider_names:
        raise ConfigError("providers.default_primary must match a configured provider name")

    channels = _build_channel_settings(_optional_mapping(raw, "channels"), config_dir)

    return Settings(
        agent=AgentSettings(
            name=_require_str(agent_section, "name", "agent"),
            workspace_dir=_resolve_agent_workspace_dir(agent_section, config_dir=config_dir, channels=channels),
            max_iterations=_require_int(agent_section, "max_iterations", "agent"),
            context_history_turns=_optional_int(agent_section, "context_history_turns") or 10,
        ),
        providers=ProviderSettings(
            config_file=provider_path,
            default_primary=default_primary,
            items=provider_items,
        ),
        channels=channels,
        mflow=_build_mflow_settings(
            _optional_mapping(raw, "mflow"),
            config_dir=config_dir,
            provider_names=provider_names,
        ),
        tools=_build_tool_settings(_optional_mapping(raw, "tools")),
        observability=_build_observability_settings(_optional_mapping(raw, "observability")),
    )


def is_multi_runtime_settings(settings: Settings) -> bool:
    """Return True only when config declares more than one runtime instance."""

    return bool(settings.channels and len(settings.channels.instances) > 1)


def _read_yaml(path: Path) -> dict:
    """读取单个 YAML 文件，并把底层异常包装成 ConfigError。"""

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Unable to read configuration file: {path}") from exc

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in configuration file: {path}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} must contain a top-level mapping")
    return data


def _resolve_path(config_dir: Path, raw_path: str, *, field_name: str) -> Path:
    """解析带环境变量占位符的路径字段。"""

    expanded = _expand_env_tokens(raw_path, field_name=field_name)
    return (config_dir / Path(expanded).expanduser()).resolve()


def _expand_env_tokens(raw_value: str, *, field_name: str) -> str:
    """展开 `${VAR}` 或 `${VAR:-fallback}` 形式的环境变量。"""

    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        default = match.group("default")
        resolved = os.environ.get(name)
        if resolved not in {None, ""}:
            return resolved
        if default is not None:
            return default
        raise ConfigError(f"{field_name} references unset environment variable: {name}")

    return _ENV_TOKEN_PATTERN.sub(replace, raw_value)


def _require_mapping(data: dict, key: str) -> dict:
    """要求某个字段必须是字典。

    这个辅助函数让错误信息更聚焦，避免后面出现一连串难读的属性访问失败。
    """

    value = data.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be a mapping")
    return value


def _optional_mapping(data: dict, key: str) -> dict | None:
    """读取可选映射字段，不存在时返回 None。"""

    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be a mapping if provided")
    return value


def _require_str(data: dict, key: str, section: str) -> str:
    """要求某个字段必须是非空字符串。"""

    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{section}.{key} must be a non-empty string")
    return value


def _require_int(data: dict, key: str, section: str) -> int:
    """要求某个字段必须是整数。"""

    value = data.get(key)
    if not isinstance(value, int):
        raise ConfigError(f"{section}.{key} must be an integer")
    return value


def _optional_int(data: dict, key: str) -> int | None:
    """读取可选整数，不存在时返回 None。"""

    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} must be an integer if provided")
    return value


def _optional_float(data: dict, key: str) -> float | None:
    """读取可选浮点数，允许 YAML 中使用整数写法。"""

    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{key} must be a number if provided")
    return float(value)


def _optional_str(data: dict, key: str) -> str | None:
    """读取可选字符串字段，不存在或为空时返回 None。"""

    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string if provided")
    return value


def _optional_bool(data: dict, key: str) -> bool | None:
    """读取可选布尔值，不存在时返回 None。"""

    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be a boolean if provided")
    return value


def _build_provider_item(item: object, index: int) -> ProviderConfigItem:
    """把 provider 原始字典转换成强类型对象。"""

    if not isinstance(item, dict):
        raise ConfigError(f"providers.providers[{index}] must be a mapping")

    return ProviderConfigItem(
        name=_require_str(item, "name", f"providers.providers[{index}]"),
        provider_type=_require_str(item, "type", f"providers.providers[{index}]"),
        model=_require_str(item, "model", f"providers.providers[{index}]"),
        api_key_env=_require_str(item, "api_key_env", f"providers.providers[{index}]"),
        base_url=_optional_str(item, "base_url"),
        temperature=_optional_float(item, "temperature"),
        top_p=_optional_float(item, "top_p"),
        max_output_tokens=_optional_int(item, "max_output_tokens"),
        extra_body=_optional_mapping(item, "extra_body"),
    )


def _build_channel_settings(data: dict | None, config_dir: Path) -> ChannelSettings | None:
    """解析可选的渠道实例配置。"""

    if data is None:
        return None

    instances_raw = data.get("instances")
    if instances_raw is None:
        return ChannelSettings(instances=[])
    if not isinstance(instances_raw, list):
        raise ConfigError("channels.instances must be a list if provided")

    return ChannelSettings(
        instances=[
            _build_channel_instance(item, index, config_dir)
            for index, item in enumerate(instances_raw)
        ]
    )


def _build_channel_instance(item: object, index: int, config_dir: Path) -> ChannelInstanceSettings:
    """把渠道实例原始字典转换成强类型对象。"""

    if not isinstance(item, dict):
        raise ConfigError(f"channels.instances[{index}] must be a mapping")

    return ChannelInstanceSettings(
        name=_require_str(item, "name", f"channels.instances[{index}]"),
        channel_type=_require_str(item, "type", f"channels.instances[{index}]"),
        workspace_dir=_resolve_path(
            config_dir,
            _require_str(item, "workspace_dir", f"channels.instances[{index}]"),
            field_name=f"channels.instances[{index}].workspace_dir",
        ),
        app_id_env=_optional_str(item, "app_id_env"),
        app_secret_env=_optional_str(item, "app_secret_env"),
    )


def _resolve_agent_workspace_dir(
    agent_section: dict,
    *,
    config_dir: Path,
    channels: ChannelSettings | None,
) -> Path:
    """解析入口层基础工作区。

    当配置了 channels.instances 时，以第一个 instance 的 workspace 作为基础工作区；
    这会让 gateway 日志、锁文件等宿主运行态与实例空间保持一致，而不是额外生成独立 workspace。
    """

    if channels is not None and channels.instances:
        return channels.instances[0].workspace_dir

    workspace_dir = _optional_str(agent_section, "workspace_dir")
    if workspace_dir is None:
        raise ConfigError("agent.workspace_dir must be a non-empty string")

    return _resolve_path(
        config_dir,
        workspace_dir,
        field_name="agent.workspace_dir",
    )


def _build_mflow_settings(
    data: dict | None,
    *,
    config_dir: Path,
    provider_names: set[str],
) -> MflowSettings:
    """解析可选的 M-flow 配置。"""

    if data is None:
        return MflowSettings()

    enabled = _optional_bool(data, "enabled")
    llm_provider_name = _optional_str(data, "llm_provider_name")
    if llm_provider_name is not None and llm_provider_name not in provider_names:
        raise ConfigError("mflow.llm_provider_name must match a configured provider name")

    data_dir_text = _optional_str(data, "data_dir")
    data_dir = (
        _resolve_path(config_dir, data_dir_text, field_name="mflow.data_dir")
        if data_dir_text is not None
        else None
    )
    graph_database_provider = _optional_str(data, "graph_database_provider") or "kuzu"
    vector_db_provider = _optional_str(data, "vector_db_provider") or "lancedb"

    return MflowSettings(
        enabled=True if enabled is None else enabled,
        data_dir=data_dir,
        dataset_name=_optional_str(data, "dataset_name"),
        llm_provider_name=llm_provider_name,
        graph_database_provider=graph_database_provider,
        vector_db_provider=vector_db_provider,
        embedding=_build_mflow_embedding_settings(_optional_mapping(data, "embedding"), provider_names),
    )


def _build_mflow_embedding_settings(
    data: dict | None,
    provider_names: set[str],
) -> MflowEmbeddingSettings | None:
    """解析 M-flow embedding 配置。"""

    if data is None:
        return None

    provider_name = _optional_str(data, "provider_name")
    if provider_name is not None and provider_name not in provider_names:
        raise ConfigError("mflow.embedding.provider_name must match a configured provider name")

    return MflowEmbeddingSettings(
        provider_name=provider_name,
        provider_type=_optional_str(data, "provider_type"),
        model=_optional_str(data, "model"),
        api_key_env=_optional_str(data, "api_key_env"),
        base_url=_optional_str(data, "base_url"),
        api_version=_optional_str(data, "api_version"),
        dimensions=_optional_int(data, "dimensions"),
        batch_size=_optional_int(data, "batch_size"),
    )


def _build_tool_settings(data: dict | None) -> ToolSettings:
    if data is None:
        return ToolSettings(shell=ShellToolSettings())

    shell_data = _optional_mapping(data, "shell") or {}
    enabled = _optional_bool(shell_data, "enabled")
    requires_confirmation = _optional_bool(shell_data, "requires_confirmation")
    return ToolSettings(
        shell=ShellToolSettings(
            enabled=False if enabled is None else enabled,
            requires_confirmation=True if requires_confirmation is None else requires_confirmation,
        )
    )


def _build_observability_settings(data: dict | None) -> ObservabilitySettings:
    langfuse_data = _optional_mapping(data, "langfuse") if data is not None else None
    return ObservabilitySettings(langfuse=_build_langfuse_settings(langfuse_data))


def _optional_bool_with_default(data: dict, key: str, default: bool) -> bool:
    value = _optional_bool(data, key)
    return default if value is None else value


def _optional_float_with_default(data: dict, key: str, default: float) -> float:
    value = _optional_float(data, key)
    return default if value is None else value


def _build_langfuse_settings(data: dict | None) -> LangfuseSettings:
    if data is None:
        return LangfuseSettings()

    capture_reasoning = _optional_str(data, "capture_reasoning") or "metadata"
    if capture_reasoning not in {"off", "metadata", "full"}:
        raise ConfigError("observability.langfuse.capture_reasoning must be one of: off, metadata, full")

    return LangfuseSettings(
        enabled=_optional_bool_with_default(data, "enabled", True),
        public_key_env=_optional_str(data, "public_key_env") or "LANGFUSE_PUBLIC_KEY",
        secret_key_env=_optional_str(data, "secret_key_env") or "LANGFUSE_SECRET_KEY",
        base_url=_optional_str(data, "base_url") or "http://192.169.26.221:3000",
        capture_inputs=_optional_bool_with_default(data, "capture_inputs", True),
        capture_outputs=_optional_bool_with_default(data, "capture_outputs", True),
        capture_tool_args=_optional_bool_with_default(data, "capture_tool_args", True),
        capture_tool_results=_optional_bool_with_default(data, "capture_tool_results", True),
        capture_reasoning=capture_reasoning,
        max_field_chars=_optional_int(data, "max_field_chars") or 12000,
        sample_rate=_optional_float_with_default(data, "sample_rate", 1.0),
        timeout_seconds=_optional_int(data, "timeout_seconds") or 15,
        flush_interval_seconds=_optional_float_with_default(data, "flush_interval_seconds", 2.0),
        flush_at=_optional_int(data, "flush_at") or 32,
        flush_on_run_end=_optional_bool_with_default(data, "flush_on_run_end", False),
    )
