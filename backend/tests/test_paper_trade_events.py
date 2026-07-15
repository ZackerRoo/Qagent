from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from qagent.storage.paper import PaperTradeEventMetadata, PaperTradingRepository
from qagent.storage.tables import PaperTradeEventRow

from test_state_repository import make_repo


def _create_trade(paper_repo: PaperTradingRepository, source_snapshot_id: str = "event-source"):
    return paper_repo.create_trade(
        source_snapshot_id=source_snapshot_id,
        provider="fixture",
        instrument_id="US:TEST",
        strategy_id="event-test",
        signal_date=date(2026, 7, 10),
        trigger_price=Decimal("10.00"),
        initial_stop=Decimal("9.00"),
        target_1=Decimal("12.00"),
        rank_score=Decimal("0.80"),
        notes="created for event test",
    )


def test_create_trade_appends_created_event(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)

    trade = _create_trade(paper_repo)
    events = paper_repo.list_trade_events(trade.trade_id)

    assert len(events) == 1
    event = events[0]
    assert event.sequence == 1
    assert event.event_type == "created"
    assert event.from_status is None
    assert event.to_status == "pending"
    assert event.trade_date == date(2026, 7, 10)
    assert event.price == Decimal("10.0000")
    assert event.note == "created for event test"
    assert event.source == "paper_repository"
    assert event.occurred_at.tzinfo == timezone.utc
    assert event.created_at.tzinfo == timezone.utc


