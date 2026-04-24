"""配置层的数据模型。

这些 dataclass 的作用很简单：
把原始 YAML 字典收敛成“有名字、可推断、可补全”的 Python 对象，
避免后续代码到处写 `config["xxx"]["yyy"]` 这种脆弱访问。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class AgentSettings:
    """一期 Agent 自身运行所需的最小配置。"""

    name: str
    workspace_dir: Path
    max_iterations: int


@dataclass(slots=True)
class ChannelInstanceSettings:
    """单个渠道实例的静态配置。"""

    name: str
    channel_type: str
    workspace_dir: Path
    app_id_env: str | None = None
    app_secret_env: str | None = None


@dataclass(slots=True)
class ProviderConfigItem:
    """单个 Provider 的静态配置。

    注意这里仍然保留了抽象层设计：
    虽然一期默认只接 Anthropic，但数据模型没有和 Anthropic 写死耦合。
    """

    name: str
    provider_type: str
    model: str
    api_key_env: str
    base_url: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_output_tokens: int | None = None
    extra_body: dict[str, Any] | None = None


@dataclass(slots=True)
class ProviderSettings:
    """Provider 配置集合。

    `default_primary` 指向默认主模型；
    `items` 则保存配置文件里声明的所有 Provider。
    """

    config_file: Path
    default_primary: str
    items: list[ProviderConfigItem]


@dataclass(slots=True)
class ChannelSettings:
    """渠道实例配置集合。"""

    instances: list[ChannelInstanceSettings]


@dataclass(slots=True)
class Settings:
    """运行时顶层配置对象。

    目前只包含一期真正会用到的两个部分：
    - agent
    - providers
    """

    agent: AgentSettings
    providers: ProviderSettings
    channels: ChannelSettings | None = None
