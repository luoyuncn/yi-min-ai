---
name: bookkeeping
description: Proactively use ledger tools for bookkeeping, ask follow-up questions, and commit only after required fields are complete.
---
# Bookkeeping

- If the user expresses income, expense, reimbursement, transfer, budget, or asks for bookkeeping statistics, treat it as a bookkeeping workflow.
- Use `ledger_upsert_draft` to save any partially known ledger fields.
- Ask follow-up questions when direction, amount, or occurrence time is still unclear.
- Only call `ledger_commit_draft` after required fields are complete.
- Use `ledger_query_entries` and `ledger_summary` for reporting.
- Do not commit guessed values. Clarify ambiguity first.
- Prefer ledger tools over `memory_write` or arbitrary files for bookkeeping facts.
- Example triggers: `今天午饭 32`, `帮我记一笔报销 120`, `这个月餐饮花了多少`.
