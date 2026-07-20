from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

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
)
from qagent.execution.models import (
    Account,
    ExecutionState,
    Order,
    OrderSide,
    OrderStatus,
    Position,
)
from qagent.execution.rules import incremental_fee_breakdown, is_tick_aligned, money


class ExecutionInvariantError(ValueError):
    pass


class IdempotencyConflict(ExecutionInvariantError):
    pass


def reduce_event(state: ExecutionState, event: ExecutionEvent) -> ExecutionState:
    """Apply one immutable event.

    Re-applying an event with the same ID and payload is a no-op. Reusing an ID
    for a different payload is rejected instead of silently corrupting replay.
    """

    digest = canonical_digest(event)
    previous_digest = state.event_digests.get(event.event_id)
    if previous_digest is not None:
        if previous_digest != digest:
            raise IdempotencyConflict(f"event_id {event.event_id!r} has conflicting payloads")
        return state

    account = state.account
    orders = dict(state.orders)
    fills = state.fills
    session_date = state.session_date
    processed_market_events = dict(state.processed_market_events)

    if isinstance(event, OrderCreatedEvent):
        account, orders = _create_order(account, orders, event)
    elif isinstance(event, OrderActivatedEvent):
        account, orders = _activate_order(account, orders, event)
    elif isinstance(event, OrderRejectedEvent):
        account, orders = _close_order(
            account,
            orders,
            event.order_id,
            OrderStatus.REJECTED,
            event.reason,
            event.occurred_at,
            allowed={OrderStatus.PENDING_NEW},
        )
    elif isinstance(event, OrderCancelledEvent):
        account, orders = _close_order(
            account,
            orders,
            event.order_id,
            OrderStatus.CANCELLED,
            event.reason,
            event.occurred_at,
            allowed={
                OrderStatus.PENDING_NEW,
                OrderStatus.ACTIVE,
                OrderStatus.PARTIALLY_FILLED,
            },
        )
    elif isinstance(event, OrderExpiredEvent):
        account, orders = _close_order(
            account,
            orders,
            event.order_id,
            OrderStatus.EXPIRED,
            event.reason,
            event.occurred_at,
            allowed={
                OrderStatus.PENDING_NEW,
                OrderStatus.ACTIVE,
                OrderStatus.PARTIALLY_FILLED,
            },
        )
    elif isinstance(event, FillRecordedEvent):
        account, orders = _record_fill(account, orders, event)
        fills = (*fills, event.fill)
    elif isinstance(event, SessionAdvancedEvent):
        if session_date is not None and event.trading_date < session_date:
            raise ExecutionInvariantError("trading sessions must advance monotonically")
        if session_date is None or event.trading_date > session_date:
            positions = {
                instrument_id: position.model_copy(
                    update={"sellable_quantity": position.quantity}
                )
                for instrument_id, position in account.positions.items()
            }
            account = _replace_account(account, positions=positions)
            session_date = event.trading_date
    elif isinstance(event, MarketEventProcessedEvent):
        previous_market_digest = processed_market_events.get(event.market_event_id)
        if (
            previous_market_digest is not None
            and previous_market_digest != event.market_digest
        ):
            raise IdempotencyConflict(
                f"market event {event.market_event_id!r} has conflicting payloads"
            )
        processed_market_events[event.market_event_id] = event.market_digest
    else:  # pragma: no cover - the discriminated union makes this defensive only
        raise TypeError(f"unsupported execution event: {type(event).__name__}")

    event_digests = dict(state.event_digests)
    event_digests[event.event_id] = digest
    return ExecutionState(
        account=account,
        orders=orders,
        fills=fills,
        session_date=session_date,
        event_digests=event_digests,
        processed_market_events=processed_market_events,
    )


def reduce_events(
    state: ExecutionState,
    events: Iterable[ExecutionEvent],
) -> ExecutionState:
    for event in events:
        state = reduce_event(state, event)
    return state


def replay_events(
    initial_state: ExecutionState,
    events: Iterable[ExecutionEvent],
) -> ExecutionState:
    return reduce_events(initial_state, events)


execution_reducer = reduce_event


