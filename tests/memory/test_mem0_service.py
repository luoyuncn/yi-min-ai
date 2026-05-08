from agent.memory.mem0_service import Mem0MemoryService


class FakeMem0Client:
    def __init__(self) -> None:
        self.add_calls: list[dict] = []
        self.search_calls: list[dict] = []
        self.get_all_calls: list[dict] = []
        self.search_results = [{"id": "m1", "memory": "用户喜欢 Tims 冷萃美式。"}]
        self.add_result = {"results": [{"id": "m1"}]}
        self.get_all_result = [{"id": "m1", "memory": "用户喜欢 Tims 冷萃美式。"}]

    def add(self, messages, **kwargs):
        self.add_calls.append({"messages": messages, **kwargs})
        return self.add_result

    def search(self, query: str, **kwargs):
        self.search_calls.append({"query": query, **kwargs})
        return self.search_results

    def get_all(self, **kwargs):
        self.get_all_calls.append(kwargs)
        return self.get_all_result


def test_mem0_service_returns_structured_disabled_outcome() -> None:
    service = Mem0MemoryService(enabled=False, agent_id="yi-min")

    result = service.search("我喜欢喝什么", user_id="ou-1", run_id="thread-1")

    assert result["ok"] is False
    assert result["results"] == []
    assert "disabled" in result["error"].lower()


def test_mem0_service_adds_memories_with_user_and_run_scope() -> None:
    client = FakeMem0Client()
    service = Mem0MemoryService(enabled=True, agent_id="yi-min", client=client)

    result = service.add(
        [{"role": "user", "content": "记住我喜欢 Tims 冷萃美式"}],
        user_id="ou-123",
        run_id="feishu:feishu:chat-1",
    )

    assert result["ok"] is True
    assert client.add_calls == [
        {
            "messages": [{"role": "user", "content": "记住我喜欢 Tims 冷萃美式"}],
            "user_id": "ou-123",
            "agent_id": "yi-min",
            "run_id": "feishu:feishu:chat-1",
            "infer": True,
            "metadata": None,
        }
    ]


def test_mem0_service_searches_by_sender_scope() -> None:
    client = FakeMem0Client()
    service = Mem0MemoryService(enabled=True, agent_id="yi-min", client=client)

    result = service.search("我喜欢喝什么", user_id="ou-123", run_id="feishu:feishu:chat-1")

    assert result["ok"] is True
    assert result["results"][0]["memory"] == "用户喜欢 Tims 冷萃美式。"
    assert client.search_calls == [
        {
            "query": "我喜欢喝什么",
            "top_k": 5,
            "filters": {
                "user_id": "ou-123",
                "agent_id": "yi-min",
            },
        }
    ]


def test_mem0_service_lists_all_user_memories() -> None:
    client = FakeMem0Client()
    service = Mem0MemoryService(enabled=True, agent_id="yi-min", client=client)

    result = service.get_all(user_id="ou-123")

    assert result["ok"] is True
    assert result["results"][0]["memory"] == "用户喜欢 Tims 冷萃美式。"
    assert client.get_all_calls == [{"filters": {"user_id": "ou-123", "agent_id": "yi-min"}, "top_k": 20}]


def test_mem0_service_adds_structured_memory_items_without_infer() -> None:
    client = FakeMem0Client()
    service = Mem0MemoryService(enabled=True, agent_id="yi-min", client=client)

    result = service.add_memory_items(
        [
            {
                "kind": "profile",
                "title": "职业",
                "content": "用户是 AI Agent 开发工程师。",
                "confidence": 0.91,
                "importance": "high",
            }
        ],
        user_id="ou-123",
        run_id="feishu:feishu:chat-1",
    )

    assert result["ok"] is True
    assert client.add_calls == [
        {
            "messages": "用户是 AI Agent 开发工程师。",
            "user_id": "ou-123",
            "agent_id": "yi-min",
            "run_id": "feishu:feishu:chat-1",
            "infer": False,
            "metadata": {
                "kind": "profile",
                "title": "职业",
                "confidence": 0.91,
                "importance": "high",
            },
        }
    ]


def test_mem0_service_builds_context_block_from_search_results() -> None:
    client = FakeMem0Client()
    service = Mem0MemoryService(enabled=True, agent_id="yi-min", client=client)

    text = service.build_context_block(
        query="我喜欢喝什么",
        user_id="ou-123",
        run_id="feishu:feishu:chat-1",
    )

    assert "Tims 冷萃美式" in text


def test_mem0_service_builds_context_block_from_recent_fallback_when_search_is_empty() -> None:
    client = FakeMem0Client()
    client.search_results = []
    client.get_all_result = [{"id": "m2", "memory": "用户是 AI Agent 开发工程师。"}]
    service = Mem0MemoryService(enabled=True, agent_id="yi-min", client=client)

    text = service.build_context_block(
        query="我是做什么的",
        user_id="ou-123",
        run_id="feishu:feishu:chat-1",
    )

    assert "AI Agent 开发工程师" in text


def test_add_conversation_calls_client_with_infer_true():
    """add_conversation 应以 infer=True 调用 client.add，传入完整对话消息列表。"""
    from unittest.mock import MagicMock
    mock_client = MagicMock()
    mock_client.add.return_value = {"results": [{"memory": "用户喜欢 Python"}]}
    service = Mem0MemoryService(enabled=True, agent_id="test-agent", client=mock_client)

    result = service.add_conversation(
        user_message="我喜欢用 Python 写代码",
        assistant_message="好的，我记住了",
        user_id="user-1",
        run_id="thread-abc",
    )

    mock_client.add.assert_called_once_with(
        [
            {"role": "user", "content": "我喜欢用 Python 写代码"},
            {"role": "assistant", "content": "好的，我记住了"},
        ],
        user_id="user-1",
        agent_id="test-agent",
        run_id="thread-abc",
        infer=True,
    )
    assert result["ok"] is True


def test_add_conversation_returns_failure_when_disabled():
    from agent.memory.mem0_service import Mem0MemoryService
    service = Mem0MemoryService(enabled=False, agent_id="test-agent", client=None)
    result = service.add_conversation(
        user_message="x", assistant_message="y", user_id="u", run_id="r"
    )
    assert result["ok"] is False
    assert "disabled" in result["error"]


def test_add_conversation_returns_failure_when_client_raises():
    from unittest.mock import MagicMock
    from agent.memory.mem0_service import Mem0MemoryService
    mock_client = MagicMock()
    mock_client.add.side_effect = RuntimeError("Qdrant unavailable")
    service = Mem0MemoryService(enabled=True, agent_id="test-agent", client=mock_client)

    result = service.add_conversation(
        user_message="x", assistant_message="y", user_id="u", run_id="r"
    )
    assert result["ok"] is False
    assert "Qdrant unavailable" in result["error"]
