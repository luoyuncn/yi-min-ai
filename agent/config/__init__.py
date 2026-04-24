"""配置模块对外暴露的统一入口。

阅读顺序建议：
1. 先看 `models.py` 了解配置对象长什么样
2. 再看 `loader.py` 了解 YAML 是怎么被解析和校验的

这样后面读 `build_app()` 时，会更容易看懂配置是怎么流入运行时的。
"""

from agent.config.environment import load_environment_files
from agent.config.loader import ConfigError, load_settings
from agent.config.models import (
    AgentSettings,
    ChannelInstanceSettings,
    ChannelSettings,
    MflowEmbeddingSettings,
    MflowSettings,
    ProviderConfigItem,
    ProviderSettings,
    Settings,
)

__all__ = [
    "AgentSettings",
    "ChannelInstanceSettings",
    "ChannelSettings",
    "ConfigError",
    "MflowEmbeddingSettings",
    "MflowSettings",
    "ProviderConfigItem",
    "ProviderSettings",
    "Settings",
    "load_environment_files",
    "load_settings",
]
