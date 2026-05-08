"""Feishu JSON 2.0 卡片渲染测试。

新结构约定：
- card["schema"] == "2.0"
- card["body"]["elements"] 是元素列表
- 每张卡片仅有一个 {"tag": "markdown", "content": "..."} 元素
- 所有断言基于 content 字符串内容
"""

from agent.gateway.feishu_cards import FeishuCardRenderer


def _content(card: dict) -> str:
    """取卡片唯一 markdown 元素的内容。"""
    return card["body"]["elements"][0]["content"]


def _assert_v2(card: dict) -> None:
    """断言卡片是合法的 JSON 2.0 结构。"""
    assert card.get("schema") == "2.0"
    assert "body" in card
    assert "elements" in card["body"]
    assert len(card["body"]["elements"]) >= 1
    assert card["body"]["elements"][0]["tag"] == "markdown"


# ------------------------------------------------------------------ #
# 通用问答卡                                                            #
# ------------------------------------------------------------------ #

def test_generic_answer_card_structure() -> None:
    renderer = FeishuCardRenderer(agent_name="Yi Min")
    card = renderer.render_final_card(
        user_text="你有哪些 skills 和工具",
        assistant_text="我目前支持记账、笔记、搜索和文件处理。\n\n你可以直接告诉我任务。",
        tool_calls=[],
        tool_results=[],
    )
    _assert_v2(card)
    assert card["header"]["title"]["content"] == "Yi Min 回复"
    content = _content(card)
    assert "你有哪些 skills 和工具" in content
    assert "记账、笔记、搜索和文件处理" in content
    assert "直接回复我就行" in content


def test_generic_answer_card_agent_name_in_title() -> None:
    renderer = FeishuCardRenderer(agent_name="银月")
    card = renderer.render_final_card(
        user_text="hi", assistant_text="你好。", tool_calls=[], tool_results=[]
    )
    assert card["header"]["title"]["content"] == "银月 回复"


def test_generic_answer_user_quote_is_blockquote() -> None:
    renderer = FeishuCardRenderer()
    card = renderer.render_final_card(
        user_text="今天天气如何", assistant_text="今天晴天。", tool_calls=[], tool_results=[]
    )
    content = _content(card)
    assert content.startswith("> 你：今天天气如何")


# ------------------------------------------------------------------ #
# 工具调用标记                                                           #
# ------------------------------------------------------------------ #

def test_tool_trace_success_shown_in_content() -> None:
    renderer = FeishuCardRenderer()
    card = renderer.render_final_card(
        user_text="搜索一下",
        assistant_text="搜索结果如下。",
        tool_calls=[],
        tool_results=[
            {"tool_name": "web_search", "input": {"query": "天气"}, "content": "晴天"}
        ],
    )
    content = _content(card)
    assert "✅" in content
    assert "网络搜索" in content
    assert "天气" in content


def test_tool_trace_failure_shown_in_content() -> None:
    renderer = FeishuCardRenderer()
    card = renderer.render_final_card(
        user_text="设置提醒",
        assistant_text="创建提醒失败。",
        tool_calls=[],
        tool_results=[
            {
                "tool_name": "reminder_create",
                "input": {"message": "喝水"},
                "content": '{"error": "时间已过去"}',
            }
        ],
    )
    content = _content(card)
    assert "❌" in content
    assert "创建提醒" in content


# ------------------------------------------------------------------ #
# 追问确认卡                                                            #
# ------------------------------------------------------------------ #

def test_follow_up_card_detected_and_rendered() -> None:
    renderer = FeishuCardRenderer()
    card = renderer.render_final_card(
        user_text="记账",
        assistant_text="我需要确认一下：\n- 这笔消费是今天发生的吗？",
        tool_calls=[],
        tool_results=[],
    )
    _assert_v2(card)
    assert card["header"]["title"]["content"] == "需要你确认"
    content = _content(card)
    assert "今天发生的吗" in content
    assert "请直接回复下面这些点" in content
    assert "你回复后" in content