def _create_order(
    account: Account,
    orders: dict[str, Order],
    event: OrderCreatedEvent,
) -> tuple[Account, dict[str, Order]]:
    order = event.order
    if order.account_id != account.account_id:
        raise ExecutionInvariantError("order account does not match execution state")
    existing = orders.get(order.order_id)
    if existing is not None and existing != order:
        raise IdempotencyConflict(f"order_id {order.order_id!r} has conflicting payloads")
    if order.status != OrderStatus.PENDING_NEW:
        raise ExecutionInvariantError("new orders must start pending_new")
    orders[order.order_id] = order
    return account, orders


def _activate_order(
    account: Account,
    orders: dict[str, Order],
    event: OrderActivatedEvent,
) -> tuple[Account, dict[str, Order]]:
    order = _require_order(orders, event.order_id)
    if order.status != OrderStatus.PENDING_NEW:
        raise ExecutionInvariantError("only pending_new orders can become active")
    frozen_cash = account.frozen_cash + event.reserved_cash
    if frozen_cash > account.cash:
        raise ExecutionInvariantError("activation would over-reserve account cash")
    orders[order.order_id] = order.model_copy(
        update={
            "status": OrderStatus.ACTIVE,
            "reserved_cash": event.reserved_cash,
            "updated_at": event.occurred_at,
        }
    )
    account = _replace_account(account, frozen_cash=frozen_cash)
    return account, orders


def _close_order(
    account: Account,
    orders: dict[str, Order],
    order_id: str,
    status: OrderStatus,
    reason: str,
    occurred_at: datetime,
    *,
    allowed: set[OrderStatus],
) -> tuple[Account, dict[str, Order]]:
    order = _require_order(orders, order_id)
    if order.status not in allowed:
        raise ExecutionInvariantError(
            f"cannot transition order {order_id!r} from {order.status} to {status}"
        )
    frozen_cash = account.frozen_cash - order.reserved_cash
    if frozen_cash < 0:
        raise ExecutionInvariantError("order release would make frozen cash negative")
    account = _replace_account(account, frozen_cash=frozen_cash)
    orders[order_id] = order.model_copy(
        update={
            "status": status,
            "status_reason": reason,
            "reserved_cash": Decimal("0"),
            "updated_at": occurred_at,
        }
    )
    return account, orders


