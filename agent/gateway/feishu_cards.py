"""Feishu 结构化卡片渲染（JSON 2.0）。

设计原则：
- 使用 JSON 2.0 结构（schema: "2.0", body.elements）
- 每张卡片仅用一个 markdown 元素承载全部正文，消除元素数量限制与版本混用问题
- markdown 元素支持完整 CommonMark 语法（标题、列表、表格、加粗、引用块）
- 不再使用 1.0 专有的 div.fields、column_set 表格行、note（2.0 已废弃）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import re


TOOL_NAME_ZH: dict[str, str] = {
    "assistant_identity_update": "更新助手身份",
    "file_read": "读取文件",
    "file_write": "写入文件",
    "fitness_profile_get": "查看健身档案",
    "fitness_profile_update": "更新健身档案",
    "fitness_settings_get": "查看健身设定",
    "fitness_settings_update": "更新健身设定",
    "fitness_workout_append": "记录训练",
    "fitness_workout_recent": "最近训练",
    "fitness_audit_recent": "健身追溯日志",
    "ledger_upsert_draft": "记账草稿",
    "ledger_get_active_draft": "查看草稿",
    "ledger_commit_draft": "提交账目",
    "ledger_query_entries": "查询账目",
    "ledger_summary": "账目汇总",
    "profile_core_update": "更新核心资料",
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
    """把回复内容转换成飞书 JSON 2.0 卡片。"""

    def __init__(self, agent_name: str = "Yi Min") -> None:
        self.agent_name = agent_name

    # ------------------------------------------------------------------ #
    # 公开接口                                                              #
    # ------------------------------------------------------------------ #

    def render_placeholder_card(
        self,
        *,
        user_text: str,
        assistant_text: str = "",
        status: str | None = None,
    ) -> dict:
        title = f"{self.agent_name} 正在输出" if assistant_text else f"{self.agent_name} 正在处理"
        content = self._compose(
            self._quote(user_text),
            status or "处理中，请稍等…",
            assistant_text,
        )
        return self._build_card(title=title, template="blue", content=content)

    def render_error_card(
        self,
        *,
        user_text: str,
        error_text: str,
    ) -> dict:
        content = self._compose(self._quote(user_text), error_text)
        return self._build_card(title="处理失败", template="red", content=content)

    def render_final_card(
        self,
        *,
        user_text: str,
        assistant_text: str,
        tool_calls: list[dict],
        tool_results: list[dict],
    ) -> dict:
        traces = self._normalize_tool_traces(tool_calls, tool_results)

        fitness_card = self._build_fitness_card_if_applicable(user_text, assistant_text, traces)
        if fitness_card is not None:
            return fitness_card

        drafts = self._extract_ledger_drafts(traces)
        if drafts:
            return self._build_ledger_draft_card(user_text, assistant_text, drafts)

        entries = self._extract_ledger_entries(traces)
        if entries:
            return self._build_ledger_report_card(
                user_text, assistant_text, entries, self._extract_ledger_summary(traces)
            )

        questions = self._extract_questions(assistant_text)
        if questions:
            return self._build_follow_up_card(user_text, assistant_text, questions)

        return self._build_generic_answer_card(user_text, assistant_text, traces)

    def tool_name_zh(self, tool_name: str) -> str:
        return TOOL_NAME_ZH.get(tool_name, tool_name)

    # ------------------------------------------------------------------ #
    # 卡片类型构建器                                                         #
    # ------------------------------------------------------------------ #

    def _build_generic_answer_card(
        self,
        user_text: str,
        assistant_text: str,
        traces: list[ToolTrace],
    ) -> dict:
        content = self._compose(
            self._quote(user_text),
            assistant_text,
            self._tool_trace_line(traces),
            "*直接回复我就行，我会继续接着处理。*",
        )
        return self._build_card(title=f"{self.agent_name} 回复", template="indigo", content=content)

    def _build_follow_up_card(
        self,
        user_text: str,
        assistant_text: str,
        questions: list[str],
    ) -> dict:
        intro = self._strip_question_lines(assistant_text, questions).strip()
        if not intro:
            intro = "还差一点信息，我确认完就能继续。"
        q_block = "\n".join(f"- {q}" for q in questions)
        content = self._compose(
            self._quote(user_text),
            intro,
            "---\n**请直接回复下面这些点：**\n\n" + q_block,
            "*你回复后，我会在原上下文里继续，不会重新来过。*",
        )
        return self._build_card(title="需要你确认", template="orange", content=content)

    def _build_ledger_draft_card(
        self,
        user_text: str,
        assistant_text: str,
        drafts: list[dict],
    ) -> dict:
        total_cent = sum(self._coerce_amount_cent(d.get("amount_cent")) for d in drafts)
        summary_line = f"**{len(drafts)} 笔草稿**　合计 {self._format_currency(total_cent)}"

        rows = ["| 时段 | 商家 | 金额 | 分类 |", "|---|---|---|---|"]
        for d in drafts:
            label = self._label_for_occurred_at(d.get("occurred_at")) or "待确认"
            merchant = self._escape_table_cell(d.get("merchant") or "未填写")
            amount = self._format_currency(self._coerce_amount_cent(d.get("amount_cent")))
            cat = d.get("category") or "未分类"
            note = d.get("note") or ""
            suffix = f"（{note}）" if note else ""
            rows.append(f"| {label} | {merchant}{suffix} | {amount} | {cat} |")

        content = self._compose(
            self._quote(user_text),
            assistant_text,
            "---\n" + summary_line + "\n\n" + "\n".join(rows),
            "*如果没问题，直接回复「提交吧」即可。*",
        )
        return self._build_card(title="记账确认", template="green", content=content)

    def _build_ledger_report_card(
        self,
        user_text: str,
        assistant_text: str,
        entries: list[dict],
        summary: dict | None,
    ) -> dict:
        title = self._build_ledger_report_title(entries)
        parts: list[str] = [self._quote(user_text), assistant_text]

        if summary:
            ec = int(summary.get("entry_count", 0) or 0)
            exp = self._format_currency(self._coerce_amount_cent(summary.get("expense_cent")))
            inc = self._format_currency(self._coerce_amount_cent(summary.get("income_cent")))
            net = int(summary.get("net_cent", 0) or 0)
            net_label = self._net_label(net)
            net_str = self._format_currency(abs(net))
            parts.append(f"---\n**{ec} 笔**　支出 {exp}　收入 {inc}　{net_label} {net_str}")

        display = entries[:5]
        if display:
            rows = ["| 时间 | 分类 | 商家 | 金额 |", "|---|---|---|---|"]
            for e in display:
                t = self._format_occurrence_short(e.get("occurred_at") or "")
                direction = e.get("direction") or "expense"
                cat = e.get("category") or "-"
                merchant = self._escape_table_cell(e.get("merchant") or "-")
                sign = "+" if direction == "income" else "-"
                amount = f"{sign}{self._format_currency(self._coerce_amount_cent(e.get('amount_cent')))}"
                rows.append(f"| {t} | {cat} | {merchant} | {amount} |")
            parts.append("**最近 5 条明细**\n\n" + "\n".join(rows))

            insight = self._build_ledger_insight(display, total_entries=len(entries))
            if insight:
                parts.append(f"*{insight}*")
            if len(entries) > len(display):
                parts.append(f"*还有 {len(entries) - len(display)} 条记录未展开。*")

        return self._build_card(title=title, template="green", content=self._compose(*parts))

    def _build_fitness_card_if_applicable(
        self,
        user_text: str,
        assistant_text: str,
        traces: list[ToolTrace],
    ) -> dict | None:
        profile = self._extract_json_payload(traces, "fitness_profile_get")
        settings = self._extract_json_payload(traces, "fitness_settings_get")
        if profile is not None or settings is not None:
            return self._build_fitness_profile_card(
                user_text, assistant_text, profile or {}, settings or {}, traces
            )

        workouts = self._extract_fitness_workouts(traces)
        if workouts:
            return self._build_recent_fitness_workouts_card(user_text, assistant_text, workouts, traces)

        audit_lines = self._extract_fitness_audit_lines(traces)
        if audit_lines:
            return self._build_fitness_audit_card(user_text, assistant_text, audit_lines, traces)

        return None

    def _build_fitness_profile_card(
        self,
        user_text: str,
        assistant_text: str,
        profile: dict,
        settings: dict,
        traces: list[ToolTrace],
    ) -> dict:
        training = profile.get("training_profile", {}) if isinstance(profile, dict) else {}
        coach = profile.get("coach_settings", {}) if isinstance(profile, dict) else {}
        rpg = settings.get("rpg", {}) if isinstance(settings, dict) else {}
        world = settings.get("world", {}) if isinstance(settings, dict) else {}

        info_lines = [
            f"**称呼** {training.get('name') or '-'}　　**目标** {training.get('goal') or '-'}",
            f"**水平** {training.get('level') or '-'}　　**计划** {training.get('plan_style') or '-'}",
            f"**主教练** {coach.get('primary_coach') or '-'}",
            f"**世界** {world.get('world_name') or '-'}　　**剧情** {self._fitness_story_label(rpg)}",
        ]
        content = self._compose(
            self._quote(user_text),
            assistant_text,
            "---\n" + "\n".join(info_lines),
            self._tool_trace_line(traces),
            "*如需修改目标、教练风格或 RPG 设定，直接告诉我即可。*",
        )
        return self._build_card(title="健身档案", template="green", content=content)

    def _build_recent_fitness_workouts_card(
        self,
        user_text: str,
        assistant_text: str,
        workouts: list[dict],
        traces: list[ToolTrace],
    ) -> dict:
        items: list[str] = []
        for w in workouts[:5]:
            w_title = w.get("title") or "-"
            occurred = self._format_occurrence(w.get("occurred_at") or "")
            exercises = w.get("exercises", [])
            duration = w.get("duration_minutes")
            ex_line = "、".join(exercises[:3]) if exercises else "-"
            dur_line = f"{duration} 分钟" if duration else "-"
            items.append(f"**{w_title}** · {occurred} · {dur_line}\n{ex_line}")

        content = self._compose(
            self._quote(user_text),
            assistant_text,
            "---\n**最近训练记录**\n\n" + "\n\n".join(items),
            self._tool_trace_line(traces),
            "*如果你要，我也可以基于这些记录直接给出下一次训练建议。*",
        )
        return self._build_card(title="最近训练", template="green", content=content)

    def _build_fitness_audit_card(
        self,
        user_text: str,
        assistant_text: str,
        audit_lines: list[str],
        traces: list[ToolTrace],
    ) -> dict:
        items = "\n".join(f"- {line}" for line in audit_lines[:8])
        content = self._compose(
            self._quote(user_text),
            assistant_text,
            "---\n**最近变更**\n\n" + items,
            self._tool_trace_line(traces),
            "*这些记录来自 fitness 审计日志，方便你追溯是谁在什么时候改了什么。*",
        )
        return self._build_card(title="健身追溯日志", template="green", content=content)

    # ------------------------------------------------------------------ #
    # 核心 JSON 2.0 卡片构建                                                #
    # ------------------------------------------------------------------ #

    def _build_card(self, *, title: str, template: str, content: str) -> dict:
        return {
            "schema": "2.0",
            "config": {
                "enable_forward": True,
                "update_multi": True,
            },
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": template,
            },
            "body": {
                "elements": [
                    {"tag": "markdown", "content": content or " "},
                ]
            },
        }

    # ------------------------------------------------------------------ #
    # Markdown 组合辅助                                                     #
    # ------------------------------------------------------------------ #

    def _compose(self, *parts: str) -> str:
        """用双换行拼接非空段落。"""
        return "\n\n".join(p for p in parts if p and p.strip())

    def _quote(self, user_text: str) -> str:
        text = self._truncate((user_text or "").strip(), 120)
        return f"> 你：{text}" if text else ""

    def _tool_trace_line(self, traces: list[ToolTrace]) -> str:
        called = [t for t in traces if t.tool_name]
        if not called:
            return ""
        parts: list[str] = []
        for t in called:
            name_zh = self.tool_name_zh(t.tool_name)
            status = "⏳" if t.result is None else ("❌" if self._is_tool_result_failed(t.result) else "✅")
            brief = self._format_tool_input_brief(t.tool_name, t.input) if t.input else ""
            parts.append(f"{status} {name_zh}：{brief}" if brief else f"{status} {name_zh}")
        return f"*🔧 调用了 {len(called)} 个工具：{'  |  '.join(parts)}*"

    # ------------------------------------------------------------------ #
    # 工具调用数据提取                                                        #
    # ------------------------------------------------------------------ #

    def _normalize_tool_traces(
        self, tool_calls: list[dict], tool_results: list[dict]
    ) -> list[ToolTrace]:
        if tool_results:
            return [
                ToolTrace(
                    tool_name=r.get("tool_name", ""),
                    input=r.get("input"),
                    result=r.get("content"),
                )
                for r in tool_results
            ]
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

    def _extract_json_payload(self, traces: list[ToolTrace], tool_name: str) -> dict | None:
        for trace in traces:
            if trace.tool_name != tool_name or not trace.result:
                continue
            try:
                payload = json.loads(trace.result)
            except json.JSONDecodeError:
                return None
            if isinstance(payload, dict):
                return payload
        return None

    def _extract_fitness_workouts(self, traces: list[ToolTrace]) -> list[dict]:
        workouts: list[dict] = []
        pattern = re.compile(r"^## \[(?P<occurred_at>[^\]]+)\] (?P<title>.+)$")
        for trace in traces:
            if trace.tool_name != "fitness_workout_recent" or not trace.result:
                continue
            current: dict | None = None
            for raw_line in trace.result.splitlines():
                line = raw_line.rstrip()
                match = pattern.match(line)
                if match:
                    if current:
                        workouts.append(current)
                    current = {
                        "occurred_at": match.group("occurred_at"),
                        "title": match.group("title"),
                        "exercises": [],
                        "duration_minutes": None,
                    }
                    continue
                if current is None:
                    continue
                stripped = line.strip()
                if stripped.startswith("- duration_minutes:"):
                    current["duration_minutes"] = stripped.split(":", 1)[1].strip()
                elif stripped.startswith("-") and "exercises" in stripped:
                    continue
                elif raw_line.startswith("  -"):
                    current["exercises"].append(raw_line.strip()[2:].strip())
                elif stripped.startswith("- "):
                    current.setdefault("notes", []).append(stripped[2:].strip())
            if current:
                workouts.append(current)
        return workouts

    def _extract_fitness_audit_lines(self, traces: list[ToolTrace]) -> list[str]:
        for trace in traces:
            if trace.tool_name != "fitness_audit_recent" or not trace.result:
                continue
            return [line.strip() for line in trace.result.splitlines() if line.strip()]
        return []

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

    # ------------------------------------------------------------------ #
    # 追问问题提取                                                            #
    # ------------------------------------------------------------------ #

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
        normalized_questions = {q.lstrip("-").strip() for q in questions}
        filtered: list[str] = []
        for line in assistant_text.splitlines():
            stripped = line.strip().lstrip("-").strip()
            if stripped in normalized_questions:
                continue
            filtered.append(line)
        return "\n".join(filtered).strip()

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
            "确认一下", "需要确认", "请确认",
            "还差一点信息", "还差这些信息",
            "请直接回复", "请回复",
            "补充一下", "补充这些",
            "为了继续", "继续处理",
            "需要你回答", "需要你补充",
        )
        return any(cue in assistant_text for cue in cues)

    def _is_routine_closing_question(self, text: str) -> bool:
        closing_patterns = (
            r"^对吧[？?]$",
            r"^还有别的事吗[？?]$",
            r"^还有其他事吗[？?]$",
            r"^还有什么需要我.*吗[？?]$",
            r"^还需要我.*吗[？?]$",
        )
        return any(re.match(p, text.strip()) for p in closing_patterns)

    # ------------------------------------------------------------------ #
    # 工具输入摘要                                                            #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    # 格式化工具                                                             #
    # ------------------------------------------------------------------ #

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

    def _escape_table_cell(self, text: str) -> str:
        """转义 markdown 表格单元格中的竖线。"""
        return (text or "").replace("|", "\\|")

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
            f"{self._format_currency(self._coerce_amount_cent(latest.get('amount_cent')))}。"
        )
        category_counts: dict[str, int] = {}
        for entry in entries:
            key = entry.get("category") or "-"
            category_counts[key] = category_counts.get(key, 0) + 1
        top_category, top_count = max(category_counts.items(), key=lambda item: item[1])
        category_text = f"最近 {len(entries)} 条里，`{top_category}` 类出现 {top_count} 次。"
        return f"{latest_text} {category_text}"

    def _fitness_story_label(self, rpg: dict) -> str:
        enabled = rpg.get("rpg_enabled")
        density = rpg.get("story_density") or "-"
        if enabled:
            return f"RPG 开启 / {density}"
        return "RPG 关闭"