def test_legal_update_appends_event_with_explicit_metadata(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    trade = _create_trade(paper_repo)
    occurred_at = datetime(2026, 7, 11, 2, 30, tzinfo=timezone.utc)

    updated = paper_repo.update_trade(
        trade.trade_id,
        status="open",
        entry_date=date(2026, 7, 11),
        entry_price=Decimal("10.10"),
        latest_date=date(2026, 7, 11),
        latest_price=Decimal("10.20"),
        event_metadata=PaperTradeEventMetadata(
            idempotency_key="fixture-open-event",
            occurred_at=occurred_at,
            reason_code="trigger_filled",
            note="fixture fill",
            source="test_engine",
        ),
    )
    events = paper_repo.list_trade_events(trade.trade_id)

    assert updated is not None
    assert updated.status == "open"
    assert [event.sequence for event in events] == [1, 2]
    event = events[-1]
    assert event.event_type == "status_changed"
    assert event.from_status == "pending"
    assert event.to_status == "open"
    assert event.trade_date == date(2026, 7, 11)
    assert event.price == Decimal("10.1000")
    assert event.idempotency_key == "fixture-open-event"
    assert event.occurred_at == occurred_at
    assert event.reason_code == "trigger_filled"
    assert event.note == "fixture fill"
    assert event.source == "test_engine"


def test_repeated_same_value_update_does_not_append_event(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    trade = _create_trade(paper_repo)
    changes = {
        "status": "open",
        "entry_date": date(2026, 7, 11),
        "entry_price": Decimal("10.10"),
        "latest_date": date(2026, 7, 11),
        "latest_price": Decimal("10.20"),
    }

    paper_repo.update_trade(trade.trade_id, **changes)
    paper_repo.update_trade(trade.trade_id, **changes)

    events = paper_repo.list_trade_events(trade.trade_id)
    assert [event.sequence for event in events] == [1, 2]


def test_terminal_status_cannot_return_to_active_and_transaction_is_unchanged(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    trade = _create_trade(paper_repo)
    paper_repo.update_trade(
        trade.trade_id,
        status="stopped",
        entry_date=date(2026, 7, 11),
        entry_price=Decimal("10.00"),
        exit_date=date(2026, 7, 12),
        exit_price=Decimal("9.00"),
        realized_return_pct=Decimal("-10.00"),
    )

    with pytest.raises(ValueError, match="stopped -> open"):
        paper_repo.update_trade(
            trade.trade_id,
            status="open",
            exit_date=None,
            exit_price=None,
            latest_price=Decimal("10.50"),
        )

    stored = paper_repo.list_trades()[0]
    events = paper_repo.list_trade_events(trade.trade_id)
    assert stored.status == "stopped"
    assert stored.exit_date == date(2026, 7, 12)
    assert stored.exit_price == Decimal("9.0000")
    assert stored.latest_price is None
    assert [event.sequence for event in events] == [1, 2]


def test_event_constraint_failure_rolls_back_trade_snapshot(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    trade = _create_trade(paper_repo)
    created_event = paper_repo.list_trade_events(trade.trade_id)[0]

    with pytest.raises(IntegrityError):
        paper_repo.update_trade(
            trade.trade_id,
            status="open",
            entry_date=date(2026, 7, 11),
            entry_price=Decimal("10.10"),
            event_metadata=PaperTradeEventMetadata(
                idempotency_key=created_event.idempotency_key,
            ),
        )

    stored = paper_repo.list_trades()[0]
    events = paper_repo.list_trade_events(trade.trade_id)
    assert stored.status == "pending"
    assert stored.entry_date is None
    assert stored.entry_price is None
    assert [event.sequence for event in events] == [1]


def test_events_are_ordered_and_identifiers_are_unique(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    trade = _create_trade(paper_repo)
    paper_repo.update_trade(
        trade.trade_id,
        status="open",
        entry_date=date(2026, 7, 11),
        entry_price=Decimal("10.10"),
    )
    paper_repo.update_trade(
        trade.trade_id,
        latest_date=date(2026, 7, 12),
        latest_price=Decimal("10.40"),
        unrealized_return_pct=Decimal("2.9703"),
        holding_days=1,
    )

    events = paper_repo.list_trade_events(trade.trade_id)

    assert [event.sequence for event in events] == [1, 2, 3]
    assert [event.event_type for event in events] == [
        "created",
        "status_changed",
        "execution_updated",
    ]
    assert len({event.event_id for event in events}) == 3
    assert len({event.idempotency_key for event in events}) == 3

    with repo.session_factory() as session:
        session.add(
            PaperTradeEventRow(
                event_id="duplicate-trade-sequence",
                trade_id=trade.trade_id,
                instrument_id=trade.instrument_id,
                sequence=3,
                idempotency_key="unique-idempotency-key",
                event_type="execution_updated",
                from_status="open",
                to_status="open",
                occurred_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
                note="duplicate sequence must fail",
                source="constraint_test",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    assert [event.sequence for event in paper_repo.list_trade_events(trade.trade_id)] == [1, 2, 3]


def test_delete_and_reset_append_tombstones_without_erasing_events(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    first = _create_trade(paper_repo, "event-delete")
    second = _create_trade(paper_repo, "event-reset")

    assert paper_repo.delete_trade(first.trade_id) is True
    first_events = paper_repo.list_trade_events(first.trade_id)
    assert [event.event_type for event in first_events] == ["created", "deleted"]
    assert first_events[-1].to_status == "deleted"

    assert paper_repo.clear_trades() == 1
    second_events = paper_repo.list_trade_events(second.trade_id)
    assert [event.event_type for event in second_events] == ["created", "session_reset"]
    assert second_events[-1].to_status == "deleted"


def test_existing_trade_without_events_gets_one_legacy_snapshot(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    trade = _create_trade(paper_repo, "legacy-event-source")
    with repo.session_factory() as session:
        session.query(PaperTradeEventRow).filter(
            PaperTradeEventRow.trade_id == trade.trade_id
        ).delete()
        session.commit()

    first = paper_repo.list_trade_events(trade.trade_id)
    second = paper_repo.list_trade_events(trade.trade_id)

    assert len(first) == len(second) == 1
    assert first[0].event_type == "legacy_snapshot"
    assert first[0].to_status == "pending"
    assert first[0].source == "schema_backfill"
