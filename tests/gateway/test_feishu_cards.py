"""Feishu 结构化卡片渲染测试。"""

from agent.gateway.feishu_cards import FeishuCardRenderer


def test_feishu_card_renderer_builds_generic_answer_card() -> None:
    """普通问答也应渲染成有标题、引用和正文层次的结构化卡片。"""

    renderer = FeishuCardRenderer(agent_name="Yi Min")

    card = renderer.render_final_card(
        user_text="你有哪些 skills 和工具",
        assistant_text="我目前支持记账、笔记、搜索和文件处理。\n\n你可以直接告诉我任务。",
        tool_calls=[],
        tool_results=[],
    )

    assert card["header"]["title"]["content"] == "Yi Min 回复"
    assert any(element.get("tag") == "note" for element in card["elements"])
    assert any(element.get("tag") == "div" for element in card["elements"])


def test_feishu_card_renderer_marks_failed_tool_result() -> None:
    renderer = FeishuCardRenderer(agent_name="Yi Min")

    card = renderer.render_final_card(
        user_text="设置一个5分钟后的提醒，让我喝水",
        assistant_text="创建提醒失败：提醒时间已过去。",
        tool_calls=[],
        tool_results=[
            {
                "tool_name": "reminder_create",
                "input": {"message": "该喝水了！"},
                "content": '{"error": "提醒时间已过去"}',
            }
        ],
    )

    panel_text = "\n".join(
        element["text"]["content"]
        for panel in card["elements"]
        if panel.get("tag") == "collapsible_panel"
        for element in panel.get("elements", [])
        if element.get("tag") == "div"
    )
    assert "❌" in panel_text
    assert "创建提醒" in panel_text


def test_feishu_card_renderer_builds_ledger_draft_card_from_tool_args() -> None:
    """记账草稿确认应优先渲染成结构化账单卡。"""

    renderer = FeishuCardRenderer(agent_name="Yi Min")

    card = renderer.render_final_card(
        user_text="我中午吃了老乡鸡，27块钱；早餐喝了tims冷萃美式15元",
        assistant_text="两笔草稿都准备好了，需要我提交吗？",
        tool_calls=[
            {
                "tool_name": "ledger_upsert_draft",
                "input": {
                    "thread_id": "default",
                    "direction": "expense",
                    "amount_cent": 2700,
                    "currency": "CNY",
                    "category": "meal",
                    "occurred_at": "2026-04-24T12:30:00+08:00",
                    "merchant": "老乡鸡",
                    "note": "酸菜鱼、鸡腿、狮子头",
                },
            },
            {
                "tool_name": "ledger_upsert_draft",
                "input": {
                    "thread_id": "breakfast",
                    "direction": "expense",
                    "amount_cent": 1500,
                    "currency": "CNY",
                    "category": "meal",
                    "occurred_at": "2026-04-24T08:30:00+08:00",
                    "merchant": "Tims",
                    "note": "冷萃美式",
                },
            },
        ],
        tool_results=[],
    )

    assert card["header"]["title"]["content"] == "记账确认"
    field_texts = [
        field["text"]["content"]
        for element in card["elements"]
        if element.get("tag") == "div"
        for field in element.get("fields", [])
    ]
    assert any("老乡鸡" in text and "¥27.00" in text for text in field_texts)
    assert any("Tims" in text and "¥15.00" in text for text in field_texts)
    assert any("¥42.00" in field["text"]["content"] for element in card["elements"] if element.get("tag") == "div" for field in element.get("fields", []))


def test_feishu_card_renderer_accepts_string_amount_cent_in_ledger_drafts() -> None:
    renderer = FeishuCardRenderer(agent_name="Yi Min")

    card = renderer.render_final_card(
        user_text="奶茶18块",
        assistant_text="草稿准备好了。",
        tool_calls=[
            {
                "tool_name": "ledger_upsert_draft",
                "input": {
                    "direction": "expense",
                    "amount_cent": "1800",
                    "currency": "CNY",
                    "category": "drink",
                    "merchant": "奶茶",
                },
            }
        ],
        tool_results=[],
    )

    assert card["header"]["title"]["content"] == "记账确认"
    assert any(
        "¥18.00" in field["text"]["content"]
        for element in card["elements"]
        if element.get("tag") == "div"
        for field in element.get("fields", [])
    )


