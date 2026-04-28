from agent.memory.memory_extractor import MemoryExtractor
from agent.core.provider import LLMResponse
import pytest


class JsonMemoryProvider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.requests = []

    async def call(self, request):
        self.requests.append(request)
        return LLMResponse(type="text", text=self.text)


def test_memory_extractor_saves_explicit_remember_request() -> None:
    extractor = MemoryExtractor()

    candidates = extractor.extract(
        user_message="记住我喜欢 Tims 冷萃美式",
        assistant_message="好的，已记住。",
        thread_id="feishu:feishu:chat-1",
        message_id="msg-1",
        sender_id="ou-user-1",
    )

    assert len(candidates) == 1
    assert candidates[0].kind == "preference"
    assert "Tims 冷萃美式" in candidates[0].content
    assert candidates[0].source_sender_id == "ou-user-1"


def test_memory_extractor_saves_nickname_profile() -> None:
    extractor = MemoryExtractor()

    candidates = extractor.extract(
        user_message="以后叫我腿哥",
        assistant_message="没问题，腿哥。",
        thread_id="thread-1",
        message_id="msg-1",
        sender_id="sender-1",
    )

    assert len(candidates) == 1
    assert candidates[0].kind == "profile"
    assert "腿哥" in candidates[0].content


def test_memory_extractor_saves_explicit_self_nickname_with_remember_suffix() -> None:
    extractor = MemoryExtractor()

    candidates = extractor.extract(
        user_message="不是玩笑，我就是腿哥，记住吧",
        assistant_message="行，腿哥。我记下了。",
        thread_id="thread-1",
        message_id="msg-remember-nickname",
        sender_id="sender-1",
    )

    assert len(candidates) == 1
    assert candidates[0].kind == "profile"
    assert candidates[0].title == "称呼"
    assert "腿哥" in candidates[0].content


@pytest.mark.asyncio
async def test_memory_extractor_uses_llm_for_identity_and_preference_extraction() -> None:
    provider = JsonMemoryProvider(
        """
        {
          "memories": [
            {
              "kind": "profile",
              "title": "称呼",
              "content": "用户希望被称呼为腿哥。",
              "confidence": 0.96,
              "importance": "high"
            },
            {
              "kind": "preference",
              "title": "饮食偏好",
              "content": "用户明天在家休息并计划吃海鲜。",
              "confidence": 0.82,
              "importance": "medium"
            }
          ]
        }
        """
    )
    extractor = MemoryExtractor(provider_manager=provider)

    candidates = await extractor.extract_async(
        user_message="不是玩笑，我就是腿哥，记住吧。明天在家休息吃海鲜。",
        assistant_message="行，腿哥，我记下了。",
        thread_id="thread-1",
        message_id="msg-llm-memory",
        sender_id="sender-1",
    )

    assert [candidate.kind for candidate in candidates] == ["profile", "preference"]
    assert "腿哥" in candidates[0].content
    assert "海鲜" in candidates[1].content
    assert provider.requests
    assert provider.requests[0].tools == []


@pytest.mark.asyncio
async def test_memory_extractor_skips_llm_when_message_has_no_durable_memory() -> None:
    provider = JsonMemoryProvider('{"memories":[]}')
    extractor = MemoryExtractor(provider_manager=provider)

    candidates = await extractor.extract_async(
        user_message="这个怎么用？",
        assistant_message="可以这样使用。",
        thread_id="thread-1",
        message_id="msg-no-memory",
        sender_id="sender-1",
    )

    assert candidates == []
    assert provider.requests == []


@pytest.mark.asyncio
async def test_memory_extractor_falls_back_to_rules_when_llm_returns_no_memories() -> None:
    provider = JsonMemoryProvider('{"memories":[]}')
    extractor = MemoryExtractor(provider_manager=provider)

    candidates = await extractor.extract_async(
        user_message="记住我喜欢 Tims 冷萃美式",
        assistant_message="好的，已记住。",
        thread_id="thread-1",
        message_id="msg-fallback-memory",
        sender_id="sender-1",
    )

    assert len(candidates) == 1
    assert candidates[0].kind == "preference"
    assert "Tims 冷萃美式" in candidates[0].content
    assert provider.requests


def test_memory_extractor_ignores_small_talk_and_provider_errors() -> None:
    extractor = MemoryExtractor()

    assert extractor.extract(
        user_message="hi",
        assistant_message="你好",
        thread_id="thread-1",
        message_id="msg-1",
        sender_id="sender-1",
    ) == []
    assert extractor.extract(
        user_message="查询完整信息吧",
        assistant_message="抱歉，处理您的消息时出错了: Error code: 403",
        thread_id="thread-1",
        message_id="msg-2",
        sender_id="sender-1",
    ) == []
