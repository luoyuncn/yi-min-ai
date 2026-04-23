"""把 YAML 配置加载成运行时对象，并在入口处做强校验。

这一层的原则是：
1. 尽量早失败
2. 失败信息要稳定、可读
3. 不把原始 `KeyError`、`YAMLError` 等低层异常直接漏给上层
"""

from pathlib import Path

import yaml

from agent.config.models import AgentSettings, ProviderConfigItem, ProviderSettings, Settings


class ConfigError(ValueError):
    """统一的配置异常类型。

    这样上层只需要捕获一种异常，就能处理“文件缺失 / YAML 损坏 / 字段缺失”
    这些一期会遇到的配置问题。
    """


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
    provider_path = (config_dir / _require_str(provider_section, "config_file", "providers")).resolve()
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

    return Settings(
        agent=AgentSettings(
            name=_require_str(agent_section, "name", "agent"),
            workspace_dir=(config_dir / _require_str(agent_section, "workspace_dir", "agent")).resolve(),
            max_iterations=_require_int(agent_section, "max_iterations", "agent"),
        ),
        providers=ProviderSettings(
            config_file=provider_path,
            default_primary=default_primary,
            items=provider_items,
        ),
    )


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


def _optional_str(data: dict, key: str) -> str | None:
    """读取可选字符串字段，不存在或为空时返回 None。"""

    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string if provided")
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
        extra_body=_optional_mapping(item, "extra_body"),
    )
