from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Literal, Mapping, Self

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
        validate_default=True,
    )


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(StrEnum):
    GTC = "gtc"
    DAY = "day"
    IOC = "ioc"


class OrderStatus(StrEnum):
    PENDING_NEW = "pending_new"
    ACTIVE = "active"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


OPEN_ORDER_STATUSES = frozenset(
    {
        OrderStatus.PENDING_NEW,
        OrderStatus.ACTIVE,
        OrderStatus.PARTIALLY_FILLED,
    }
)
TERMINAL_ORDER_STATUSES = frozenset(
    {
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
    }
)


class AShareExecutionRules(FrozenModel):
    """Versioned rules captured on every order.

    Rates ending in ``_bps`` are basis points. ``price_limit_rate`` and
    ``volume_participation_rate`` are decimal fractions (``0.10`` means 10%).
    """

    rules_version: str = "a-share-execution-v1"
    fee_schedule_version: str = "a-share-fees-v1"
    tick_size: Decimal = Field(default=Decimal("0.01"), gt=0)
    lot_size: int = Field(default=100, gt=0)
    settlement_days: Literal[0, 1] = 1
    price_limit_rate: Decimal | None = Field(default=Decimal("0.10"), gt=0, le=1)
    volume_participation_rate: Decimal = Field(default=Decimal("0.10"), ge=0, le=1)
    commission_bps: Decimal = Field(default=Decimal("3"), ge=0)
    minimum_commission: Decimal = Field(default=Decimal("5"), ge=0)
    stamp_duty_bps: Decimal = Field(default=Decimal("5"), ge=0)
    transfer_fee_bps: Decimal = Field(default=Decimal("0.1"), ge=0)
    slippage_bps: Decimal = Field(default=Decimal("5"), ge=0)


class OrderIntent(FrozenModel):
    intent_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    side: OrderSide
    quantity: int = Field(gt=0)
    submitted_at: datetime = Field(
        validation_alias=AliasChoices("submitted_at", "created_at")
    )
    order_type: OrderType = OrderType.MARKET
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    estimated_price: Decimal | None = Field(default=None, gt=0)
    time_in_force: TimeInForce = TimeInForce.GTC
    expires_at: datetime | None = None

    @property
    def created_at(self) -> datetime:
        return self.submitted_at


class Order(FrozenModel):
    order_id: str = Field(min_length=1)
    intent_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    side: OrderSide
    quantity: int = Field(gt=0)
    submitted_at: datetime
    order_type: OrderType
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    estimated_price: Decimal | None = Field(default=None, gt=0)
    time_in_force: TimeInForce
    expires_at: datetime | None = None
    status: OrderStatus = OrderStatus.PENDING_NEW
    filled_quantity: int = Field(default=0, ge=0)
    average_fill_price: Decimal | None = Field(default=None, gt=0)
    gross_filled: Decimal = Field(default=Decimal("0"), ge=0)
    commission_paid: Decimal = Field(default=Decimal("0"), ge=0)
    stamp_duty_paid: Decimal = Field(default=Decimal("0"), ge=0)
    transfer_fee_paid: Decimal = Field(default=Decimal("0"), ge=0)
    slippage_paid: Decimal = Field(default=Decimal("0"), ge=0)
    reserved_cash: Decimal = Field(default=Decimal("0"), ge=0)
    status_reason: str | None = None
    updated_at: datetime
    rules: AShareExecutionRules

    @model_validator(mode="after")
    def validate_filled_quantity(self) -> Self:
        if self.filled_quantity > self.quantity:
            raise ValueError("filled_quantity must not exceed quantity")
        return self

    @property
    def remaining_quantity(self) -> int:
        return self.quantity - self.filled_quantity

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_ORDER_STATUSES

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_ORDER_STATUSES

    @property
    def total_fees(self) -> Decimal:
        return self.commission_paid + self.stamp_duty_paid + self.transfer_fee_paid


class FeeBreakdown(FrozenModel):
    commission: Decimal = Field(default=Decimal("0"), ge=0)
    stamp_duty: Decimal = Field(default=Decimal("0"), ge=0)
    transfer_fee: Decimal = Field(default=Decimal("0"), ge=0)

    @property
    def total(self) -> Decimal:
        return self.commission + self.stamp_duty + self.transfer_fee


class Fill(FrozenModel):
    fill_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    market_event_id: str = Field(min_length=1)
    side: OrderSide
    quantity: int = Field(gt=0)
    base_price: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    gross_amount: Decimal = Field(gt=0)
    commission: Decimal = Field(default=Decimal("0"), ge=0)
    stamp_duty: Decimal = Field(default=Decimal("0"), ge=0)
    transfer_fee: Decimal = Field(default=Decimal("0"), ge=0)
    slippage: Decimal = Field(default=Decimal("0"), ge=0)
    occurred_at: datetime
    trading_date: date
    rules_version: str
    fee_schedule_version: str
    slippage_bps: Decimal = Field(ge=0)
    reserved_cash_after: Decimal = Field(default=Decimal("0"), ge=0)

    @property
    def total_fees(self) -> Decimal:
        return self.commission + self.stamp_duty + self.transfer_fee

    @property
    def net_cash_flow(self) -> Decimal:
        if self.side == OrderSide.BUY:
            return -(self.gross_amount + self.total_fees)
        return self.gross_amount - self.total_fees


