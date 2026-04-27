"""Langfuse tracing adapter.

The agent runtime should not depend on Langfuse being installed or reachable.
This module keeps the integration behind a tiny context-manager based surface so
tests and local runs without credentials still behave normally.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
import logging
import os
from random import random
from typing import Any

logger = logging.getLogger(__name__)


class NoopObservation(AbstractContextManager):
    """Context manager with the same shape as a trace/span/generation."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def update(self, **fields: Any) -> None:
        return None


class NoopTraceClient:
    """Disabled tracer used when Langfuse is unavailable or not configured."""

    enabled = False

    def start_trace(self, name: str, **fields: Any):
        return NoopObservation()

    def start_span(self, name: str, **fields: Any):
        return NoopObservation()

    def start_generation(self, name: str, **fields: Any):
        return NoopObservation()

    def start_tool(self, name: str, **fields: Any):
        return NoopObservation()

    def flush(self) -> None:
        return None


class _LangfuseObservation(AbstractContextManager):
    def __init__(self, context_manager, *, max_value_chars: int) -> None:
        self._context_manager = context_manager
        self._observation = None
        self._max_value_chars = max_value_chars

    def __enter__(self):
        self._observation = self._context_manager.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None:
            self.update(level="ERROR", status_message=str(exc))
        return self._context_manager.__exit__(exc_type, exc, tb)

    def update(self, **fields: Any) -> None:
        if self._observation is None:
            return
        try:
            self._observation.update(**self._sanitize(fields))
        except Exception as exc:  # pragma: no cover - Langfuse SDK/runtime defensive path
            logger.warning("event=langfuse_observation_update_failed error=%s", exc)

    def _sanitize(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._truncate(value)
        if isinstance(value, dict):
            return {str(key): self._sanitize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._sanitize(item) for item in value]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return self._truncate(str(value))

    def _truncate(self, value: str) -> str:
        if len(value) <= self._max_value_chars:
            return value
        return value[: self._max_value_chars] + "...[truncated]"


class LangfuseTraceClient:
    """Small adapter around the Langfuse Python SDK."""

    enabled = True

    def __init__(self, client, *, max_value_chars: int = 12000, sample_rate: float = 1.0) -> None:
        self._client = client
        self._max_value_chars = max_value_chars
        self._sampled = random() <= max(0.0, min(1.0, sample_rate))

    @classmethod
    def from_settings(cls, settings) -> "LangfuseTraceClient | NoopTraceClient":
        langfuse_settings = getattr(getattr(settings, "observability", None), "langfuse", None)
        if langfuse_settings is None or not langfuse_settings.enabled:
            return NoopTraceClient()

        public_key = os.environ.get(langfuse_settings.public_key_env)
        secret_key = os.environ.get(langfuse_settings.secret_key_env)
        if not public_key or not secret_key:
            logger.warning(
                "event=langfuse_disabled_missing_credentials public_key_env=%s secret_key_env=%s",
                langfuse_settings.public_key_env,
                langfuse_settings.secret_key_env,
            )
            return NoopTraceClient()

        os.environ.setdefault("LANGFUSE_PUBLIC_KEY", public_key)
        os.environ.setdefault("LANGFUSE_SECRET_KEY", secret_key)
        os.environ.setdefault("LANGFUSE_BASE_URL", langfuse_settings.base_url)

        try:
            from langfuse import get_client
        except Exception as exc:  # pragma: no cover - depends on optional package install
            logger.warning("event=langfuse_disabled_import_failed error=%s", exc)
            return NoopTraceClient()

        try:
            client = get_client()
        except Exception as exc:  # pragma: no cover - defensive path
            logger.warning("event=langfuse_disabled_init_failed error=%s", exc)
            return NoopTraceClient()

        return cls(
            client,
            max_value_chars=langfuse_settings.max_field_chars,
            sample_rate=langfuse_settings.sample_rate,
        )

    def start_trace(self, name: str, **fields: Any):
        return self._start(name=name, as_type="span", **fields)

    def start_span(self, name: str, **fields: Any):
        return self._start(name=name, as_type="span", **fields)

    def start_generation(self, name: str, **fields: Any):
        return self._start(name=name, as_type="generation", **fields)

    def start_tool(self, name: str, **fields: Any):
        return self._start(name=name, as_type="tool", **fields)

    def flush(self) -> None:
        if not self._sampled:
            return
        try:
            self._client.flush()
        except Exception as exc:  # pragma: no cover - network defensive path
            logger.warning("event=langfuse_flush_failed error=%s", exc)

    def _start(self, *, name: str, as_type: str, **fields: Any):
        if not self._sampled:
            return NoopObservation()
        try:
            context_manager = self._client.start_as_current_observation(
                name=name,
                as_type=as_type,
                **fields,
            )
            return _LangfuseObservation(context_manager, max_value_chars=self._max_value_chars)
        except Exception as exc:  # pragma: no cover - Langfuse SDK/runtime defensive path
            logger.warning("event=langfuse_observation_start_failed name=%s type=%s error=%s", name, as_type, exc)
            return NoopObservation()

