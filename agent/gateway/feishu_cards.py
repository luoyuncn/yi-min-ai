"""Feishu 结构化卡片渲染。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import re

TOOL_NAME_ZH: dict[str, str] = {
    "file_read": "读取文件",
    "file_write": "写入文件",
    "ledger_upsert_draft": "记账草稿",
    "ledger_get_active_draft": "查看草稿",
    "ledger_commit_draft": "提交账目",
    "ledger_query_entries": "查询账目",
    "ledger_summary": "账目汇总",
    "profile_write": "更新档案",
    "memory_search": "搜索记忆",
    "memory_list_recent": "最近记忆",
    "memory_forget": "遗忘记忆",
    "note_add": "添加笔记",
    "note_search": "搜索笔记",
    "note_list_recent": "最近笔记",
    "note_update": "更新笔记",
    "search_sessions": "搜索历史会话",
    "read_skill": "读取技能",
    "cron_create_task": "创建定时任务",
    "cron_update_task": "更新定时任务",
    "cron_list_tasks": "查看定时任务",
    "cron_delete_task": "删除定时任务",
    "cron_run_now": "立即执行任务",
    "reminder_create": "创建提醒",
    "reminder_list": "查看提醒",
    "reminder_delete": "删除提醒",
    "shell_exec": "执行命令",
    "web_search": "网络搜索",
}


@dataclass(slots=True)
class ToolTrace:
    tool_name: str
    input: dict | None = None
    result: str | None = None


class FeishuCardRenderer:
    """把回复内容转换成更适合飞书的结构化卡片。"""

    def __init__(self, agent_name: str = "Yi Min") -> None:
        self.agent_name = agent_name

    def render_placeholder_card(
        self,
        *,
        user_text: str,
        assistant_text: str = "",
        status: str | None = None,
    ) -> dict:
        title = f"{self.agent_name} 正在输出" if assistant_text else f"{self.agent_name} 正在处理"
        elements: list[dict] = []
        self._append_quote_note(elements, user_text)
        elements.append(self._build_markdown_block(status or "处理中，请稍等…"))
        if assistant_text:
            elements.extend([{"tag": "hr"}, self._build_markdown_block(assistant_text)])
        return self._build_card(title=title, template="blue", elements=elements)

    def render_error_card(
        self,
        *,
        user_text: str,
        error_text: str,
    ) -> dict:
        elements: list[dict] = []
        self._append_quote_note(elements, user_text)
        elements.append(self._build_markdown_block(error_text))
        return self._build_card(
            title="处理失败",
            template="red",
            elements=elements,
        )

    def render_final_card(
        self,
        *,
        user_text: str,
        assistant_text: str,
        tool_calls: list[dict],
        tool_results: list[dict],
    ) -> dict:
        traces = self._normalize_tool_traces(tool_calls, tool_results)

        ledger_drafts = self._extract_ledger_drafts(traces)
        if ledger_drafts:
            return self._build_ledger_draft_card(
                user_text=user_text,
                assistant_text=assistant_text,
                drafts=ledger_drafts,
            )

        ledger_entries = self._extract_ledger_entries(traces)
        if ledger_entries:
            return self._build_ledger_report_card(
                user_text=user_text,
                assistant_text=assistant_text,
                entries=ledger_entries,
                summary=self._extract_ledger_summary(traces),
            )

        questions = self._extract_questions(assistant_text)
        if questions:
            return self._build_follow_up_card(
                user_text=user_text,
                assistant_text=assistant_text,
                questions=questions,
            )

        return self._build_generic_answer_card(
            user_text=user_text,
            assistant_text=assistant_text,
            traces=traces,
        )

    def tool_name_zh(self, tool_name: str) -> str:
        return TOOL_NAME_ZH.get(tool_name, tool_name)

    def _build_tool_trace_panel(self, traces: list[ToolTrace]) -> dict | None:
        called = [t for t in traces if t.tool_name]
        if not called:
            return None

        lines: list[str] = []
        for trace in called:
            name_zh = self.tool_name_zh(trace.tool_name)
            status = "⏳" if trace.result is None else ("❌" if self._is_tool_result_failed(trace.result) else "✅")
            lines.append(f"{status} **{name_zh}**")
            if trace.input:
                brief = self._format_tool_input_brief(trace.tool_name, trace.input)
                if brief:
                    lines.append(f"　└ {brief}")

        return {
            "tag": "collapsible_panel",
            "expanded": False,
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"🔧 调用了 {len(called)} 个工具",
                },
                "background_color": "grey",
                "vertical_align": "center",
            },
            "elements": [self._build_markdown_block("\n".join(lines))],
        }

    def _is_tool_result_failed(self, result: str) -> bool:
        if result.startswith("Tool execution failed:"):
            return True
        try:
            payload = json.loads(result)
        except json.JSONDecodeError:
            return False
        return isinstance(payload, dict) and bool(payload.get("error"))

    def _format_tool_input_brief(self, tool_name: str, input_dict: dict) -> str:
        if not input_dict:
            return ""
        if tool_name == "web_search":
            return input_dict.get("query", "")
        if tool_name in ("file_read", "file_write"):
            return input_dict.get("path", "") or input_dict.get("file_path", "")
        if tool_name in ("cron_create_task", "cron_update_task"):
            name = input_dict.get("name", "")
            schedule = input_dict.get("schedule", "")
            return f"{name} ({schedule})" if name else schedule
        if tool_name == "reminder_create":
            return input_dict.get("message", "") or input_dict.get("text", "")
        if tool_name in ("note_add", "note_update"):
            return self._truncate(input_dict.get("content", "") or input_dict.get("title", ""), 40)
        if tool_name in ("memory_search", "note_search", "search_sessions"):
            return input_dict.get("query", "")
        if tool_name == "shell_exec":
            return self._truncate(input_dict.get("command", ""), 50)
        first_val = next(iter(input_dict.values()), None)
        if isinstance(first_val, str):
            return self._truncate(first_val, 40)
        return ""

    def _build_generic_answer_card(
        self,
        *,
        user_text: str,
        assistant_text: str,
        traces: list[ToolTrace] | None = None,
    ) -> dict:
        elements: list[dict] = []
        self._append_quote_note(elements, user_text)
        elements.extend(self._build_body_sections(assistant_text))
        if traces:
            panel = self._build_tool_trace_panel(traces)
            if panel:
                elements.append({"tag": "hr"})
                elements.append(panel)
        elements.extend(
            [
                {"tag": "hr"},
                self._build_note_footer("直接回复我就行，我会继续接着处理。"),
            ]
        )
        return self._build_card(title=f"{self.agent_name} 回复", template="indigo", elements=elements)

    def _build_follow_up_card(self, *, user_text: str, assistant_text: str, questions: list[str]) -> dict:
        intro = self._strip_question_lines(assistant_text, questions).strip() or "还差一点信息，我确认完就能继续。"
        elements: list[dict] = []
        self._append_quote_note(elements, user_text)
        elements.extend([self._build_markdown_block(intro), {"tag": "hr"}])
        elements.append(self._build_markdown_block("**请直接回复下面这些点：**"))
        for question in questions:
            elements.append(self._build_markdown_block(f"- {question}"))
        elements.append(self._build_note_footer("你回复后，我会在原上下文里继续，不会重新来过。"))
        return self._build_card(title="需要你确认", template="orange", elements=elements)

    def _build_ledger_draft_card(self, *, user_text: str, assistant_text: str, drafts: list[dict]) -> dict:
        total_cent = sum(self._coerce_amount_cent(draft.get("amount_cent")) for draft in drafts)
        elements: list[dict] = []
        self._append_quote_note(elements, user_text)
        elements.extend(
            [
                self._build_markdown_block(assistant_text),
                {"tag": "hr"},
                self._build_fields_block(
                [
                    ("草稿笔数", str(len(drafts))),
                    ("合计金额", self._format_currency(total_cent)),
                ]
                ),
            ]
        )

        for draft in drafts:
            label = self._label_for_occurred_at(draft.get("occurred_at")) or "待确认"
            merchant = draft.get("merchant") or "未填写"
            amount = self._format_currency(self._coerce_amount_cent(draft.get("amount_cent")))
            category = draft.get("category") or "未分类"
            occurred_at = draft.get("occurred_at") or "时间待确认"
            note = draft.get("note") or "无备注"
            elements.append(
                self._build_fields_block(
                    [
                        (label, f"{merchant}\n{amount}"),
                        ("分类 / 时间", f"{category}\n{self._format_occurrence(occurred_at)}"),
                    ]
                )
            )
            elements.append(self._build_note_footer(note))

        elements.append(self._build_note_footer("如果没问题，直接回复“提交吧”即可。"))
        return self._build_card(title="记账确认", template="green", elements=elements)

    def _build_ledger_report_card(
        self,
        *,
        user_text: str,
        assistant_text: str,
        entries: list[dict],
        summary: dict | None,
    ) -> dict:
        display_entries = entries[:5]
        report_title = self._build_ledger_report_title(display_entries or entries)
        elements: list[dict] = []
        self._append_quote_note(elements, user_text)
        elements.extend([self._build_markdown_block(assistant_text), {"tag": "hr"}])
        if summary:
            elements.append(
                self._build_fields_block(
                    [
                        ("笔数", str(summary.get("entry_count", 0))),
                        ("支出", self._format_currency(summary.get("expense_cent", 0))),
                        ("收入", self._format_currency(summary.get("income_cent", 0))),
                        (self._net_label(summary.get("net_cent", 0)), self._format_currency(abs(summary.get("net_cent", 0)))),
                    ]
                )
            )
            elements.append(self._build_note_footer(self._build_ledger_summary_sentence(summary)))

        if display_entries:
            elements.extend(
                [
                    {"tag": "hr"},
                    self._build_markdown_block("**最近 5 条明细**"),
                    self._build_ledger_table_header_row(),
                ]
            )

        for entry in display_entries:
            elements.append(self._build_ledger_table_row(entry))

        insight = self._build_ledger_insight(display_entries, total_entries=len(entries))
        if insight:
            elements.append(self._build_note_footer(insight))

        if len(entries) > len(display_entries):
            elements.append(self._build_note_footer(f"还有 {len(entries) - len(display_entries)} 条记录未展开。"))

        return self._build_card(title=report_title, template="green", elements=elements)

    def _build_body_sections(self, assistant_text: str) -> list[dict]:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", assistant_text.strip()) if part.strip()]
        if not paragraphs:
            return [self._build_markdown_block(" ")]
        return [self._build_markdown_block(paragraph) for paragraph in paragraphs]

    def _build_card(self, *, title: str, template: str, elements: list[dict]) -> dict:
        return {
            "config": {
                "wide_screen_mode": True,
                "enable_forward": True,
                "update_multi": True,
            },
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": title,
                },
                "template": template,
            },
            "elements": elements,
        }

    def _build_quote_note(self, user_text: str) -> dict:
        text = self._truncate(user_text.strip() or " ", 120)
        return {
            "tag": "note",
            "elements": [
                {
                    "tag": "lark_md",
                    "content": f"你：{text}",
                }
            ],
        }

    def _append_quote_note(self, elements: list[dict], user_text: str) -> None:
        if user_text.strip():
            elements.append(self._build_quote_note(user_text))

    def _build_markdown_block(self, content: str) -> dict:
        return {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": content.strip() or " ",
            },
        }

    def _build_fields_block(self, fields: list[tuple[str, str]]) -> dict:
        return {
            "tag": "div",
            "fields": [
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": f"**{label}**\n{value}",
                    },
                }
                for label, value in fields
            ],
        }

    def _build_column_row(
        self,
        cells: list[str],
        *,
        header: bool = False,
    ) -> dict:
        weights = [2, 2, 3, 2]
        return {
            "tag": "column_set",
            "flex_mode": "none",
            "background_style": "grey" if header else "default",
            "columns": [
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": weights[index] if index < len(weights) else 2,
                    "vertical_align": "center",
                    "elements": [
                        self._build_markdown_block(
                            f"**{cell}**" if header else cell
                        )
                    ],
                }
                for index, cell in enumerate(cells)
            ],
        }

    def _build_ledger_table_header_row(self) -> dict:
        return self._build_column_row(["时间", "分类", "商家", "金额"], header=True)

    def _build_ledger_table_row(self, entry: dict) -> dict:
        direction = entry.get("direction") or "expense"
        amount_cent = int(entry.get("amount_cent") or 0)
        sign = "+" if direction == "income" else "-"
        amount_text = f"{sign}{self._format_currency(amount_cent)}"
        return self._build_column_row(
            [
                self._format_occurrence_short(entry.get("occurred_at") or ""),
                self._format_category_label(entry.get("category") or "-", direction),
                entry.get("merchant") or "-",
                amount_text,
            ]
        )

    def _build_note_footer(self, content: str) -> dict:
        return {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": self._truncate(content.strip() or " ", 160),
                }
            ],
        }

    def _normalize_tool_traces(self, tool_calls: list[dict], tool_results: list[dict]) -> list[ToolTrace]:
        # tool_results is already a superset of completed tool_calls (same dict, filtered)
        # Use tool_results as primary to avoid duplicating each call
        if tool_results:
            return [
                ToolTrace(
                    tool_name=r.get("tool_name", ""),
                    input=r.get("input"),
                    result=r.get("content"),
                )
                for r in tool_results
            ]
        # Fallback for in-progress tools (no results yet)
        return [
            ToolTrace(tool_name=c.get("tool_name", ""), input=c.get("input"), result=None)
            for c in tool_calls
        ]

    def _extract_ledger_drafts(self, traces: list[ToolTrace]) -> list[dict]:
        drafts: list[dict] = []
        for trace in traces:
            if trace.tool_name != "ledger_upsert_draft" or not isinstance(trace.input, dict):
                continue
            if trace.input.get("amount_cent") is None:
                continue
            drafts.append(trace.input)
        return drafts

    def _extract_ledger_entries(self, traces: list[ToolTrace]) -> list[dict]:
        entries: list[dict] = []
        for trace in traces:
            if trace.tool_name != "ledger_query_entries" or not trace.result:
                continue
            for raw_line in trace.result.splitlines():
                parsed = self._parse_ledger_query_line(raw_line)
                if parsed is not None:
                    entries.append(parsed)
        return entries

    def _extract_ledger_summary(self, traces: list[ToolTrace]) -> dict | None:
        for trace in traces:
            if trace.tool_name != "ledger_summary" or not trace.result:
                continue
            try:
                parsed = json.loads(trace.result)
            except json.JSONDecodeError:
                return None
            if isinstance(parsed, dict):
                return parsed
        return None

    def _parse_ledger_query_line(self, line: str) -> dict | None:
        match = re.match(
            r"^\[(?P<occurred_at>[^\]]+)\]\s+(?P<direction>\S+)\s+(?P<amount_cent>\d+)\s+(?P<currency>\S+)\s+(?P<category>\S+)\s+(?P<merchant>.+)$",
            line.strip(),
        )
        if not match:
            return None
        result = match.groupdict()
        result["amount_cent"] = int(result["amount_cent"])
        return result

    def _extract_questions(self, assistant_text: str) -> list[str]:
        questions: list[str] = []
        follow_up_context = self._looks_like_follow_up_prompt(assistant_text)
        for line in assistant_text.splitlines():
            stripped = self._normalize_question_line(line)
            if not stripped:
                continue
            if not self._is_question_line(stripped):
                continue
            if self._is_routine_closing_question(stripped):
                continue
            if follow_up_context or self._looks_like_question_item(line):
                questions.append(stripped)
        return questions

    def _strip_question_lines(self, assistant_text: str, questions: list[str]) -> str:
        filtered_lines: list[str] = []
        normalized_questions = {question.lstrip("-").strip() for question in questions}
        for line in assistant_text.splitlines():
            stripped = line.strip().lstrip("-").strip()
            if stripped in normalized_questions:
                continue
            filtered_lines.append(line)
        return "\n".join(filtered_lines).strip()

    def _label_for_occurred_at(self, occurred_at: str | None) -> str | None:
        if not occurred_at:
            return None
        try:
            dt = datetime.fromisoformat(occurred_at)
        except ValueError:
            return None
        hour = dt.hour
        if 5 <= hour < 10:
            return "早餐"
        if 10 <= hour < 15:
            return "午餐"
        if 15 <= hour < 18:
            return "下午"
        if 18 <= hour < 22:
            return "晚餐"
        return dt.strftime("%m-%d")

    def _format_occurrence(self, occurred_at: str) -> str:
        try:
            dt = datetime.fromisoformat(occurred_at)
        except ValueError:
            return occurred_at
        return dt.strftime("%m-%d %H:%M")

    def _format_occurrence_short(self, occurred_at: str) -> str:
        try:
            dt = datetime.fromisoformat(occurred_at)
        except ValueError:
            return occurred_at
        return dt.strftime("%H:%M")

    def _format_currency(self, amount_cent: int) -> str:
        amount_cent = self._coerce_amount_cent(amount_cent)
        sign = "-" if amount_cent < 0 else ""
        amount = abs(amount_cent) / 100
        return f"{sign}¥{amount:.2f}"

    def _coerce_amount_cent(self, value) -> int:
        if value is None or value == "":
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _truncate(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 1].rstrip() + "…"

    def _build_ledger_report_title(self, entries: list[dict]) -> str:
        if not entries:
            return "账本总览"
        occurred_at = entries[0].get("occurred_at")
        try:
            dt = datetime.fromisoformat(occurred_at or "")
        except ValueError:
            return "账本总览"
        return f"{dt.year}年{dt.month}月{dt.day}日 账本总览"

    def _net_label(self, net_cent: int) -> str:
        if net_cent < 0:
            return "净支出"
        if net_cent > 0:
            return "净收入"
        return "净额"

    def _build_ledger_summary_sentence(self, summary: dict) -> str:
        entry_count = int(summary.get("entry_count", 0) or 0)
        expense_cent = int(summary.get("expense_cent", 0) or 0)
        income_cent = int(summary.get("income_cent", 0) or 0)
        net_cent = int(summary.get("net_cent", 0) or 0)
        if income_cent <= 0:
            return f"今天共 {entry_count} 笔，暂无收入，净支出 {self._format_currency(abs(net_cent))}。"
        if expense_cent <= 0:
            return f"今天共 {entry_count} 笔，暂无支出，净收入 {self._format_currency(abs(net_cent))}。"
        return f"今天共 {entry_count} 笔，收支都发生了，当前净额 {self._format_currency(net_cent)}。"

    def _build_ledger_insight(self, entries: list[dict], *, total_entries: int) -> str:
        if not entries:
            return ""

        latest = entries[0]
        latest_text = (
            f"最新一笔是 {latest.get('merchant') or '-'}，"
            f"{self._format_occurrence(latest.get('occurred_at') or '')}，"
            f"{self._format_currency(int(latest.get('amount_cent') or 0))}。"
        )

        category_counts: dict[str, int] = {}
        for entry in entries:
            key = entry.get("category") or "-"
            category_counts[key] = category_counts.get(key, 0) + 1
        top_category, top_count = max(category_counts.items(), key=lambda item: item[1])
        category_text = f"最近 {len(entries)} 条里，`{top_category}` 类出现 {top_count} 次。"

        if total_entries > len(entries):
            return f"{latest_text} {category_text}"
        return f"{latest_text} {category_text}"

    def _format_category_label(self, category: str, direction: str) -> str:
        direction_label = "收入" if direction == "income" else "支出"
        return f"{category}\n{direction_label}"

    def _normalize_question_line(self, line: str) -> str:
        stripped = line.strip()
        stripped = re.sub(r"^(?:[-*•]\s*|\d+[.)]\s+)", "", stripped)
        return stripped.strip()

    def _is_question_line(self, text: str) -> bool:
        return "？" in text or text.endswith("?")

    def _looks_like_question_item(self, line: str) -> bool:
        stripped = line.lstrip()
        return bool(re.match(r"^(?:[-*•]\s+|\d+[.)]\s+)", stripped))

    def _looks_like_follow_up_prompt(self, assistant_text: str) -> bool:
        cues = (
            "确认一下",
            "需要确认",
            "请确认",
            "还差一点信息",
            "还差这些信息",
            "请直接回复",
            "请回复",
            "补充一下",
            "补充这些",
            "为了继续",
            "继续处理",
            "需要你回答",
            "需要你补充",
        )
        return any(cue in assistant_text for cue in cues)

    def _is_routine_closing_question(self, text: str) -> bool:
        normalized = text.strip()
        closing_patterns = (
            r"^对吧[？?]$",
            r"^还有别的事吗[？?]$",
            r"^还有其他事吗[？?]$",
            r"^还有什么需要我.*吗[？?]$",
            r"^还需要我.*吗[？?]$",
        )
        return any(re.match(pattern, normalized) for pattern in closing_patterns)
