from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from qagent.execution import (
    EXECUTION_EVENT_ADAPTER,
    AShareExecutionRules,
    Account,
    ExecutionState,
    FillRecordedEvent,
    ExecutionInvariantError,
    IdempotencyConflict,
    MarketEvent,
    OrderActivatedEvent,
    OrderCancelledEvent,
    OrderCreatedEvent,
    OrderExpiredEvent,
    OrderIntent,
    OrderRejectedEvent,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
    apply_market_event,
    apply_order_intent,
    cancel_order,
    process_market_event,
    reduce_event,
    reduce_events,
    replay_events,
    run_market_feed,
    submit_order,
)


DAY_1 = date(2025, 1, 2)
DAY_2 = date(2025, 1, 3)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)


def _state(
    cash: str = "100000",
    *,
    positions: dict[str, Position] | None = None,
    session_date: date | None = None,
) -> ExecutionState:
    return ExecutionState(
        account=Account(
            account_id="paper",
            cash=Decimal(cash),
            positions=positions or {},
        ),
        session_date=session_date,
    )


def _intent(
    intent_id: str,
    *,
    side: OrderSide = OrderSide.BUY,
    quantity: int = 100,
    submitted_at: datetime | None = None,
    order_type: OrderType = OrderType.LIMIT,
    limit_price: str | None = "10.00",
    stop_price: str | None = None,
    estimated_price: str | None = None,
    time_in_force: TimeInForce = TimeInForce.GTC,
    expires_at: datetime | None = None,
    instrument_id: str = "CN:000001",
) -> OrderIntent:
    return OrderIntent(
        intent_id=intent_id,
        account_id="paper",
        instrument_id=instrument_id,
        side=side,
        quantity=quantity,
        submitted_at=submitted_at or _at(DAY_1, 9, 30),
        order_type=order_type,
        limit_price=Decimal(limit_price) if limit_price is not None else None,
        stop_price=Decimal(stop_price) if stop_price is not None else None,
        estimated_price=Decimal(estimated_price) if estimated_price is not None else None,
        time_in_force=time_in_force,
        expires_at=expires_at,
    )


def _bar(
    event_id: str,
    *,
    day: date = DAY_1,
    hour: int = 15,
    minute: int = 0,
    open_price: str = "10.00",
    high: str = "10.00",
    low: str = "10.00",
    close: str = "10.00",
    volume: int = 10_000,
    previous_close: str | None = "10.00",
    suspended: bool = False,
    instrument_id: str = "CN:000001",
) -> MarketEvent:
    return MarketEvent(
        event_id=event_id,
        instrument_id=instrument_id,
        occurred_at=_at(day, hour, minute),
        trading_date=day,
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
        previous_close=(
            Decimal(previous_close) if previous_close is not None else None
        ),
        suspended=suspended,
    )


def _order_by_intent(state: ExecutionState, intent_id: str):
    return next(order for order in state.orders.values() if order.intent_id == intent_id)


def test_immutable_contract_and_explicit_order_lifecycle():
    initial = _state()
    intent = _intent("lifecycle")
    rules = AShareExecutionRules(slippage_bps=Decimal("0"))

    events = submit_order(initial, intent, rules)

    assert len(events) == 2
    assert isinstance(events[0], OrderCreatedEvent)
    assert isinstance(events[1], OrderActivatedEvent)
    pending = reduce_event(initial, events[0])
    order_id = events[0].order.order_id
    assert pending.orders[order_id].status == OrderStatus.PENDING_NEW
    active = reduce_event(pending, events[1])
    assert active.orders[order_id].status == OrderStatus.ACTIVE
    assert active.account.frozen_cash == Decimal("1005.01")
    assert initial.orders == {}

    with pytest.raises(ValidationError):
        intent.quantity = 200
    with pytest.raises(ValidationError):
        events[0].event_id = "changed"
    with pytest.raises(TypeError):
        active.orders["other"] = active.orders[order_id]

    restored = EXECUTION_EVENT_ADAPTER.validate_python(events[0].model_dump())
    assert restored == events[0]

    cancelled_events = cancel_order(active, order_id, _at(DAY_1, 10))
    assert isinstance(cancelled_events[0], OrderCancelledEvent)
    cancelled = reduce_events(active, cancelled_events)
    assert cancelled.orders[order_id].status == OrderStatus.CANCELLED
    assert cancelled.account.frozen_cash == 0
    assert cancel_order(cancelled, order_id, _at(DAY_1, 10)) == ()


