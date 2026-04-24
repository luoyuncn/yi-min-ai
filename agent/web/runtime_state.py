"""Web runtime 的短生命周期状态。"""

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4


class RunInterrupted(RuntimeError):
    """表示一次运行被外部 interrupt 请求中止。"""


@dataclass(slots=True)
class RunControl:
    """单次 run 的协作式中断控制器。"""

    thread_id: str
    run_id: str
    interrupted_at: datetime | None = None
    reason: str | None = None

    @property
    def is_interrupted(self) -> bool:
        return self.interrupted_at is not None

    def interrupt(self, reason: str = "user requested interrupt") -> None:
        self.interrupted_at = datetime.now(UTC)
        self.reason = reason

    def ensure_active(self) -> None:
        if self.is_interrupted:
            raise RunInterrupted(self.reason or "run interrupted")


class RunControlRegistry:
    """记录当前活跃 run 的控制器。"""

    def __init__(self) -> None:
        self._controls: dict[str, RunControl] = {}

    def start(self, thread_id: str, run_id: str) -> RunControl:
        control = RunControl(thread_id=thread_id, run_id=run_id)
        self._controls[run_id] = control
        return control

    def get(self, run_id: str) -> RunControl | None:
        return self._controls.get(run_id)

    def finish(self, run_id: str) -> None:
        self._controls.pop(run_id, None)


@dataclass(slots=True)
class PendingApproval:
    """待审批的中断点。"""

    approval_id: str
    thread_id: str
    run_id: str
    tool_call: dict
    context: list[dict]
    message: str
    thread_aliases: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class PendingApprovalStore:
    """按 approval_id / thread_id 管理待审批状态。"""

    def __init__(self) -> None:
        self._by_id: dict[str, PendingApproval] = {}
        self._thread_index: dict[str, str] = {}

    def create(
        self,
        *,
        thread_id: str,
        run_id: str,
        tool_call: dict,
        context: list[dict],
        message: str,
        aliases: list[str] | tuple[str, ...] | None = None,
    ) -> PendingApproval:
        thread_keys = self._normalize_thread_keys(thread_id, aliases)
        existing_id = next(
            (self._thread_index[key] for key in thread_keys if key in self._thread_index),
            None,
        )
        if existing_id is not None:
            self.resolve(existing_id)

        approval = PendingApproval(
            approval_id=str(uuid4()),
            thread_id=thread_id,
            run_id=run_id,
            tool_call=deepcopy(tool_call),
            context=deepcopy(context),
            message=message,
            thread_aliases=thread_keys,
        )
        self._by_id[approval.approval_id] = approval
        for key in thread_keys:
            self._thread_index[key] = approval.approval_id
        return approval

    def get(self, approval_id: str) -> PendingApproval | None:
        return self._by_id.get(approval_id)

    def get_by_thread(self, thread_id: str) -> PendingApproval | None:
        approval_id = self._thread_index.get(thread_id)
        if approval_id is None:
            return None
        return self._by_id.get(approval_id)

    def resolve(self, approval_id: str) -> PendingApproval | None:
        approval = self._by_id.pop(approval_id, None)
        if approval is None:
            return None
        for key in approval.thread_aliases:
            if self._thread_index.get(key) == approval_id:
                self._thread_index.pop(key, None)
        return approval

    def _normalize_thread_keys(
        self,
        thread_id: str,
        aliases: list[str] | tuple[str, ...] | None,
    ) -> tuple[str, ...]:
        ordered = [thread_id]
        for alias in aliases or ():
            if alias and alias not in ordered:
                ordered.append(alias)
        return tuple(ordered)
