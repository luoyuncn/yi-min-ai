"""Runtime context passed to tools that need request-scoped state."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class RuntimeServices:
    """Mutable service references shared between runtime assembly and tools."""

    cron_scheduler: Any | None = None
    reminder_scheduler: Any | None = None


@dataclass(slots=True)
class RuntimeToolContext:
    """Request metadata available to context-aware tools."""

    workspace_dir: Path
    run_id: str
    channel: str
    channel_instance: str
    session_id: str
    sender: str | None
    metadata: dict