def test_feishu_card_renderer_builds_follow_up_card_for_questions() -> None:
    """需要用户确认时，应渲染成更适合回复的追问卡。"""

    renderer = FeishuCardRenderer(agent_name="Yi Min")

    card = renderer.render_final_card(
        user_text="我早餐喝了tims冷萃美式15元",
        assistant_text="午餐草稿已创建。接下来记早餐，但我需要确认一下：\n- 早餐的 Tims 冷萃美式是今天早上喝的吗？",
        tool_calls=[],
        tool_results=[],
    )

    assert card["header"]["title"]["content"] == "需要你确认"
    assert any("今天早上喝的吗" in element["text"]["content"] for element in card["elements"] if element.get("tag") == "div" and "text" in element)


def test_feishu_card_renderer_keeps_answer_with_tag_question_as_generic_reply() -> None:
    """带收尾问句的直接回答，不应被误判为确认卡。"""

    renderer = FeishuCardRenderer(agent_name="Yi Min")

    card = renderer.render_final_card(
        user_text="我是谁",
        assistant_text="你是**腿哥**，我的用户。平时爱喝 Tims 冷萃美式，午餐常吃老乡鸡的酸菜鱼、鸡腿和狮子头。对吧？",
        tool_calls=[],
        tool_results=[],
    )

    assert card["header"]["title"]["content"] == "Yi Min 回复"


def test_feishu_card_renderer_builds_ledger_report_with_summary_and_five_detail_rows() -> None:
    """账本总结应带总览、5条明细和一句洞察，而不是只有一段摘要。"""

    renderer = FeishuCardRenderer(agent_name="Yi Min")

    card = renderer.render_final_card(
        user_text="今天一天的账本总结下",
        assistant_text="你今天的账本总结如下。",
        tool_calls=[],
        tool_results=[
            {
                "tool_name": "ledger_summary",
                "content": '{"entry_count": 6, "expense_cent": 10500, "income_cent": 2000, "net_cent": -8500}',
            },
            {
                "tool_name": "ledger_query_entries",
                "content": "\n".join(
                    [
                        "[2026-04-24T19:30:00+08:00] expense 1800 CNY meal 海底捞",
                        "[2026-04-24T12:30:00+08:00] expense 2700 CNY meal 老乡鸡",
                        "[2026-04-24T10:00:00+08:00] income 2000 CNY salary 公司报销",
                        "[2026-04-24T08:30:00+08:00] expense 1500 CNY meal Tims",
                        "[2026-04-24T07:50:00+08:00] expense 1200 CNY transport 滴滴",
                        "[2026-04-24T00:20:00+08:00] expense 3300 CNY shopping 京东",
                    ]
                ),
            },
        ],
    )

    assert card["header"]["title"]["content"] == "2026年4月24日 账本总览"

    overview_blocks = [
        element
        for element in card["elements"]
        if element.get("tag") == "div" and element.get("fields")
    ]
    assert any("净支出" in field["text"]["content"] for block in overview_blocks for field in block["fields"])

    table_rows = [
        element
        for element in card["elements"]
        if element.get("tag") == "column_set"
    ]
    assert len(table_rows) >= 6

    header_row = table_rows[0]
    header_text = "".join(
        child["text"]["content"]
        for column in header_row["columns"]
        for child in column["elements"]
        if child.get("tag") == "div"
    )
    assert "时间" in header_text and "金额" in header_text

    detail_text = "\n".join(
        child["text"]["content"]
        for row in table_rows[1:6]
        for column in row["columns"]
        for child in column["elements"]
        if child.get("tag") == "div"
    )
    assert "海底捞" in detail_text
    assert "老乡鸡" in detail_text
    assert "公司报销" in detail_text
    assert "京东" not in detail_text

    assert any(
        element.get("tag") == "note" and "海底捞" in "".join(item.get("content", "") for item in element.get("elements", []))
        for element in card["elements"]
    )
