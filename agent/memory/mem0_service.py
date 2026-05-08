"""Thin Mem0 service boundary for long-term memory retrieval and writes."""

from __future__ import annotations


class Mem0MemoryService:
    """Wrap Mem0 client calls behind a stable local interface."""

    def __init__(self, *, enabled: bool, agent_id: str, client=None) -> None:
        self.enabled = enabled
        self.agent_id = agent_id
        self.client = client

    @property
    def is_ready(self) -> bool:
        return self.enabled and self.client is not None

    def add(self, messages: list[dict] | str, *, user_id: str, run_id: str, infer: bool = True, metadata=None) -> dict:
        if not self.enabled:
            return self._failure("Mem0 is disabled")
        if self.client is None:
            return self._failure("Mem0 client is not configured")
        try:
            raw = self.client.add(
                messages,
                user_id=user_id,
                agent_id=self.agent_id,
                run_id=run_id,
                infer=infer,
                metadata=metadata,
            )
        except Exception as exc:
            return self._failure(str(exc))
        return self._success(raw)

    def add_memory_items(self, items: list[dict], *, user_id: str, run_id: str) -> dict:
        if not self.enabled:
            return self._failure("Mem0 is disabled")
        if self.client is None:
            return self._failure("Mem0 client is not configured")

        try:
            results: list[dict] = []
            raw_results: list[object] = []
            for item in items:
                content = str(item.get("content") or "").strip()
                if not content:
                    continue
                metadata = {
                    key: value
                    for key, value in item.items()
                    if key != "content" and value is not None
                }
                raw = self.client.add(
                    content,
                    user_id=user_id,
                    agent_id=self.agent_id,
                    run_id=run_id,
                    infer=False,
                    metadata=metadata or None,
                )
                raw_results.append(raw)
                results.extend(_normalize_results(raw))
        except Exception as exc:
            return self._failure(str(exc))

        return {
            "ok": True,
            "results": results,
            "error": None,
            "raw": raw_results,
        }

    def add_conversation(
        self,
        *,
        user_message: str,
        assistant_message: str,
        user_id: str,
        run_id: str,
    ) -> dict:
        """Pass a raw conversation turn to mem0 with infer=True.

        mem0 internally extracts facts, deduplicates, and resolves conflicts
        with existing memories before persisting.
        """
        if not self.enabled:
            return self._failure("Mem0 is disabled")
        if self.client is None:
            return self._failure("Mem0 client is not configured")
        messages = [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_message},
        ]
        try:
            raw = self.client.add(
                messages,
                user_id=user_id,
                agent_id=self.agent_id,
                run_id=run_id,
                infer=True,
            )
        except Exception as exc:
            return self._failure(str(exc))
        return self._success(raw)

    def search(self, query: str, *, user_id: str, run_id: str, top_k: int = 5) -> dict:
        if not self.enabled:
            return self._failure("Mem0 is disabled")
        if self.client is None:
            return self._failure("Mem0 client is not configured")
        try:
            raw = self.client.search(
                query,
                top_k=top_k,
                filters={
                    "user_id": user_id,
                    "agent_id": self.agent_id,
                },
            )
        except Exception as exc:
            return self._failure(str(exc))
        return self._success(raw)

    def get_all(self, *, user_id: str, run_id: str | None = None, top_k: int = 20) -> dict:
        if not self.enabled:
            return self._failure("Mem0 is disabled")
        if self.client is None:
            return self._failure("Mem0 client is not configured")
        filters = {
            "user_id": user_id,
            "agent_id": self.agent_id,
        }
        if run_id:
            filters["run_id"] = run_id
        try:
            raw = self.client.get_all(
                filters=filters,
                top_k=top_k,
            )
        except Exception as exc:
            return self._failure(str(exc))
        return self._success(raw)

    def delete(self, memory_id: str) -> dict:
        if not self.enabled:
            return self._failure("Mem0 is disabled")
        if self.client is None:
            return self._failure("Mem0 client is not configured")
        try:
            raw = self.client.delete(memory_id)
        except Exception as exc:
            return self._failure(str(exc))
        return self._success(raw)

    def build_context_block(self, *, query: str, user_id: str, run_id: str, top_k: int = 5) -> str:
        outcome = self.search(query, user_id=user_id, run_id=run_id, top_k=top_k)
        rows = outcome.get("results") or []
        if not rows:
            fallback = self.get_all(user_id=user_id, top_k=top_k)
            if fallback.get("ok"):
                rows = fallback.get("results") or []
        return self.build_context_block_from_rows(rows, top_k=top_k)

    def build_context_block_from_rows(self, rows: list[dict], *, top_k: int = 5) -> str:
        lines: list[str] = []
        for row in rows[:top_k]:
            text = _result_text(row)
            if text:
                lines.append(f"- {text}")
        return "\n".join(lines)

    def _success(self, raw) -> dict:
        return {
            "ok": True,
            "results": _normalize_results(raw),
            "error": None,
            "raw": raw,
        }

    def _failure(self, error: str) -> dict:
        return {
            "ok": False,
            "results": [],
            "error": error,
            "raw": None,
        }


def _normalize_results(raw) -> list[dict]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        nested = raw.get("results")
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _result_text(row: dict) -> str:
    for key in ("memory", "content", "text", "summary"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
