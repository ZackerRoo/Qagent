from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_UP

from qagent.execution.models import (
    AShareExecutionRules,
    FeeBreakdown,
    MarketEvent,
    Order,
    OrderSide,
    OrderType,
)


MONEY_QUANTUM = Decimal("0.01")
BASIS_POINTS = Decimal("10000")


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def round_to_tick(
    value: Decimal,
    tick_size: Decimal,
    *,
    rounding: str = ROUND_HALF_UP,
) -> Decimal:
    ticks = (value / tick_size).to_integral_value(rounding=rounding)
    return ticks * tick_size


def is_tick_aligned(value: Decimal, tick_size: Decimal) -> bool:
    return value == round_to_tick(value, tick_size)


def round_lot(quantity: int, lot_size: int) -> int:
    if quantity <= 0:
        return 0
    return (quantity // lot_size) * lot_size


def fee_breakdown(
    side: OrderSide,
    gross_amount: Decimal,
    rules: AShareExecutionRules,
) -> FeeBreakdown:
    if gross_amount <= 0:
        return FeeBreakdown()
    variable_commission = money(gross_amount * rules.commission_bps / BASIS_POINTS)
    commission = max(variable_commission, money(rules.minimum_commission))
    stamp_duty = (
        money(gross_amount * rules.stamp_duty_bps / BASIS_POINTS)
        if side == OrderSide.SELL
        else Decimal("0")
    )
    transfer_fee = money(gross_amount * rules.transfer_fee_bps / BASIS_POINTS)
    return FeeBreakdown(
        commission=commission,
        stamp_duty=stamp_duty,
        transfer_fee=transfer_fee,
    )


def incremental_fee_breakdown(
    order: Order,
    additional_gross: Decimal,
) -> FeeBreakdown:
    cumulative = fee_breakdown(order.side, order.gross_filled + additional_gross, order.rules)
    return FeeBreakdown(
        commission=max(cumulative.commission - order.commission_paid, Decimal("0")),
        stamp_duty=max(cumulative.stamp_duty - order.stamp_duty_paid, Decimal("0")),
        transfer_fee=max(cumulative.transfer_fee - order.transfer_fee_paid, Decimal("0")),
    )


def estimate_buy_reservation(
    order: Order,
    quantity: int,
    price: Decimal | None,
) -> Decimal:
    if order.side != OrderSide.BUY or quantity <= 0 or price is None:
        return Decimal("0")
    remaining_gross = price * quantity
    projected = fee_breakdown(
        order.side,
        order.gross_filled + remaining_gross,
        order.rules,
    )
    additional_fees = (
        max(projected.commission - order.commission_paid, Decimal("0"))
        + max(projected.stamp_duty - order.stamp_duty_paid, Decimal("0"))
        + max(projected.transfer_fee - order.transfer_fee_paid, Decimal("0"))
    )
    return money(remaining_gross + additional_fees)


def reservation_price(order: Order) -> Decimal | None:
    if order.order_type in {OrderType.LIMIT, OrderType.STOP_LIMIT}:
        return order.limit_price
    return order.estimated_price


def participation_capacity(volume: int, rules: AShareExecutionRules) -> int:
    raw = (Decimal(volume) * rules.volume_participation_rate).to_integral_value(
        rounding=ROUND_DOWN
    )
    return round_lot(int(raw), rules.lot_size)


def price_limits(
    market: MarketEvent,
    rules: AShareExecutionRules,
) -> tuple[Decimal | None, Decimal | None]:
    up = market.limit_up_price
    down = market.limit_down_price
    rate = market.price_limit_rate
    if rate is None:
        rate = rules.price_limit_rate
    if market.previous_close is None or rate is None:
        return up, down
    if up is None:
        up = round_to_tick(
            market.previous_close * (Decimal("1") + rate),
            rules.tick_size,
        )
    if down is None:
        down = round_to_tick(
            market.previous_close * (Decimal("1") - rate),
            rules.tick_size,
        )
    return up, down


def is_one_price_limit_blocked(
    side: OrderSide,
    market: MarketEvent,
    rules: AShareExecutionRules,
) -> bool:
    if not (market.open == market.high == market.low == market.close):
        return False
    limit_up, limit_down = price_limits(market, rules)
    if side == OrderSide.BUY:
        return limit_up is not None and market.close >= limit_up
    return limit_down is not None and market.close <= limit_down


def match_base_price(order: Order, market: MarketEvent) -> Decimal | None:
    if order.order_type == OrderType.MARKET:
        return market.open

    if order.order_type == OrderType.LIMIT:
        return _limit_base_price(order, market)

    if order.order_type == OrderType.STOP:
        assert order.stop_price is not None
        if order.side == OrderSide.BUY:
            if market.open >= order.stop_price:
                return market.open
            return order.stop_price if market.high >= order.stop_price else None
        if market.open <= order.stop_price:
            return market.open
        return order.stop_price if market.low <= order.stop_price else None

    return _stop_limit_base_price(order, market)


def _limit_base_price(order: Order, market: MarketEvent) -> Decimal | None:
    assert order.limit_price is not None
    if order.side == OrderSide.BUY:
        if market.open <= order.limit_price:
            return market.open
        return order.limit_price if market.low <= order.limit_price else None
    if market.open >= order.limit_price:
        return market.open
    return order.limit_price if market.high >= order.limit_price else None


def _stop_limit_base_price(order: Order, market: MarketEvent) -> Decimal | None:
    assert order.stop_price is not None and order.limit_price is not None
    if order.side == OrderSide.BUY:
        triggered = market.open >= order.stop_price or market.high >= order.stop_price
        if not triggered:
            return None
        if market.open >= order.stop_price and market.open <= order.limit_price:
            return market.open
        if market.low <= order.limit_price and order.stop_price <= order.limit_price:
            return order.stop_price
        return None
    triggered = market.open <= order.stop_price or market.low <= order.stop_price
    if not triggered:
        return None
    if market.open <= order.stop_price and market.open >= order.limit_price:
        return market.open
    if market.high >= order.limit_price and order.stop_price >= order.limit_price:
        return order.stop_price
    return None


def apply_slippage(
    order: Order,
    market: MarketEvent,
    base_price: Decimal,
) -> Decimal:
    slip = order.rules.slippage_bps / BASIS_POINTS
    limit_up, limit_down = price_limits(market, order.rules)
    if order.side == OrderSide.BUY:
        target = base_price * (Decimal("1") + slip)
        upper = market.high
        if limit_up is not None:
            upper = min(upper, limit_up)
        if order.order_type in {OrderType.LIMIT, OrderType.STOP_LIMIT}:
            assert order.limit_price is not None
            upper = min(upper, order.limit_price)
        target = min(max(target, market.low), upper)
        price = round_to_tick(target, order.rules.tick_size, rounding=ROUND_CEILING)
        if price > upper:
            price = round_to_tick(upper, order.rules.tick_size, rounding=ROUND_FLOOR)
        return price

    target = base_price * (Decimal("1") - slip)
    lower = market.low
    if limit_down is not None:
        lower = max(lower, limit_down)
    if order.order_type in {OrderType.LIMIT, OrderType.STOP_LIMIT}:
        assert order.limit_price is not None
        lower = max(lower, order.limit_price)
    target = max(min(target, market.high), lower)
    price = round_to_tick(target, order.rules.tick_size, rounding=ROUND_FLOOR)
    if price < lower:
        price = round_to_tick(lower, order.rules.tick_size, rounding=ROUND_CEILING)
    return price
