from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from qagent.execution.events import (
    ExecutionEvent,
    FillRecordedEvent,
    MarketEventProcessedEvent,
    OrderActivatedEvent,
    OrderCancelledEvent,
    OrderCreatedEvent,
    OrderExpiredEvent,
    OrderRejectedEvent,
    SessionAdvancedEvent,
    canonical_digest,
    stable_id,
)
from qagent.execution.models import (
    AShareExecutionRules,
    ExecutionState,
    Fill,
    MarketEvent,
    Order,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from qagent.execution.reducer import (
    IdempotencyConflict,
    reduce_event,
    reduce_events,
)
from qagent.execution.rules import (
    apply_slippage,
    estimate_buy_reservation,
    incremental_fee_breakdown,
    is_one_price_limit_blocked,
    is_tick_aligned,
    match_base_price,
    money,
    participation_capacity,
    reservation_price,
    round_lot,
)


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    state: ExecutionState
    events: tuple[ExecutionEvent, ...]


def submit_order(
    state: ExecutionState,
    intent: OrderIntent,
    rules: AShareExecutionRules,
) -> tuple[ExecutionEvent, ...]:
    """Turn an intent into deterministic pending_new + terminal/active events."""

    if intent.account_id != state.account.account_id:
        raise ValueError("intent account_id does not match execution state")

    expected = _order_from_intent(intent, rules)
    existing = state.orders.get(expected.order_id)
    events: list[ExecutionEvent] = []
    working = state
    if existing is None:
        created = OrderCreatedEvent(
            event_id=stable_id("evt", "order_created", expected.order_id),
            occurred_at=intent.submitted_at,
            order=expected,
        )
        events.append(created)
        working = reduce_event(working, created)
        order = expected
    else:
        _assert_same_order_contract(existing, expected)
        if existing.status != OrderStatus.PENDING_NEW:
            return ()
        order = existing

    rejection = _validate_order(working, order)
    if rejection is not None:
        rejected = OrderRejectedEvent(
            event_id=stable_id("evt", "order_rejected", order.order_id, rejection),
            occurred_at=intent.submitted_at,
            order_id=order.order_id,
            reason=rejection,
        )
        events.append(rejected)
        return tuple(events)

    reserved_cash = estimate_buy_reservation(
        order,
        order.quantity,
        reservation_price(order),
    )
    if reserved_cash > working.account.available_cash:
        rejected = OrderRejectedEvent(
            event_id=stable_id(
                "evt",
                "order_rejected",
                order.order_id,
                "insufficient_cash",
            ),
            occurred_at=intent.submitted_at,
            order_id=order.order_id,
            reason="insufficient_cash",
        )
        events.append(rejected)
        return tuple(events)

    activated = OrderActivatedEvent(
        event_id=stable_id("evt", "order_activated", order.order_id),
        occurred_at=intent.submitted_at,
        order_id=order.order_id,
        reserved_cash=reserved_cash,
    )
    events.append(activated)
    return tuple(events)


def apply_order_intent(
    state: ExecutionState,
    intent: OrderIntent,
    rules: AShareExecutionRules,
) -> ExecutionResult:
    events = submit_order(state, intent, rules)
    return ExecutionResult(state=reduce_events(state, events), events=events)


def cancel_order(
    state: ExecutionState,
    order_id: str,
    occurred_at: datetime,
    *,
    reason: str = "cancelled_by_client",
    request_id: str | None = None,
) -> tuple[ExecutionEvent, ...]:
    try:
        order = state.orders[order_id]
    except KeyError as exc:
        raise ValueError(f"unknown order_id {order_id!r}") from exc
    if order.is_terminal:
        return ()
    event = OrderCancelledEvent(
        event_id=stable_id(
            "evt",
            "order_cancelled",
            request_id or order_id,
            occurred_at.isoformat(),
            reason,
        ),
        occurred_at=occurred_at,
        order_id=order_id,
        reason=reason,
    )
    return (event,)


def expire_order(
    state: ExecutionState,
    order_id: str,
    occurred_at: datetime,
    *,
    reason: str = "expired",
) -> tuple[ExecutionEvent, ...]:
    try:
        order = state.orders[order_id]
    except KeyError as exc:
        raise ValueError(f"unknown order_id {order_id!r}") from exc
    if order.is_terminal:
        return ()
    return (
        OrderExpiredEvent(
            event_id=stable_id(
                "evt",
                "order_expired",
                order_id,
                occurred_at.isoformat(),
                reason,
            ),
            occurred_at=occurred_at,
            order_id=order_id,
            reason=reason,
        ),
    )


def process_market_event(
    state: ExecutionState,
    market: MarketEvent,
) -> tuple[ExecutionEvent, ...]:
    """Match one normalized market event without I/O, clocks, or randomness."""

    market_digest = canonical_digest(market)
    previous_digest = state.processed_market_events.get(market.event_id)
    if previous_digest is not None:
        if previous_digest != market_digest:
            raise IdempotencyConflict(
                f"market event {market.event_id!r} has conflicting payloads"
            )
        return ()
    assert market.trading_date is not None
    if state.session_date is not None and market.trading_date < state.session_date:
        raise ValueError("market events must be processed in trading-date order")

    events: list[ExecutionEvent] = []
    working = state

    def emit(event: ExecutionEvent) -> None:
        nonlocal working
        events.append(event)
        working = reduce_event(working, event)

    if working.session_date is None or market.trading_date > working.session_date:
        emit(
            SessionAdvancedEvent(
                event_id=stable_id("evt", "session", market.trading_date.isoformat()),
                occurred_at=market.occurred_at,
                trading_date=market.trading_date,
            )
        )

    for order in sorted(
        working.orders.values(),
        key=lambda item: (item.submitted_at.isoformat(), item.order_id),
    ):
        if not order.is_open:
            continue
        if order.expires_at is not None and order.expires_at <= market.occurred_at:
            emit(
                OrderExpiredEvent(
                    event_id=stable_id(
                        "evt", "order_expired", order.order_id, market.event_id
                    ),
                    occurred_at=market.occurred_at,
                    order_id=order.order_id,
                    reason="expires_at_reached",
                )
            )
        elif (
            order.time_in_force == TimeInForce.DAY
            and market.trading_date > order.submitted_at.date()
        ):
            emit(
                OrderExpiredEvent(
                    event_id=stable_id(
                        "evt", "day_order_expired", order.order_id, market.event_id
                    ),
                    occurred_at=market.occurred_at,
                    order_id=order.order_id,
                    reason="day_expired",
                )
            )

    used_quantity = 0
    candidates = sorted(
        (
            order
            for order in working.orders.values()
            if order.instrument_id == market.instrument_id
            and order.status in {OrderStatus.ACTIVE, OrderStatus.PARTIALLY_FILLED}
            and order.submitted_at <= market.occurred_at
        ),
        key=lambda item: (item.submitted_at.isoformat(), item.order_id),
    )
    for stale_order in candidates:
        order = working.orders[stale_order.order_id]
        if order.status not in {OrderStatus.ACTIVE, OrderStatus.PARTIALLY_FILLED}:
            continue
        if market.suspended or market.volume == 0:
            continue
        if is_one_price_limit_blocked(order.side, market, order.rules):
            continue

        base_price = match_base_price(order, market)
        if base_price is None:
            continue
        event_capacity = participation_capacity(market.volume, order.rules)
        available_capacity = round_lot(
            max(event_capacity - used_quantity, 0),
            order.rules.lot_size,
        )
        quantity = round_lot(
            min(order.remaining_quantity, available_capacity),
            order.rules.lot_size,
        )
        if order.side == OrderSide.SELL:
            position = working.account.positions.get(order.instrument_id)
            sellable = position.sellable_quantity if position is not None else 0
            quantity = round_lot(min(quantity, sellable), order.rules.lot_size)
        if quantity <= 0:
            continue

        price = apply_slippage(order, market, base_price)
        if order.side == OrderSide.BUY:
            quantity = _affordable_quantity(working, order, quantity, price)
            if quantity <= 0:
                emit(
                    OrderCancelledEvent(
                        event_id=stable_id(
                            "evt",
                            "insufficient_cash",
                            order.order_id,
                            market.event_id,
                        ),
                        occurred_at=market.occurred_at,
                        order_id=order.order_id,
                        reason="insufficient_cash_at_execution",
                    )
                )
                continue

        gross_amount = price * quantity
        fees = incremental_fee_breakdown(order, gross_amount)
        remaining = order.remaining_quantity - quantity
        projected_order = order.model_copy(
            update={
                "gross_filled": order.gross_filled + gross_amount,
                "commission_paid": order.commission_paid + fees.commission,
                "stamp_duty_paid": order.stamp_duty_paid + fees.stamp_duty,
                "transfer_fee_paid": order.transfer_fee_paid + fees.transfer_fee,
            }
        )
        reserved_cash_after = estimate_buy_reservation(
            projected_order,
            remaining,
            reservation_price(order),
        )
        if order.side == OrderSide.BUY:
            post_fill_cash = money(working.account.cash - gross_amount - fees.total)
            other_frozen = working.account.frozen_cash - order.reserved_cash
            reserved_cash_after = min(
                reserved_cash_after,
                max(post_fill_cash - other_frozen, Decimal("0")),
            )

        fill = Fill(
            fill_id=stable_id(
                "fill",
                market.event_id,
                order.order_id,
                order.filled_quantity,
                quantity,
            ),
            order_id=order.order_id,
            account_id=order.account_id,
            instrument_id=order.instrument_id,
            market_event_id=market.event_id,
            side=order.side,
            quantity=quantity,
            base_price=base_price,
            price=price,
            gross_amount=gross_amount,
            commission=fees.commission,
            stamp_duty=fees.stamp_duty,
            transfer_fee=fees.transfer_fee,
            slippage=money(abs(price - base_price) * quantity),
            occurred_at=market.occurred_at,
            trading_date=market.trading_date,
            rules_version=order.rules.rules_version,
            fee_schedule_version=order.rules.fee_schedule_version,
            slippage_bps=order.rules.slippage_bps,
            reserved_cash_after=reserved_cash_after,
        )
        emit(
            FillRecordedEvent(
                event_id=stable_id("evt", "fill_recorded", fill.fill_id),
                occurred_at=market.occurred_at,
                fill=fill,
            )
        )
        used_quantity += quantity

    for stale_order in candidates:
        order = working.orders[stale_order.order_id]
        if order.time_in_force == TimeInForce.IOC and order.status in {
            OrderStatus.ACTIVE,
            OrderStatus.PARTIALLY_FILLED,
        }:
            emit(
                OrderCancelledEvent(
                    event_id=stable_id(
                        "evt", "ioc_remainder", order.order_id, market.event_id
                    ),
                    occurred_at=market.occurred_at,
                    order_id=order.order_id,
                    reason="ioc_remainder_cancelled",
                )
            )

    emit(
        MarketEventProcessedEvent(
            event_id=stable_id(
                "evt", "market_processed", market.event_id, market_digest
            ),
            occurred_at=market.occurred_at,
            market_event_id=market.event_id,
            market_digest=market_digest,
        )
    )
    return tuple(events)


def apply_market_event(
    state: ExecutionState,
    market: MarketEvent,
) -> ExecutionResult:
    events = process_market_event(state, market)
    return ExecutionResult(state=reduce_events(state, events), events=events)


def run_market_feed(
    state: ExecutionState,
    feed: Iterable[MarketEvent],
) -> ExecutionResult:
    all_events: list[ExecutionEvent] = []
    for market in feed:
        events = process_market_event(state, market)
        state = reduce_events(state, events)
        all_events.extend(events)
    return ExecutionResult(state=state, events=tuple(all_events))


run_feed = run_market_feed
on_market_event = process_market_event
handle_order_intent = submit_order


def _order_from_intent(
    intent: OrderIntent,
    rules: AShareExecutionRules,
) -> Order:
    return Order(
        order_id=stable_id("ord", intent.account_id, intent.intent_id),
        intent_id=intent.intent_id,
        account_id=intent.account_id,
        instrument_id=intent.instrument_id,
        side=intent.side,
        quantity=intent.quantity,
        submitted_at=intent.submitted_at,
        order_type=intent.order_type,
        limit_price=intent.limit_price,
        stop_price=intent.stop_price,
        estimated_price=intent.estimated_price,
        time_in_force=intent.time_in_force,
        expires_at=intent.expires_at,
        updated_at=intent.submitted_at,
        rules=rules,
    )


def _assert_same_order_contract(existing: Order, expected: Order) -> None:
    mutable_fields = {
        "status",
        "filled_quantity",
        "average_fill_price",
        "gross_filled",
        "commission_paid",
        "stamp_duty_paid",
        "transfer_fee_paid",
        "slippage_paid",
        "reserved_cash",
        "status_reason",
        "updated_at",
    }
    existing_contract = existing.model_dump(exclude=mutable_fields)
    expected_contract = expected.model_dump(exclude=mutable_fields)
    if existing_contract != expected_contract:
        raise IdempotencyConflict(
            f"intent_id {expected.intent_id!r} was reused with a different order"
        )


def _validate_order(state: ExecutionState, order: Order) -> str | None:
    if order.quantity % order.rules.lot_size != 0:
        return "quantity_must_be_round_lot"
    if order.expires_at is not None and order.expires_at <= order.submitted_at:
        return "expired_before_submission"
    if order.order_type in {OrderType.LIMIT, OrderType.STOP_LIMIT}:
        if order.limit_price is None:
            return "limit_price_required"
        if not is_tick_aligned(order.limit_price, order.rules.tick_size):
            return "limit_price_not_on_tick"
    if order.order_type in {OrderType.STOP, OrderType.STOP_LIMIT}:
        if order.stop_price is None:
            return "stop_price_required"
        if not is_tick_aligned(order.stop_price, order.rules.tick_size):
            return "stop_price_not_on_tick"
    if order.order_type == OrderType.STOP_LIMIT:
        assert order.stop_price is not None and order.limit_price is not None
        invalid = (
            order.side == OrderSide.BUY and order.limit_price < order.stop_price
        ) or (order.side == OrderSide.SELL and order.limit_price > order.stop_price)
        if invalid:
            return "invalid_stop_limit_prices"
    if order.estimated_price is not None and not is_tick_aligned(
        order.estimated_price, order.rules.tick_size
    ):
        return "estimated_price_not_on_tick"
    if order.side == OrderSide.SELL:
        position = state.account.positions.get(order.instrument_id)
        sellable = position.sellable_quantity if position is not None else 0
        committed = sum(
            other.remaining_quantity
            for other in state.orders.values()
            if other.order_id != order.order_id
            and other.instrument_id == order.instrument_id
            and other.side == OrderSide.SELL
            and other.status in {OrderStatus.ACTIVE, OrderStatus.PARTIALLY_FILLED}
        )
        if order.quantity > max(sellable - committed, 0):
            return "insufficient_sellable_quantity"
    return None


def _affordable_quantity(
    state: ExecutionState,
    order: Order,
    desired_quantity: int,
    price: Decimal,
) -> int:
    lot_size = order.rules.lot_size
    available = state.account.available_cash + order.reserved_cash
    low = 0
    high = desired_quantity // lot_size
    while low < high:
        middle = (low + high + 1) // 2
        quantity = middle * lot_size
        gross = price * quantity
        fees = incremental_fee_breakdown(order, gross)
        if gross + fees.total <= available:
            low = middle
        else:
            high = middle - 1
    return low * lot_size
