"""Langfuse tracing adapter using the legacy ingestion endpoint.

Langfuse 3.171 exposes the OpenTelemetry endpoint, but in this deployment the
OTLP exporter returned server errors/timeouts for real spans. The legacy
`/api/public/ingestion` endpoint is stable and gives us explicit control over
batching, timeouts, and request-path blocking.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from contextvars import ContextVar
from datetime import UTC, datetime
import base64
import json
import logging
import os
from random import random
import threading
import urllib.error
import urllib.request
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

_observation_stack: ContextVar[tuple[dict[str, str], ...]] = ContextVar(
    "langfuse_observation_stack",
    default=(),
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class NoopObservation(AbstractContextManager):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def update(self, **fields: Any) -> None:
        return None


class NoopTraceClient:
    enabled = False
    flush_on_run_end = False

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

    def flush_async(self) -> None:
        return None


class _LegacyObservation(AbstractContextManager):
    def __init__(
        self,
        client: "LangfuseTraceClient",
        *,
        observation_id: str,
        trace_id: str,
        name: str,
        kind: str,
    ) -> None:
        self._client = client
        self._id = observation_id
        self._trace_id = trace_id
        self._name = name
        self._kind = kind
        self._token = None

    def __enter__(self):
        stack = _observation_stack.get()
        self._token = _observation_stack.set(
            (*stack, {"id": self._id, "trace_id": self._trace_id, "kind": self._kind})
        )
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None:
            self.update(level="ERROR", status_message=str(exc))
        if self._kind != "trace":
            self.update(end_time=_now_iso())
        if self._token is not None:
            _observation_stack.reset(self._token)
        return False

    def update(self, **fields: Any) -> None:
        self._client.update_observation(
            observation_id=self._id,
            trace_id=self._trace_id,
            name=self._name,
            kind=self._kind,
            **fields,
        )


class LangfuseTraceClient:
    enabled = True

    def __init__(
        self,
        *,
        public_key: str,
        secret_key: str,
        base_url: str,
        timeout_seconds: int = 15,
        flush_at: int = 32,
        max_value_chars: int = 12000,
        sample_rate: float = 1.0,
        flush_on_run_end: bool = False,
    ) -> None:
        self.public_key = public_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.flush_at = flush_at
        self._max_value_chars = max_value_chars
        self._sampled = random() <= max(0.0, min(1.0, sample_rate))
        self.flush_on_run_end = flush_on_run_end
        self._auth_header = "Basic " + base64.b64encode(
            f"{public_key}:{secret_key}".encode("utf-8")
        ).decode("ascii")
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []
        self._flush_thread: threading.Thread | None = None

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

        tracer = cls(
            public_key=public_key,
            secret_key=secret_key,
            base_url=langfuse_settings.base_url,
            timeout_seconds=langfuse_settings.timeout_seconds,
            flush_at=langfuse_settings.flush_at,
            max_value_chars=langfuse_settings.max_field_chars,
            sample_rate=langfuse_settings.sample_rate,
            flush_on_run_end=langfuse_settings.flush_on_run_end,
        )
        logger.info(
            "event=langfuse_enabled mode=legacy_ingestion base_url=%s timeout_seconds=%s flush_at=%s "
            "flush_on_run_end=%s",
            langfuse_settings.base_url,
            langfuse_settings.timeout_seconds,
            langfuse_settings.flush_at,
            langfuse_settings.flush_on_run_end,
        )
        return tracer

    def start_trace(self, name: str, **fields: Any):
        if not self._sampled:
            return NoopObservation()

        trace_id = self._metadata_value(fields, "trace_id") or str(uuid4())
        body = {
            "id": trace_id,
            "timestamp": _now_iso(),
            "name": name,
            "input": fields.get("input"),
            "metadata": fields.get("metadata"),
            "sessionId": self._metadata_value(fields, "session_id"),
            "userId": self._metadata_value(fields, "sender"),
        }
        self._enqueue("trace-create", body)
        return _LegacyObservation(self, observation_id=trace_id, trace_id=trace_id, name=name, kind="trace")

    def start_span(self, name: str, **fields: Any):
        return self._start_observation("span", "span-create", name, **fields)

    def start_generation(self, name: str, **fields: Any):
        return self._start_observation("generation", "generation-create", name, **fields)

    def start_tool(self, name: str, **fields: Any):
        return self._start_observation("tool", "span-create", name, **fields)

    def update_observation(
        self,
        *,
        observation_id: str,
        trace_id: str,
        name: str,
        kind: str,
        **fields: Any,
    ) -> None:
        if not self._sampled:
            return
        if kind == "trace":
            body = {
                "id": trace_id,
                "name": name,
                "output": fields.get("output"),
                "metadata": fields.get("metadata"),
            }
            if fields.get("level"):
                body["metadata"] = {**(body.get("metadata") or {}), "level": fields.get("level")}
            if fields.get("status_message"):
                body["metadata"] = {
                    **(body.get("metadata") or {}),
                    "statusMessage": fields.get("status_message"),
                }
            self._enqueue("trace-create", body)
            return

        event_type = {
            "span": "span-update",
            "generation": "generation-update",
            "tool": "span-update",
        }[kind]
        body = {
            "id": observation_id,
            "traceId": trace_id,
            "name": name,
            "output": fields.get("output"),
            "metadata": fields.get("metadata"),
            "level": fields.get("level"),
            "statusMessage": fields.get("status_message"),
            "endTime": fields.get("end_time"),
        }
        if kind == "generation":
            body["model"] = fields.get("model")
            body["usageDetails"] = fields.get("usage_details")
        self._enqueue(event_type, body)

    def flush(self) -> None:
        events = self._pop_events()
        if events:
            self._send_batch(events)

    def flush_async(self) -> None:
        if self._flush_thread is not None and self._flush_thread.is_alive():
            return
        events = self._pop_events()
        if not events:
            return
        self._flush_thread = threading.Thread(target=self._send_batch, args=(events,), daemon=True)
        self._flush_thread.start()

    def _start_observation(self, kind: str, event_type: str, name: str, **fields: Any):
        if not self._sampled:
            return NoopObservation()

        stack = _observation_stack.get()
        parent = stack[-1] if stack else {}
        trace_id = parent.get("trace_id") or self._metadata_value(fields, "trace_id") or str(uuid4())
        observation_id = str(uuid4())
        body = {
            "id": observation_id,
            "traceId": trace_id,
            "name": name,
            "startTime": _now_iso(),
            "input": fields.get("input"),
            "metadata": fields.get("metadata"),
            "parentObservationId": parent.get("id") if parent.get("kind") != "trace" else None,
        }
        self._enqueue(event_type, body)
        return _LegacyObservation(
            self,
            observation_id=observation_id,
            trace_id=trace_id,
            name=name,
            kind=kind,
        )

    def _enqueue(self, event_type: str, body: dict[str, Any]) -> None:
        event = {
            "id": str(uuid4()),
            "timestamp": _now_iso(),
            "type": event_type,
            "body": self._sanitize({key: value for key, value in body.items() if value is not None}),
        }
        with self._lock:
            self._events.append(event)
            should_flush = len(self._events) >= self.flush_at
        if should_flush:
            self.flush_async()

    def _pop_events(self) -> list[dict[str, Any]]:
        with self._lock:
            events = self._events
            self._events = []
        return events

    def _send_batch(self, events: list[dict[str, Any]]) -> None:
        payload = json.dumps({"batch": events}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/public/ingestion",
            data=payload,
            method="POST",
            headers={
                "Authorization": self._auth_header,
                "Content-Type": "application/json",
                "x-langfuse-sdk-name": "yi-min-ai",
                "x-langfuse-public-key": self.public_key,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                response_body = response.read(4096)
                if response.status not in {200, 207}:
                    logger.warning("event=langfuse_ingestion_failed status=%s", response.status)
                    return
                if response.status == 207 and b'"errors":[]' not in response_body:
                    logger.warning("event=langfuse_ingestion_partial response=%s", response_body.decode("utf-8", "ignore"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("event=langfuse_ingestion_failed error=%s", exc)

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

    def _metadata_value(self, fields: dict[str, Any], key: str) -> Any:
        metadata = fields.get("metadata") or {}
        return metadata.get(key)
