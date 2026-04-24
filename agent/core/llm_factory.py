"""LLM 工厂模块。

统一创建运行时 `ProviderConfig`，把模型默认参数和调用方覆盖规则收口到一个入口。
"""

from typing import Any

from agent.config.models import ProviderConfigItem, Settings
from agent.core.provider import ProviderConfig


class LLMFactory:
    """统一构建运行时 LLM 配置。"""

    @classmethod
    def create(
        cls,
        provider_item: ProviderConfigItem,
        *,
        name: str | None = None,
        provider_type: str | None = None,
        model: str | None = None,
        api_key_env: str | None = None,
        base_url: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        extra_body: dict[str, Any] | None = None,
        enable_thinking: bool | None = None,
        max_output_tokens: int | None = None,
    ) -> ProviderConfig:
        """根据静态 provider 配置和运行时覆盖参数创建 `ProviderConfig`。"""

        resolved_model = model or provider_item.model
        merged_extra_body = cls._merge_extra_body(
            cls._default_extra_body_for_model(resolved_model),
            provider_item.extra_body,
            extra_body,
        )
        merged_extra_body = cls._apply_thinking_override(
            resolved_model,
            merged_extra_body,
            enable_thinking,
        )

        return ProviderConfig(
            name=name or provider_item.name,
            provider_type=provider_type or provider_item.provider_type,
            model=resolved_model,
            api_key_env=api_key_env or provider_item.api_key_env,
            base_url=base_url if base_url is not None else provider_item.base_url,
            temperature=temperature if temperature is not None else provider_item.temperature,
            top_p=top_p if top_p is not None else provider_item.top_p,
            extra_body=merged_extra_body,
            max_output_tokens=(
                max_output_tokens
                if max_output_tokens is not None
                else provider_item.max_output_tokens or 2048
            ),
        )

    @classmethod
    def create_primary(
        cls,
        settings: Settings,
        *,
        provider_name: str | None = None,
        **overrides,
    ) -> ProviderConfig:
        """根据全局设置构建 primary provider 的运行时配置。"""

        target_name = provider_name or settings.providers.default_primary
        provider_item = cls._find_provider_item(settings, target_name)
        return cls.create(provider_item, **overrides)

    @staticmethod
    def _find_provider_item(settings: Settings, provider_name: str) -> ProviderConfigItem:
        for item in settings.providers.items:
            if item.name == provider_name:
                return item

        raise ValueError(f"Unknown provider: {provider_name}")

    @staticmethod
    def _default_extra_body_for_model(model: str) -> dict[str, Any]:
        """返回模型族默认附加参数。"""

        normalized_model = model.strip().lower()
        if normalized_model.startswith("qwen3.6"):
            return {"enable_thinking": True}
        if normalized_model.startswith("deepseek"):
            return {"thinking": {"type": "disabled"}}

        return {}

    @staticmethod
    def _merge_extra_body(
        *extra_bodies: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """按优先级合并 extra_body。

        优先级从低到高依次为：
        - 模型默认值
        - 静态配置值
        - 运行时 extra_body
        """

        merged: dict[str, Any] = {}
        for item in extra_bodies:
            if not item:
                continue
            merged.update(item)

        if not merged:
            return None

        return merged

    @staticmethod
    def _apply_thinking_override(
        model: str,
        extra_body: dict[str, Any] | None,
        enable_thinking: bool | None,
    ) -> dict[str, Any] | None:
        """把统一 thinking 开关映射到不同模型族的兼容参数。"""

        if enable_thinking is None:
            return extra_body

        merged = dict(extra_body or {})
        normalized_model = model.strip().lower()

        if normalized_model.startswith("deepseek"):
            merged.pop("enable_thinking", None)
            merged["thinking"] = {
                "type": "enabled" if enable_thinking else "disabled",
            }
            return merged

        merged["enable_thinking"] = enable_thinking
        return merged
