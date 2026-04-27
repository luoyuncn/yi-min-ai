from agent.memory.memory_extractor import MemoryExtractor


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
