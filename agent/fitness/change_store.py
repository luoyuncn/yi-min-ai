"""Transient pending confirmations for fitness domain changes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class FitnessPendingChange:
    thread_key: str
    sender: str | None
    target: str
    updates: dict
    summary: str


class FitnessPendingChangeStore:
    """Keep the latest pending fitness change per session."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str | None], FitnessPendingChange] = {}

    def stage(
        self,
        thread_key: str,
        *,
        sender: str | None,
        target: str,
        updates: dict,
        summary: str,
    ) -> FitnessPendingChange:
        item = FitnessPendingChange(
            thread_key=thread_key,
            sender=sender,
            target=target,
            updates=dict(updates),
            summary=summary,
        )
        self._items[(thread_key, sender)] = item
        return item

    def get(self, thread_key: str, *, sender: str | None) -> FitnessPendingChange | None:
        return self._items.get((thread_key, sender))

    def clear(self, thread_key: str, *, sender: str | None) -> None:
        self._items.pop((thread_key, sender), None)
