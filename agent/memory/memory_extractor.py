"""Conservative durable-memory extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class MemoryCandidate:
    kind: str
    title: str
    content: str
    confidence: float = 0.8
    importance: str = "medium"
    source_thread_id: str | None = None
    source_message_id: str | None = None
    source_sender_id: str | None = None


class MemoryExtractor:
    """Extract only explicit or high-confidence durable memories."""

    _nickname_patterns = (
        re.compile(r"(?:以后)?叫我(?P<name>[\u4e00-\u9fa5A-Za-z0-9_-]{1,16})"),
        re.compile(r"我的称呼是(?P<name>[\u4e00-\u9fa5A-Za-z0-9_-]{1,16})"),
    )

    def extract(
        self,
        *,
        user_message: str,
        assistant_message: str,
        thread_id: str,
        message_id: str,
        sender_id: str | None,
    ) -> list[MemoryCandidate]:
        text = user_message.strip()
        if not text or self._is_small_talk(text) or self._is_error_response(assistant_message):
            return []

        nickname = self._extract_nickname(text)
        if nickname:
            return [
                self._candidate(
                    kind="profile",
                    title="称呼",
                    content=f"用户希望被称呼为{nickname}。",
                    thread_id=thread_id,
                    message_id=message_id,
                    sender_id=sender_id,
                    importance="high",
                    confidence=0.95,
                )
            ]

        preference = self._extract_preference(text)
        if preference:
            return [
                self._candidate(
                    kind="preference",
                    title="偏好",
                    content=preference,
                    thread_id=thread_id,
                    message_id=message_id,
                    sender_id=sender_id,
                    confidence=0.9 if text.startswith("记住") else 0.8,
                )
            ]

        explicit_fact = self._extract_explicit_fact(text)
        if explicit_fact:
            return [
                self._candidate(
                    kind="fact",
                    title="用户事实",
                    content=explicit_fact,
                    thread_id=thread_id,
                    message_id=message_id,
                    sender_id=sender_id,
                )
            ]

        return []

    def _candidate(
        self,
        *,
        kind: str,
        title: str,
        content: str,
        thread_id: str,
        message_id: str,
        sender_id: str | None,
        confidence: float = 0.8,
        importance: str = "medium",
    ) -> MemoryCandidate:
        return MemoryCandidate(
            kind=kind,
            title=title,
            content=content,
            confidence=confidence,
            importance=importance,
            source_thread_id=thread_id,
            source_message_id=message_id,
            source_sender_id=sender_id,
        )

    def _extract_nickname(self, text: str) -> str | None:
        for pattern in self._nickname_patterns:
            match = pattern.search(text)
            if match:
                return match.group("name")
        return None

    def _extract_preference(self, text: str) -> str | None:
        normalized = text.removeprefix("记住").strip(" ：:")
        for marker in ("我喜欢", "我不喜欢", "我更喜欢"):
            if marker in normalized:
                return normalized
        return None

    def _extract_explicit_fact(self, text: str) -> str | None:
        if not text.startswith("记住"):
            return None
        fact = text.removeprefix("记住").strip(" ：:")
        return fact or None

    def _is_small_talk(self, text: str) -> bool:
        return text.lower() in {"hi", "hello", "你好", "在吗", "嗯", "好"}

    def _is_error_response(self, assistant_message: str) -> bool:
        return "处理您的消息时出错" in assistant_message or "Error code:" in assistant_message
