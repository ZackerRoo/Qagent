from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class PositionInput(BaseModel):
    instrument_id: str
    shares: Decimal
    entry_price: Decimal
    entry_date: date
    strategy_tag: str | None = None
    initial_stop: Decimal | None = None
    target_1: Decimal | None = None
    target_2: Decimal | None = None
    thesis: str | None = None


class PositionRisk(BaseModel):
    instrument_id: str
    current_price: Decimal
    unrealized_return_pct: float
    stop_distance_pct: float | None = None
    target_1_distance_pct: float | None = None
    status: str


def unrealized_return_pct(entry_price: Decimal, current_price: Decimal) -> float:
    if entry_price <= 0:
        raise ValueError("entry_price must be positive")
    return round(float((current_price / entry_price - Decimal("1")) * Decimal("100")), 4)


def analyze_position_risk(position: PositionInput, current_price: Decimal | str) -> PositionRisk:
    price = Decimal(str(current_price)).quantize(Decimal("0.01"))
    stop_distance = _stop_distance_pct(position.initial_stop, price)
    target_distance = _target_distance_pct(position.target_1, price)
    status = _risk_status(price, position.initial_stop, position.target_1)
    return PositionRisk(
        instrument_id=position.instrument_id,
        current_price=price,
        unrealized_return_pct=unrealized_return_pct(position.entry_price, price),
        stop_distance_pct=stop_distance,
        target_1_distance_pct=target_distance,
        status=status,
    )


def _stop_distance_pct(level: Decimal | None, current_price: Decimal) -> float | None:
    if level is None or current_price <= 0:
        return None
    return round(float(((current_price - level) / current_price) * Decimal("100")), 4)


def _target_distance_pct(level: Decimal | None, current_price: Decimal) -> float | None:
    if level is None or current_price <= 0:
        return None
    return round(float(((level - current_price) / current_price) * Decimal("100")), 4)


def _risk_status(
    current_price: Decimal, initial_stop: Decimal | None, target_1: Decimal | None
) -> str:
    if initial_stop is not None and current_price <= initial_stop:
        return "stop_breached"
    if target_1 is not None and current_price >= target_1:
        return "target_reached"
    return "inside_plan"
