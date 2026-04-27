"""结构化记账工具。"""

import json


def ledger_upsert_draft(
    ledger_store,
    *,
    thread_id: str,
    source_message_id: str | None = None,
    direction: str | None = None,
    amount_cent: int | None = None,
    currency: str | None = "CNY",
    category: str | None = None,
    occurred_at: str | None = None,
    merchant: str | None = None,
    note: str | None = None,
    missing_fields: list[str] | None = None,
) -> str:
    _require_dependency(ledger_store, "LedgerStore")
    ledger_store.upsert_draft(
        thread_id=thread_id,
        source_message_id=source_message_id,
        direction=direction,
        amount_cent=amount_cent,
        currency=currency,
        category=category,
        occurred_at=occurred_at,
        merchant=merchant,
        note=note,
        missing_fields=missing_fields or [],
    )
    return "ok"


def ledger_get_active_draft(ledger_store, *, thread_id: str) -> str:
    _require_dependency(ledger_store, "LedgerStore")
    draft = ledger_store.get_active_draft(thread_id)
    if draft is None:
        return "No active ledger draft."
    return json.dumps(draft, ensure_ascii=False)


def ledger_commit_draft(ledger_store, *, thread_id: str) -> str:
    _require_dependency(ledger_store, "LedgerStore")
    entry_id = ledger_store.commit_draft(thread_id)
    return f"Committed ledger entry: {entry_id}"


def ledger_query_entries(
    ledger_store,
    *,
    direction: str | None = None,
    category: str | None = None,
    source_thread_id: str | None = None,
    occurred_from: str | None = None,
    occurred_to: str | None = None,
    limit: int = 10,
) -> str:
    _require_dependency(ledger_store, "LedgerStore")
    rows = ledger_store.query_entries(
        direction=direction,
        category=category,
        source_thread_id=source_thread_id,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        limit=limit,
    )
    if not rows:
        return "No ledger entries found."
    return "\n".join(
        f"[{row['occurred_at']}] {row['direction']} {row['amount_cent']} {row['currency']} "
        f"{row['category'] or '-'} {row['merchant'] or '-'}"
        for row in rows
    )


def ledger_summary(
    ledger_store,
    *,
    category: str | None = None,
    source_thread_id: str | None = None,
    occurred_from: str | None = None,
    occurred_to: str | None = None,
) -> str:
    _require_dependency(ledger_store, "LedgerStore")
    summary = ledger_store.summary(
        category=category,
        source_thread_id=source_thread_id,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
    )
    return json.dumps(summary, ensure_ascii=False)


def _require_dependency(dependency, name: str) -> None:
    if dependency is None:
        raise RuntimeError(f"{name} dependency is not configured")
