"""JSONL ReAct trace logging."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class ReactTraceLogger:
    """Write observable model/tool decisions to a dedicated JSONL file."""

    def __init__(self, log_path: Path, *, max_value_chars: int = 8000) -> None:
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_value_chars = max_value_chars

    def record(self, event: str, **fields: Any) -> None:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            **{key: self._coerce(value) for key, value in fields.items()},
        }
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _coerce(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._truncate(value)
        if isinstance(value, dict):
            return {str(key): self._coerce(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._coerce(item) for item in value]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return self._truncate(str(value))

    def _truncate(self, value: str) -> str:
        if len(value) <= self.max_value_chars:
            return value
        return value[: self.max_value_chars] + "...[truncated]"
