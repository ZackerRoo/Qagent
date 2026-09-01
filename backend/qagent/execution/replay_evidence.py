from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import Field, model_validator

from qagent.execution.models import (
    AShareExecutionRules,
    FrozenModel,
    MarketEvent,
    Order,
    OrderSide,
    OrderType,
    TimeInForce,
)
from qagent.execution.rules import is_tick_aligned


PAPER_REPLAY_EVIDENCE_SCHEMA_VERSION = "paper-replay-evidence-v1"
PAPER_REPLAY_EVIDENCE_NOTE_PREFIX = "[paper_replay_evidence:v1]"


class PaperReplayOrderContract(FrozenModel):
    """Privacy-safe order fields that were actually used by the matcher."""

    instrument_id: str = Field(min_length=1)
    side: OrderSide
    order_type: OrderType
    quantity: int = Field(gt=0)
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    estimated_price: Decimal | None = Field(default=None, gt=0)
    time_in_force: TimeInForce
    submitted_at: datetime

    @classmethod
    def from_order(cls, order: Order) -> Self:
        return cls(
            instrument_id=order.instrument_id,
            side=order.side,
            order_type=order.order_type,
            quantity=order.quantity,
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            estimated_price=order.estimated_price,
            time_in_force=order.time_in_force,
            submitted_at=order.submitted_at,
        )


class PaperReplayExpectedFill(FrozenModel):
    market_event_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    side: OrderSide
    trade_date: date
    base_price: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    quantity: int = Field(gt=0)
    gross_amount: Decimal = Field(gt=0)
    commission: Decimal = Field(default=Decimal("0"), ge=0)
    stamp_duty: Decimal = Field(default=Decimal("0"), ge=0)
    transfer_fee: Decimal = Field(default=Decimal("0"), ge=0)
    slippage: Decimal = Field(default=Decimal("0"), ge=0)
    cash_flow: Decimal

    @model_validator(mode="after")
    def validate_cash_contract(self) -> Self:
        if self.gross_amount != self.price * self.quantity:
            raise ValueError("gross_amount must equal price times quantity")
        fees = self.commission + self.stamp_duty + self.transfer_fee
        expected = (
            -(self.gross_amount + fees) if self.side == OrderSide.BUY else self.gross_amount - fees
        )
        if self.cash_flow != expected:
            raise ValueError("cash_flow must match side, gross amount, and fees")
        return self


class PaperReplayEvidence(FrozenModel):
    schema_version: str = PAPER_REPLAY_EVIDENCE_SCHEMA_VERSION
    phase: Literal["entry", "exit"]
    market: MarketEvent
    order: PaperReplayOrderContract
    rules: AShareExecutionRules
    expected_fill: PaperReplayExpectedFill
    expected_fill_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def create(
        cls,
        *,
        phase: Literal["entry", "exit"],
        market: MarketEvent,
        order: Order,
        rules: AShareExecutionRules,
        expected_fill: PaperReplayExpectedFill,
    ) -> Self:
        order_contract = PaperReplayOrderContract.from_order(order)
        fill_digest = stable_replay_digest(expected_fill)
        payload = {
            "schema_version": PAPER_REPLAY_EVIDENCE_SCHEMA_VERSION,
            "phase": phase,
            "market": market,
            "order": order_contract,
            "rules": rules,
            "expected_fill": expected_fill,
            "expected_fill_digest": fill_digest,
        }
        return cls(
            **payload,
            evidence_digest=stable_replay_digest(payload),
        )

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        if self.schema_version != PAPER_REPLAY_EVIDENCE_SCHEMA_VERSION:
            raise ValueError("unsupported replay evidence schema")
        expected_side = OrderSide.BUY if self.phase == "entry" else OrderSide.SELL
        if self.order.side != expected_side or self.expected_fill.side != expected_side:
            raise ValueError("phase, order side, and expected fill side must agree")
        instrument_ids = {
            self.market.instrument_id,
            self.order.instrument_id,
            self.expected_fill.instrument_id,
        }
        if len(instrument_ids) != 1:
            raise ValueError("market, order, and expected fill instrument must agree")
        if self.expected_fill.market_event_id != self.market.event_id:
            raise ValueError("expected fill must reference the exact market event")
        if self.expected_fill.trade_date != self.market.trading_date:
            raise ValueError("expected fill date must match the market event")
        if self.order.submitted_at != self.market.occurred_at:
            raise ValueError("order and market timestamps must preserve the matched snapshot")
        if self.expected_fill.quantity > self.order.quantity:
            raise ValueError("expected fill quantity must not exceed the order contract")
        minimum = self.rules.effective_minimum_order_quantity
        step = self.rules.effective_quantity_step
        quantity = self.expected_fill.quantity
        if quantity < minimum or (quantity - minimum) % step:
            raise ValueError("expected fill quantity must respect the frozen rules")
        if not is_tick_aligned(self.expected_fill.price, self.rules.tick_size):
            raise ValueError("expected fill price must respect the frozen rules")
        if self.expected_fill_digest != stable_replay_digest(self.expected_fill):
            raise ValueError("expected fill digest mismatch")
        payload = self.model_dump(mode="json", exclude={"evidence_digest"})
        if self.evidence_digest != stable_replay_digest(payload):
            raise ValueError("replay evidence digest mismatch")
        return self


def stable_replay_digest(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    else:
        value = _json_value(value)
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_value(value: object) -> object:
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value