@pytest.mark.parametrize(
    ("intent", "cash", "reason"),
    [
        (_intent("bad-lot", quantity=150), "100000", "quantity_must_be_round_lot"),
        (
            _intent("bad-tick", limit_price="10.005"),
            "100000",
            "limit_price_not_on_tick",
        ),
        (
            _intent("missing-limit", limit_price=None),
            "100000",
            "limit_price_required",
        ),
        (
            _intent(
                "missing-stop",
                order_type=OrderType.STOP,
                limit_price=None,
                stop_price=None,
            ),
            "100000",
            "stop_price_required",
        ),
        (
            _intent(
                "invalid-stop-limit",
                order_type=OrderType.STOP_LIMIT,
                limit_price="9.90",
                stop_price="10.00",
            ),
            "100000",
            "invalid_stop_limit_prices",
        ),
        (_intent("no-money"), "1000", "insufficient_cash"),
        (
            _intent("no-position", side=OrderSide.SELL),
            "100000",
            "insufficient_sellable_quantity",
        ),
    ],
)
def test_a_share_order_validation_rejects_invalid_orders(intent, cash, reason):
    result = apply_order_intent(_state(cash), intent, AShareExecutionRules())
    order = _order_by_intent(result.state, intent.intent_id)

    assert isinstance(result.events[-1], OrderRejectedEvent)
    assert order.status == OrderStatus.REJECTED
    assert order.status_reason == reason
    assert result.state.account.frozen_cash == 0


def test_explicit_expiry_reaches_expired_terminal_state():
    intent = _intent("expiring", expires_at=_at(DAY_1, 10))
    active = apply_order_intent(_state(), intent, AShareExecutionRules()).state

    result = apply_market_event(active, _bar("after-expiry", hour=11))
    order = _order_by_intent(result.state, "expiring")

    assert any(isinstance(event, OrderExpiredEvent) for event in result.events)
    assert order.status == OrderStatus.EXPIRED
    assert order.filled_quantity == 0
    assert result.state.account.frozen_cash == 0


def test_gap_prices_volume_participation_partial_fills_and_cumulative_minimum_fee():
    rules = AShareExecutionRules(
        volume_participation_rate=Decimal("0.10"),
        slippage_bps=Decimal("0"),
    )
    active = apply_order_intent(
        _state(),
        _intent("partial", quantity=1000),
        rules,
    ).state

    first = apply_market_event(
        active,
        _bar(
            "gap-below-limit",
            open_price="9.50",
            high="10.00",
            low="9.40",
            close="9.80",
            volume=5_000,
        ),
    )
    first_order = _order_by_intent(first.state, "partial")
    first_fill = first.state.fills[0]
    assert first_order.status == OrderStatus.PARTIALLY_FILLED
    assert first_order.filled_quantity == 500
    assert first_fill.base_price == Decimal("9.50")
    assert first_fill.price == Decimal("9.50")
    assert first_fill.commission == Decimal("5.00")
    assert first_fill.transfer_fee == Decimal("0.05")

    second = apply_market_event(
        first.state,
        _bar(
            "intraday-limit-touch",
            minute=1,
            open_price="10.20",
            high="10.30",
            low="9.90",
            close="10.00",
            volume=5_000,
            previous_close="9.80",
        ),
    )
    order = _order_by_intent(second.state, "partial")
    second_fill = second.state.fills[1]
    assert order.status == OrderStatus.FILLED
    assert order.filled_quantity == 1000
    assert order.average_fill_price == Decimal("9.750000")
    assert second_fill.base_price == Decimal("10.00")
    assert second_fill.commission == 0
    assert second_fill.transfer_fee == Decimal("0.05")
    assert second.state.account.fees_paid == Decimal("5.10")
    assert second.state.account.cash == Decimal("90244.90")
    assert second.state.account.positions["CN:000001"].sellable_quantity == 0