def test_routine_closing_question_not_treated_as_follow_up() -> None:
    renderer = FeishuCardRenderer()
    card = renderer.render_final_card(
        user_text="我是谁",
        assistant_text="你是腿哥，我的用户。对吧？",
        tool_calls=[],
        tool_results=[],
    )
    assert card["header"]["title"]["content"] == "Yi Min 回复"


# ------------------------------------------------------------------ #
# 记账草稿卡                                                            #
# ------------------------------------------------------------------ #

def test_ledger_draft_card_shows_merchants_and_amounts() -> None:
    renderer = FeishuCardRenderer()
    card = renderer.render_final_card(
        user_text="我中午吃了老乡鸡27块，早餐tims15元",
        assistant_text="两笔草稿准备好了。",
        tool_calls=[
            {
                "tool_name": "ledger_upsert_draft",
                "input": {
                    "thread_id": "t1",
                    "direction": "expense",
                    "amount_cent": 2700,
                    "currency": "CNY",
                    "category": "meal",
                    "occurred_at": "2026-04-24T12:30:00+08:00",
                    "merchant": "老乡鸡",
                    "note": "酸菜鱼",
                },
            },
            {
                "tool_name": "ledger_upsert_draft",
                "input": {
                    "thread_id": "t2",
                    "direction": "expense",
                    "amount_cent": 1500,
                    "currency": "CNY",
                    "category": "meal",
                    "occurred_at": "2026-04-24T08:30:00+08:00",
                    "merchant": "Tims",
                },
            },
        ],
        tool_results=[],
    )
    _assert_v2(card)
    assert card["header"]["title"]["content"] == "记账确认"
    content = _content(card)
    assert "老乡鸡" in content
    assert "¥27.00" in content
    assert "Tims" in content
    assert "¥15.00" in content
    assert "¥42.00" in content
    assert "提交吧" in content