def _record_fill(
    account: Account,
    orders: dict[str, Order],
    event: FillRecordedEvent,
) -> tuple[Account, dict[str, Order]]:
    fill = event.fill
    order = _require_order(orders, fill.order_id)
    if order.status not in {OrderStatus.ACTIVE, OrderStatus.PARTIALLY_FILLED}:
        raise ExecutionInvariantError("fills require an active order")
    if fill.account_id != account.account_id or fill.account_id != order.account_id:
        raise ExecutionInvariantError("fill account does not match order")
    if fill.instrument_id != order.instrument_id or fill.side != order.side:
        raise ExecutionInvariantError("fill instrument or side does not match order")
    if fill.quantity > order.remaining_quantity:
        raise ExecutionInvariantError("fill exceeds remaining order quantity")
    if fill.quantity % order.rules.lot_size != 0:
        raise ExecutionInvariantError("fill quantity must respect the order lot size")
    if not is_tick_aligned(fill.price, order.rules.tick_size):
        raise ExecutionInvariantError("fill price must respect the order tick size")
    if fill.gross_amount != fill.price * fill.quantity:
        raise ExecutionInvariantError("fill gross_amount must equal price times quantity")
    if fill.rules_version != order.rules.rules_version:
        raise ExecutionInvariantError("fill rules version does not match order snapshot")
    if fill.fee_schedule_version != order.rules.fee_schedule_version:
        raise ExecutionInvariantError("fill fee schedule does not match order snapshot")
    if fill.slippage_bps != order.rules.slippage_bps:
        raise ExecutionInvariantError("fill slippage does not match order snapshot")
    expected_fees = incremental_fee_breakdown(order, fill.gross_amount)
    if (
        fill.commission != expected_fees.commission
        or fill.stamp_duty != expected_fees.stamp_duty
        or fill.transfer_fee != expected_fees.transfer_fee
    ):
        raise ExecutionInvariantError("fill fees do not match order fee snapshot")
    expected_slippage = money(abs(fill.price - fill.base_price) * fill.quantity)
    if fill.slippage != expected_slippage:
        raise ExecutionInvariantError("fill slippage amount does not match fill prices")
    if fill.side == OrderSide.SELL and fill.reserved_cash_after != 0:
        raise ExecutionInvariantError("sell fills cannot reserve cash")
    if fill.quantity == order.remaining_quantity and fill.reserved_cash_after != 0:
        raise ExecutionInvariantError("a completed order cannot retain reserved cash")

    positions = dict(account.positions)
    position = positions.get(
        fill.instrument_id,
        Position(account_id=account.account_id, instrument_id=fill.instrument_id),
    )
    fill_fees = fill.total_fees
    new_frozen_cash = account.frozen_cash - order.reserved_cash + fill.reserved_cash_after
    if new_frozen_cash < 0:
        raise ExecutionInvariantError("fill would make frozen cash negative")

    if fill.side == OrderSide.BUY:
        cash = money(account.cash - fill.gross_amount - fill_fees)
        if cash < 0:
            raise ExecutionInvariantError("fill would make cash negative")
        if new_frozen_cash > cash:
            raise ExecutionInvariantError("remaining reservations exceed post-fill cash")
        quantity = position.quantity + fill.quantity
        cost_basis = money(position.cost_basis + fill.gross_amount + fill_fees)
        positions[fill.instrument_id] = position.model_copy(
            update={
                "quantity": quantity,
                "sellable_quantity": (
                    position.sellable_quantity + fill.quantity
                    if order.rules.settlement_days == 0
                    else position.sellable_quantity
                ),
                "cost_basis": cost_basis,
                "average_cost": _unit_value(cost_basis, quantity),
                "last_fill_at": fill.occurred_at,
            }
        )
        realized_pnl = account.realized_pnl
    else:
        if fill.quantity > position.sellable_quantity:
            raise ExecutionInvariantError("sell fill exceeds sellable quantity")
        released_cost = (
            position.cost_basis
            if fill.quantity == position.quantity
            else money(position.cost_basis * fill.quantity / position.quantity)
        )
        realized = money(fill.gross_amount - fill_fees - released_cost)
        quantity = position.quantity - fill.quantity
        cost_basis = money(position.cost_basis - released_cost)
        positions[fill.instrument_id] = position.model_copy(
            update={
                "quantity": quantity,
                "sellable_quantity": position.sellable_quantity - fill.quantity,
                "cost_basis": cost_basis,
                "average_cost": (
                    _unit_value(cost_basis, quantity) if quantity else Decimal("0")
                ),
                "realized_pnl": money(position.realized_pnl + realized),
                "last_fill_at": fill.occurred_at,
            }
        )
        cash = money(account.cash + fill.gross_amount - fill_fees)
        realized_pnl = money(account.realized_pnl + realized)

    account = _replace_account(
        account,
        cash=cash,
        frozen_cash=new_frozen_cash,
        positions=positions,
        fees_paid=money(account.fees_paid + fill_fees),
        slippage_paid=money(account.slippage_paid + fill.slippage),
        realized_pnl=realized_pnl,
    )

    filled_quantity = order.filled_quantity + fill.quantity
    gross_filled = order.gross_filled + fill.gross_amount
    status = (
        OrderStatus.FILLED
        if filled_quantity == order.quantity
        else OrderStatus.PARTIALLY_FILLED
    )
    orders[order.order_id] = order.model_copy(
        update={
            "status": status,
            "filled_quantity": filled_quantity,
            "average_fill_price": _unit_value(gross_filled, filled_quantity),
            "gross_filled": gross_filled,
            "commission_paid": money(order.commission_paid + fill.commission),
            "stamp_duty_paid": money(order.stamp_duty_paid + fill.stamp_duty),
            "transfer_fee_paid": money(order.transfer_fee_paid + fill.transfer_fee),
            "slippage_paid": money(order.slippage_paid + fill.slippage),
            "reserved_cash": fill.reserved_cash_after,
            "updated_at": fill.occurred_at,
        }
    )
    return account, orders


def _unit_value(total: Decimal, quantity: int) -> Decimal:
    return (total / quantity).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _require_order(orders: dict[str, Order], order_id: str) -> Order:
    try:
        return orders[order_id]
    except KeyError as exc:
        raise ExecutionInvariantError(f"unknown order_id {order_id!r}") from exc


def _replace_account(account: Account, **updates: object) -> Account:
    payload = {name: getattr(account, name) for name in Account.model_fields}
    payload.update(updates)
    return Account.model_validate(payload)