def test_stop_gap_uses_open_then_applies_frozen_adverse_slippage():
    rules = AShareExecutionRules(
        volume_participation_rate=Decimal("1"),
        slippage_bps=Decimal("10"),
        rules_version="rules-fixed",
        fee_schedule_version="fees-fixed",
    )
    active = apply_order_intent(
        _state(),
        _intent(
            "gap-stop",
            order_type=OrderType.STOP,
            limit_price=None,
            stop_price="10.00",
        ),
        rules,
    ).state

    result = apply_market_event(
        active,
        _bar(
            "gap-over-stop",
            open_price="10.50",
            high="10.60",
            low="10.40",
            close="10.55",
            volume=1_000,
        ),
    )
    fill = result.state.fills[0]

    assert fill.base_price == Decimal("10.50")
    assert fill.price == Decimal("10.52")
    assert fill.slippage == Decimal("2.00")
    assert fill.rules_version == "rules-fixed"
    assert fill.fee_schedule_version == "fees-fixed"
    assert fill.slippage_bps == Decimal("10")
    assert _order_by_intent(result.state, "gap-stop").rules == rules


def test_suspension_zero_volume_and_one_price_limits_are_side_specific():
    rules = AShareExecutionRules(slippage_bps=Decimal("0"))
    buy_state = apply_order_intent(
        _state(),
        _intent("blocked-buy", order_type=OrderType.MARKET, limit_price=None),
        rules,
    ).state

    blocked_bars = (
        _bar("suspended", hour=10, suspended=True),
        _bar("zero-volume", hour=11, volume=0),
        _bar(
            "one-price-limit-up",
            hour=12,
            open_price="11.00",
            high="11.00",
            low="11.00",
            close="11.00",
            previous_close="10.00",
        ),
    )
    for bar in blocked_bars:
        buy_state = apply_market_event(buy_state, bar).state
        assert buy_state.fills == ()
        assert _order_by_intent(buy_state, "blocked-buy").status == OrderStatus.ACTIVE

    buy_state = apply_market_event(buy_state, _bar("tradable", hour=13)).state
    assert _order_by_intent(buy_state, "blocked-buy").status == OrderStatus.FILLED

    position = Position(
        account_id="paper",
        instrument_id="CN:000001",
        quantity=100,
        sellable_quantity=100,
        average_cost=Decimal("10"),
        cost_basis=Decimal("1000"),
    )
    sell_state = apply_order_intent(
        _state("0", positions={"CN:000001": position}, session_date=DAY_1),
        _intent(
            "blocked-sell",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            limit_price=None,
        ),
        rules,
    ).state
    sell_state = apply_market_event(
        sell_state,
        _bar(
            "one-price-limit-down",
            hour=12,
            open_price="9.00",
            high="9.00",
            low="9.00",
            close="9.00",
            previous_close="10.00",
        ),
    ).state
    assert sell_state.fills == ()
    sell_state = apply_market_event(
        sell_state,
        _bar(
            "sell-at-limit-up",
            hour=13,
            open_price="11.00",
            high="11.00",
            low="11.00",
            close="11.00",
            previous_close="10.00",
        ),
    ).state
    assert _order_by_intent(sell_state, "blocked-sell").status == OrderStatus.FILLED
    assert sell_state.fills[0].price == Decimal("11.00")


def test_t1_and_t0_rules_control_sellable_quantity():
    t1_rules = AShareExecutionRules(settlement_days=1, slippage_bps=Decimal("0"))
    t1_state = apply_order_intent(_state(), _intent("t1-buy"), t1_rules).state
    t1_state = apply_market_event(t1_state, _bar("t1-buy-fill")).state
    assert t1_state.account.positions["CN:000001"].sellable_quantity == 0

    same_day_sell = apply_order_intent(
        t1_state,
        _intent("t1-same-day-sell", side=OrderSide.SELL, submitted_at=_at(DAY_1, 16)),
        t1_rules,
    ).state
    assert _order_by_intent(same_day_sell, "t1-same-day-sell").status == OrderStatus.REJECTED

    rolled = apply_market_event(
        t1_state,
        _bar(
            "next-session",
            day=DAY_2,
            hour=9,
            instrument_id="CN:600000",
        ),
    ).state
    assert rolled.account.positions["CN:000001"].sellable_quantity == 100
    next_day_sell = apply_order_intent(
        rolled,
        _intent(
            "t1-next-day-sell",
            side=OrderSide.SELL,
            submitted_at=_at(DAY_2, 10),
        ),
        t1_rules,
    ).state
    assert _order_by_intent(next_day_sell, "t1-next-day-sell").status == OrderStatus.ACTIVE

    t0_rules = AShareExecutionRules(settlement_days=0, slippage_bps=Decimal("0"))
    t0_state = apply_order_intent(_state(), _intent("t0-buy"), t0_rules).state
    t0_state = apply_market_event(t0_state, _bar("t0-buy-fill")).state
    assert t0_state.account.positions["CN:000001"].sellable_quantity == 100
    t0_state = apply_order_intent(
        t0_state,
        _intent("t0-sell", side=OrderSide.SELL, submitted_at=_at(DAY_1, 16)),
        t0_rules,
    ).state
    assert _order_by_intent(t0_state, "t0-sell").status == OrderStatus.ACTIVE


