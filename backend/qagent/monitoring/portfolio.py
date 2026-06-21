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


def unrealized_return_pct(entry_price: Decimal, current_price: Decimal) -> float:
    if entry_price <= 0:
        raise ValueError("entry_price must be positive")
    return round(float((current_price / entry_price - Decimal("1")) * Decimal("100")), 4)