class Position(FrozenModel):
    account_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    quantity: int = Field(default=0, ge=0)
    sellable_quantity: int = Field(default=0, ge=0)
    average_cost: Decimal = Field(default=Decimal("0"), ge=0)
    cost_basis: Decimal = Field(default=Decimal("0"), ge=0)
    realized_pnl: Decimal = Decimal("0")
    last_fill_at: datetime | None = None

    @model_validator(mode="after")
    def validate_sellable_quantity(self) -> Self:
        if self.sellable_quantity > self.quantity:
            raise ValueError("sellable_quantity must not exceed quantity")
        if self.quantity == 0 and (self.average_cost != 0 or self.cost_basis != 0):
            raise ValueError("an empty position must have zero cost")
        return self


class Account(FrozenModel):
    account_id: str = Field(min_length=1)
    cash: Decimal = Field(ge=0)
    frozen_cash: Decimal = Field(default=Decimal("0"), ge=0)
    positions: Mapping[str, Position] = Field(default_factory=dict)
    fees_paid: Decimal = Field(default=Decimal("0"), ge=0)
    slippage_paid: Decimal = Field(default=Decimal("0"), ge=0)
    realized_pnl: Decimal = Decimal("0")

    @field_validator("positions", mode="after")
    @classmethod
    def freeze_positions(cls, value: Mapping[str, Position]) -> Mapping[str, Position]:
        return MappingProxyType(dict(value))

    @model_validator(mode="after")
    def validate_balances(self) -> Self:
        if self.frozen_cash > self.cash:
            raise ValueError("frozen_cash must not exceed cash")
        for instrument_id, position in self.positions.items():
            if instrument_id != position.instrument_id:
                raise ValueError("position key must match instrument_id")
            if position.account_id != self.account_id:
                raise ValueError("position account_id must match account")
        return self

    @field_serializer("positions")
    def serialize_positions(self, value: Mapping[str, Position]) -> dict[str, Position]:
        return dict(value)

    @property
    def available_cash(self) -> Decimal:
        return self.cash - self.frozen_cash


class MarketEvent(FrozenModel):
    """A normalized OHLCV event accepted by both historical and paper feeds."""

    event_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    occurred_at: datetime = Field(
        validation_alias=AliasChoices("occurred_at", "timestamp", "as_of")
    )
    trading_date: date | None = None
    open: Decimal = Field(validation_alias=AliasChoices("open", "open_price"), gt=0)
    high: Decimal = Field(validation_alias=AliasChoices("high", "high_price"), gt=0)
    low: Decimal = Field(validation_alias=AliasChoices("low", "low_price"), gt=0)
    close: Decimal = Field(validation_alias=AliasChoices("close", "close_price"), gt=0)
    volume: int = Field(ge=0)
    previous_close: Decimal | None = Field(default=None, gt=0)
    suspended: bool = Field(
        default=False,
        validation_alias=AliasChoices("suspended", "is_suspended"),
    )
    limit_up_price: Decimal | None = Field(default=None, gt=0)
    limit_down_price: Decimal | None = Field(default=None, gt=0)
    price_limit_rate: Decimal | None = Field(default=None, gt=0, le=1)

    @model_validator(mode="after")
    def validate_bar(self) -> Self:
        if self.low > self.high:
            raise ValueError("low must not exceed high")
        if not self.low <= self.open <= self.high:
            raise ValueError("open must be inside low/high")
        if not self.low <= self.close <= self.high:
            raise ValueError("close must be inside low/high")
        if (
            self.limit_up_price is not None
            and self.limit_down_price is not None
            and self.limit_down_price >= self.limit_up_price
        ):
            raise ValueError("limit_down_price must be below limit_up_price")
        if self.trading_date is None:
            object.__setattr__(self, "trading_date", self.occurred_at.date())
        return self

    @property
    def timestamp(self) -> datetime:
        return self.occurred_at

    @property
    def is_suspended(self) -> bool:
        return self.suspended


class ExecutionState(FrozenModel):
    account: Account
    orders: Mapping[str, Order] = Field(default_factory=dict)
    fills: tuple[Fill, ...] = ()
    session_date: date | None = None
    event_digests: Mapping[str, str] = Field(default_factory=dict)
    processed_market_events: Mapping[str, str] = Field(default_factory=dict)

    @field_validator("orders", mode="after")
    @classmethod
    def freeze_orders(cls, value: Mapping[str, Order]) -> Mapping[str, Order]:
        return MappingProxyType(dict(value))

    @field_validator("event_digests", "processed_market_events", mode="after")
    @classmethod
    def freeze_string_map(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        return MappingProxyType(dict(value))

    @model_validator(mode="after")
    def validate_ownership(self) -> Self:
        for order_id, order in self.orders.items():
            if order_id != order.order_id:
                raise ValueError("order key must match order_id")
            if order.account_id != self.account.account_id:
                raise ValueError("order account_id must match state account")
        return self

    @field_serializer("orders")
    def serialize_orders(self, value: Mapping[str, Order]) -> dict[str, Order]:
        return dict(value)

    @field_serializer("event_digests", "processed_market_events")
    def serialize_string_map(self, value: Mapping[str, str]) -> dict[str, str]:
        return dict(value)

    @property
    def applied_event_ids(self) -> frozenset[str]:
        return frozenset(self.event_digests)


SettlementDays = Literal[0, 1]