def test_ledger_draft_card_string_amount_cent() -> None:
    renderer = FeishuCardRenderer()
    card = renderer.render_final_card(
        user_text="奶茶18块",
        assistant_text="草稿好了。",
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
    assert "¥18.00" in _content(card)


# ------------------------------------------------------------------ #
# 账本报告卡                                                            #
# ------------------------------------------------------------------ #

def test_ledger_report_card_shows_table_and_summary() -> None:
    renderer = FeishuCardRenderer()
    card = renderer.render_final_card(
        user_text="今天账本总结",
        assistant_text="你今天的账本如下。",
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
    _assert_v2(card)
    assert card["header"]["title"]["content"] == "2026年4月24日 账本总览"
    content = _content(card)

    # 汇总行
    assert "净支出" in content
    assert "¥85.00" in content

    # 明细表格（只展示前 5 条，京东不应出现）
    assert "海底捞" in content
    assert "老乡鸡" in content
    assert "公司报销" in content
    assert "Tims" in content
    assert "京东" not in content

    # markdown 表格语法
    assert "| 时间 |" in content
    assert "| 金额 |" in content


# ------------------------------------------------------------------ #
# 健身档案卡                                                            #
# ------------------------------------------------------------------ #

def test_fitness_profile_card_shows_all_fields() -> None:
    renderer = FeishuCardRenderer()
    card = renderer.render_final_card(
        user_text="查看我的健身档案",
        assistant_text="这是你的档案。",
        tool_calls=[],
        tool_results=[
            {
                "tool_name": "fitness_profile_get",
                "content": """{
  "training_profile": {"name": "腿哥", "goal": "力量提升", "level": "有基础", "plan_style": "PPL"},
  "coach_settings": {"primary_coach": "凯圣王×谭指导"}
}""",
            },
            {
                "tool_name": "fitness_settings_get",
                "content": '{"rpg": {"rpg_enabled": true, "story_density": "low"}, "world": {"world_name": "风痕原野"}}',
            },
        ],
    )
    _assert_v2(card)
    assert card["header"]["title"]["content"] == "健身档案"
    content = _content(card)
    assert "腿哥" in content
    assert "力量提升" in content
    assert "凯圣王×谭指导" in content
    assert "风痕原野" in content
    assert "RPG 开启" in content


# ------------------------------------------------------------------ #
# 最近训练卡                                                            #
# ------------------------------------------------------------------ #

def test_recent_workouts_card_shows_exercises() -> None:
    renderer = FeishuCardRenderer()
    card = renderer.render_final_card(
        user_text="看看最近训练",
        assistant_text="这是你最近的训练记录。",
        tool_calls=[],
        tool_results=[
            {
                "tool_name": "fitness_workout_recent",
                "content": (
                    "## [2026-05-06T19:30:00+08:00] 推类日\n"
                    "- exercises:\n"
                    "  - 卧推 60kg x 5 x 3\n"
                    "- duration_minutes: 55\n\n"
                    "## [2026-05-04T18:10:00+08:00] 腿日\n"
                    "- exercises:\n"
                    "  - 深蹲 80kg x 5 x 3\n"
                    "- duration_minutes: 60"
                ),
            }
        ],
    )
    _assert_v2(card)
    assert card["header"]["title"]["content"] == "最近训练"
    content = _content(card)
    assert "推类日" in content
    assert "卧推 60kg x 5 x 3" in content
    assert "腿日" in content


# ------------------------------------------------------------------ #
# 健身审计日志卡                                                         #
# ------------------------------------------------------------------ #

def test_fitness_audit_card_shows_change_lines() -> None:
    renderer = FeishuCardRenderer()
    card = renderer.render_final_card(
        user_text="看最近健身变更",
        assistant_text="这是最近的健身追溯日志。",
        tool_calls=[],
        tool_results=[
            {
                "tool_name": "fitness_audit_recent",
                "content": (
                    '[2026-05-06T19:40:00+08:00] workout_appended: {"title": "推类日"}\n'
                    '[2026-05-06T19:20:00+08:00] profile_updated: {"updated_fields": {"goal": "力量提升"}}\n'
                    '[2026-05-06T19:10:00+08:00] settings_updated: {"updated_fields": {"world_name": "风痕原野"}}'
                ),
            }
        ],
    )
    _assert_v2(card)
    assert card["header"]["title"]["content"] == "健身追溯日志"
    content = _content(card)
    assert "workout_appended" in content
    assert "profile_updated" in content
    assert "风痕原野" in content


# ------------------------------------------------------------------ #
# Placeholder / Error 卡                                               #
# ------------------------------------------------------------------ #

def test_placeholder_card_shows_status() -> None:
    renderer = FeishuCardRenderer(agent_name="Yi Min")
    card = renderer.render_placeholder_card(
        user_text="开始健身", status="👀 已收到，正在思考…"
    )
    _assert_v2(card)
    assert "正在处理" in card["header"]["title"]["content"]
    content = _content(card)
    assert "开始健身" in content
    assert "已收到" in content


def test_placeholder_card_with_partial_text() -> None:
    renderer = FeishuCardRenderer()
    card = renderer.render_placeholder_card(
        user_text="问题", assistant_text="正在输出中…", status="✍️ 正在输出…"
    )
    assert "正在输出" in card["header"]["title"]["content"]
    content = _content(card)
    assert "正在输出中" in content


def test_error_card_shows_error_text() -> None:
    renderer = FeishuCardRenderer()
    card = renderer.render_error_card(
        user_text="开始健身", error_text="抱歉，出错了：内部错误"
    )
    _assert_v2(card)
    assert card["header"]["title"]["content"] == "处理失败"
    assert card["header"]["template"] == "red"
    content = _content(card)
    assert "内部错误" in content
    assert "开始健身" in content
