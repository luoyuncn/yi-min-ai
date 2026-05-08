"""Conservative durable-memory extraction."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from agent.core.provider import LLMRequest

logger = logging.getLogger(__name__)


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

    _allowed_kinds = {"profile", "preference", "fact", "plan", "constraint", "relationship"}
    _allowed_importance = {"low", "medium", "high"}

    _nickname_patterns = (
        re.compile(r"(?:以后)?叫我(?P<name>[\u4e00-\u9fa5A-Za-z0-9_-]{1,16})"),
        re.compile(r"我的称呼是(?P<name>[\u4e00-\u9fa5A-Za-z0-9_-]{1,16})"),
        re.compile(r"我(?:就)?是(?P<name>[\u4e00-\u9fa5A-Za-z0-9_-]{1,16})[，,。！!\s]*(?:请)?记住(?:吧|我)?"),
    )

    def __init__(self, provider_manager=None) -> None:
        self.provider_manager = provider_manager

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
            logger.info(
                "event=memory_extract_skipped method=rule reason=small_talk_or_error "
                "thread_id=%s message_id=%s 说明=规则抽取已跳过，原因是寒暄内容或助手报错",
                thread_id,
                message_id,
            )
            return []

        nickname = self._extract_nickname(text)
        if nickname:
            candidates = [
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
            logger.info(
                "event=memory_extract_completed method=rule candidate_count=%s kinds=%s thread_id=%s message_id=%s "
                "说明=规则抽取完成并命中用户称呼类记忆",
                len(candidates),
                ",".join(candidate.kind for candidate in candidates),
                thread_id,
                message_id,
            )
            return candidates

        preference = self._extract_preference(text)
        if preference:
            candidates = [
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
            logger.info(
                "event=memory_extract_completed method=rule candidate_count=%s kinds=%s thread_id=%s message_id=%s "
                "说明=规则抽取完成并命中用户偏好类记忆",
                len(candidates),
                ",".join(candidate.kind for candidate in candidates),
                thread_id,
                message_id,
            )
            return candidates

        explicit_fact = self._extract_explicit_fact(text)
        if explicit_fact:
            candidates = [
                self._candidate(
                    kind="fact",
                    title="用户事实",
                    content=explicit_fact,
                    thread_id=thread_id,
                    message_id=message_id,
                    sender_id=sender_id,
                )
            ]
            logger.info(
                "event=memory_extract_completed method=rule candidate_count=%s kinds=%s thread_id=%s message_id=%s "
                "说明=规则抽取完成并命中显式事实类记忆",
                len(candidates),
                ",".join(candidate.kind for candidate in candidates),
                thread_id,
                message_id,
            )
            return candidates

        logger.info(
            "event=memory_extract_skipped method=rule reason=no_rule_match thread_id=%s message_id=%s "
            "说明=规则抽取未命中任何可保存记忆",
            thread_id,
            message_id,
        )
        return []

    async def extract_async(
        self,
        *,
        user_message: str,
        assistant_message: str,
        thread_id: str,
        message_id: str,
        sender_id: str | None,
        existing_memories: str = "",
    ) -> list[MemoryCandidate]:
        """使用 LLM 抽取长期记忆，失败时退回本地保守规则。"""

        text = user_message.strip()
        if not text or self._is_small_talk(text) or self._is_error_response(assistant_message):
            logger.info(
                "event=memory_extract_skipped method=async reason=small_talk_or_error "
                "thread_id=%s message_id=%s 说明=异步抽取已跳过，原因是寒暄内容或助手报错",
                thread_id,
                message_id,
            )
            return []
        if not self._may_contain_durable_memory(text):
            logger.info(
                "event=memory_extract_skipped method=async reason=durability_heuristic_filtered "
                "thread_id=%s message_id=%s text=%r 说明=异步抽取已跳过，原因是耐久性启发式判定不值得写入",
                thread_id,
                message_id,
                text,
            )
            return []
        if self.provider_manager is None:
            logger.info(
                "event=memory_extract_fallback method=async reason=no_provider thread_id=%s message_id=%s "
                "说明=异步抽取无法调用模型，已回退到本地规则抽取",
                thread_id,
                message_id,
            )
            return self.extract(
                user_message=user_message,
                assistant_message=assistant_message,
                thread_id=thread_id,
                message_id=message_id,
                sender_id=sender_id,
            )

        try:
            logger.info(
                "event=memory_extract_llm_started thread_id=%s message_id=%s existing_memories_chars=%s "
                "说明=开始调用模型执行长期记忆抽取",
                thread_id,
                message_id,
                len(existing_memories or ""),
            )
            candidates = await self._extract_with_llm(
                user_message=user_message,
                assistant_message=assistant_message,
                existing_memories=existing_memories,
                thread_id=thread_id,
                message_id=message_id,
                sender_id=sender_id,
            )
            if candidates:
                logger.info(
                    "event=memory_extract_completed method=llm candidate_count=%s kinds=%s thread_id=%s message_id=%s "
                    "说明=模型抽取完成并得到可保存记忆",
                    len(candidates),
                    ",".join(candidate.kind for candidate in candidates),
                    thread_id,
                    message_id,
                )
                return candidates
        except Exception as exc:
            logger.warning(
                "event=memory_extract_llm_failed thread_id=%s message_id=%s error=%s "
                "说明=模型抽取失败，后续将回退到规则抽取",
                thread_id,
                message_id,
                exc,
            )
            pass
        return self.extract(
            user_message=user_message,
            assistant_message=assistant_message,
            thread_id=thread_id,
            message_id=message_id,
            sender_id=sender_id,
        )

    def build_mem0_messages(self, *, user_message: str, assistant_message: str) -> list[dict[str, str]]:
        """Build a minimal turn payload for Mem0 persistence."""

        return [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_message},
        ]

    async def _extract_with_llm(
        self,
        *,
        user_message: str,
        assistant_message: str,
        existing_memories: str,
        thread_id: str,
        message_id: str,
        sender_id: str | None,
    ) -> list[MemoryCandidate]:
        request = LLMRequest(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是长期记忆抽取器，只能输出 JSON，不要输出解释。\n"
                        "任务：从本轮用户消息和助手回复中识别长期有效、值得以后自动注入上下文的记忆。\n"
                        "重点识别：身份/称呼、偏好、稳定事实、计划、约束、重要关系。\n"
                        "不要保存：一次性闲聊、临时情绪、低把握猜测、纯寒暄、助手自己的身份设定。\n"
                        "如果用户明确说“记住”“以后”“我就是”“我喜欢/不喜欢/更喜欢”等，通常应提高置信度。\n"
                        "如果本轮没有值得长期保留的内容，返回 {\"memories\":[]}，不要猜测，不要硬编。\n"
                        "输出格式必须是："
                        "{\"memories\":[{\"kind\":\"profile|preference|fact|plan|constraint|relationship\","
                        "\"title\":\"短标题\",\"content\":\"完整中文事实\",\"confidence\":0.0,"
                        "\"importance\":\"low|medium|high\"}]}"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"[已有记忆]\n{existing_memories or '无'}\n\n"
                        f"[用户消息]\n{user_message}\n\n"
                        f"[助手回复]\n{assistant_message}\n"
                    ),
                },
            ],
            tools=[],
            max_tokens=600,
            temperature=0,
        )
        response = await self.provider_manager.call(request)
        if response.type != "text" or not response.text:
            logger.info(
                "event=memory_extract_llm_empty_response thread_id=%s message_id=%s response_type=%s has_text=%s "
                "说明=模型抽取返回空响应，未得到可解析文本",
                thread_id,
                message_id,
                response.type,
                bool(getattr(response, "text", "")),
            )
            return []
        payload = self._parse_json_payload(response.text)
        memories = payload.get("memories")
        if not isinstance(memories, list):
            logger.info(
                "event=memory_extract_llm_invalid_payload thread_id=%s message_id=%s payload_keys=%s "
                "说明=模型抽取返回了无法识别的 JSON 结构",
                thread_id,
                message_id,
                ",".join(sorted(str(key) for key in payload.keys())) if isinstance(payload, dict) else "",
            )
            return []
        if not memories:
            logger.info(
                "event=memory_extract_llm_empty_memories thread_id=%s message_id=%s "
                "说明=模型抽取成功返回 JSON，但 memories 数组为空",
                thread_id,
                message_id,
            )
            return []

        candidates: list[MemoryCandidate] = []
        rejected_reasons: list[str] = []
        for item in memories[:5]:
            candidate, rejected_reason = self._candidate_from_llm_item(
                item,
                thread_id=thread_id,
                message_id=message_id,
                sender_id=sender_id,
            )
            if candidate is not None:
                candidates.append(candidate)
            elif rejected_reason is not None:
                rejected_reasons.append(rejected_reason)
        if not candidates:
            logger.info(
                "event=memory_extract_llm_candidates_filtered thread_id=%s message_id=%s raw_count=%s accepted_count=0 reasons=%s "
                "说明=模型返回了候选项，但都被本地校验过滤掉了",
                thread_id,
                message_id,
                min(len(memories), 5),
                ",".join(rejected_reasons) if rejected_reasons else "unknown",
            )
        return candidates

    def _parse_json_payload(self, raw_text: str) -> dict[str, Any]:
        text = raw_text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end >= start:
            text = text[start:end + 1]
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}

    def _candidate_from_llm_item(
        self,
        item: object,
        *,
        thread_id: str,
        message_id: str,
        sender_id: str | None,
    ) -> tuple[MemoryCandidate | None, str | None]:
        if not isinstance(item, dict):
            return None, "non_dict_item"
        kind = str(item.get("kind") or "").strip()
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        importance = str(item.get("importance") or "medium").strip()
        try:
            confidence = float(item.get("confidence", 0))
        except (TypeError, ValueError):
            return None, "invalid_confidence"
        if kind not in self._allowed_kinds:
            return None, "invalid_kind"
        if importance not in self._allowed_importance:
            importance = "medium"
        if not title:
            return None, "missing_title"
        if not content:
            return None, "missing_content"
        if confidence < 0.65:
            return None, "low_confidence"
        if len(title) > 80 or len(content) > 500:
            return None, "too_long"
        return (
            self._candidate(
                kind=kind,
                title=title,
                content=content,
                thread_id=thread_id,
                message_id=message_id,
                sender_id=sender_id,
                confidence=confidence,
                importance=importance,
            ),
            None,
        )

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

    def _may_contain_durable_memory(self, text: str) -> bool:
        if self._extract_nickname(text) or self._extract_preference(text) or self._extract_explicit_fact(text):
            return True
        normalized = text.strip()
        if not normalized or normalized.endswith(("?", "？")):
            return False
        if self._is_small_talk(normalized):
            return False
        # 对非问句陈述，优先交给 LLM 判断是否值得沉淀为长期记忆；
        # 本地规则只保留为兜底，不再用脆弱关键词替代语义判断。
        return True
