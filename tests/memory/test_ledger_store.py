"""LedgerStore 测试。"""

from datetime import UTC, datetime

from agent.memory.ledger_store import LedgerStore


def test_ledger_store_can_upsert_and_read_active_draft(tmp_path) -> None:
    """同一线程应能保存并取回当前活跃记账草稿。"""

    store = LedgerStore(tmp_path / "agent.db")
    occurred_at = datetime(2026, 4, 24, 12, 30, tzinfo=UTC)

    store.upsert_draft(
        thread_id="web:default:thread-1",
        source_message_id="msg-1",
        direction="expense",
        amount_cent=3200,
        currency="CNY",
        category=None,
        occurred_at=occurred_at.isoformat(),
        merchant="午饭",
        note="工作日午饭",
        missing_fields=["category"],
    )

    draft = store.get_active_draft("web:default:thread-1")

    assert draft is not None
    assert draft["direction"] == "expense"
    assert draft["amount_cent"] == 3200
    assert draft["merchant"] == "午饭"
    assert draft["missing_fields"] == ["category"]


def test_ledger_store_commit_moves_draft_into_entries(tmp_path) -> None:
    """提交草稿后，应写入正式账目并清理活跃草稿。"""

    store = LedgerStore(tmp_path / "agent.db")
    store.upsert_draft(
        thread_id="web:default:thread-1",
        source_message_id="msg-1",
        direction="expense",
        amount_cent=3200,
        currency="CNY",
        category="meal",
        occurred_at="2026-04-24T12:30:00+00:00",
        merchant="午饭",
        note="工作日午饭",
        missing_fields=[],
    )

    entry_id = store.commit_draft("web:default:thread-1")
    draft = store.get_active_draft("web:default:thread-1")
    rows = store.query_entries(limit=10)

    assert entry_id
    assert draft is None
    assert len(rows) == 1
    assert rows[0]["id"] == entry_id
    assert rows[0]["category"] == "meal"


def test_ledger_store_can_summarize_entries_by_direction_and_total(tmp_path) -> None:
    """账本统计应返回总条数、收入、支出与净额。"""

    store = LedgerStore(tmp_path / "agent.db")
    store.add_entry(
        direction="expense",
        amount_cent=3200,
        currency="CNY",
        category="meal",
        occurred_at="2026-04-24T12:30:00+00:00",
        merchant="午饭",
        note="工作日午饭",
        source_message_id="msg-1",
        source_thread_id="web:default:thread-1",
    )
    store.add_entry(
        direction="income",
        amount_cent=500000,
        currency="CNY",
        category="salary",
        occurred_at="2026-04-25T01:00:00+00:00",
        merchant="公司",
        note="工资",
        source_message_id="msg-2",
        source_thread_id="web:default:thread-1",
    )

    summary = store.summary()

    assert summary["entry_count"] == 2
    assert summary["expense_cent"] == 3200
    assert summary["income_cent"] == 500000
    assert summary["net_cent"] == 496800