def test_buy_and_sell_fee_schedule_and_position_accounting_are_fixed():
    rules = AShareExecutionRules(
        volume_participation_rate=Decimal("1"),
        slippage_bps=Decimal("0"),
    )
    state = apply_order_intent(
        _state(),
        _intent("fee-buy", quantity=1000),
        rules,
    ).state
    state = apply_market_event(state, _bar("fee-buy-fill", volume=1_000)).state
    buy_fill = state.fills[0]
    assert buy_fill.commission == Decimal("5.00")
    assert buy_fill.stamp_duty == 0
    assert buy_fill.transfer_fee == Decimal("0.10")

    state = apply_market_event(
        state,
        _bar(
            "fee-rollover",
            day=DAY_2,
            hour=9,
            instrument_id="CN:600000",
        ),
    ).state
    state = apply_order_intent(
        state,
        _intent(
            "fee-sell",
            side=OrderSide.SELL,
            quantity=1000,
            submitted_at=_at(DAY_2, 10),
        ),
        rules,
    ).state
    state = apply_market_event(
        state,
        _bar("fee-sell-fill", day=DAY_2, volume=1_000),
    ).state
    sell_fill = state.fills[1]

    assert sell_fill.commission == Decimal("5.00")
    assert sell_fill.stamp_duty == Decimal("5.00")
    assert sell_fill.transfer_fee == Decimal("0.10")
    assert state.account.fees_paid == Decimal("15.20")
    assert state.account.cash == Decimal("99984.80")
    assert state.account.realized_pnl == Decimal("-15.20")
    assert state.account.positions["CN:000001"].quantity == 0


def test_history_and_paper_feeds_replay_identically_and_are_idempotent():
    rules = AShareExecutionRules(
        volume_participation_rate=Decimal("0.50"),
        slippage_bps=Decimal("0"),
    )
    intent = _intent("feed-order", quantity=300)
    active = apply_order_intent(_state(), intent, rules).state
    feed_events = (
        _bar(
            "not-touched",
            hour=10,
            open_price="10.20",
            high="10.30",
            low="10.10",
            close="10.20",
        ),
        _bar(
            "touched",
            hour=11,
            open_price="9.90",
            high="10.10",
            low="9.80",
            close="10.00",
            volume=1_000,
        ),
    )

    class HistoricalFeed:
        def __iter__(self):
            return iter(feed_events)

    class PaperFeed:
        def __iter__(self):
            return iter(feed_events)

    history = run_market_feed(active, HistoricalFeed())
    paper = run_market_feed(active, PaperFeed())

    assert history.events == paper.events
    assert history.state == paper.state
    assert _order_by_intent(history.state, "feed-order").status == OrderStatus.FILLED
    assert any(isinstance(event, FillRecordedEvent) for event in history.events)
    assert replay_events(active, history.events) == history.state

    fill_event = next(
        event for event in history.events if isinstance(event, FillRecordedEvent)
    )
    assert reduce_event(history.state, fill_event) is history.state
    assert process_market_event(history.state, feed_events[-1]) == ()
    assert active.fills == ()

    same_id_different_event = fill_event.model_copy(
        update={"occurred_at": _at(DAY_1, 12)}
    )
    with pytest.raises(IdempotencyConflict):
        reduce_event(history.state, same_id_different_event)

    tampered_fill = fill_event.fill.model_copy(
        update={"commission": fill_event.fill.commission + Decimal("0.01")}
    )
    tampered_fee_event = fill_event.model_copy(
        update={"event_id": "tampered-fee-event", "fill": tampered_fill}
    )
    with pytest.raises(ExecutionInvariantError, match="fees"):
        reduce_event(active, tampered_fee_event)

    conflicting_market = _bar(
        "touched",
        hour=11,
        open_price="9.95",
        high="10.10",
        low="9.80",
        close="10.00",
        volume=1_000,
    )
    with pytest.raises(IdempotencyConflict):
        process_market_event(history.state, conflicting_market)

    reused_intent = intent.model_copy(update={"quantity": 400})
    with pytest.raises(IdempotencyConflict):
        submit_order(active, reused_intent, rules)
