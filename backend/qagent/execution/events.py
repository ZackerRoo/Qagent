from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Union

from pydantic import Field, TypeAdapter

from qagent.execution.models import Fill, FrozenModel, Order


class EventKind(StrEnum):
    ORDER_CREATED = "order_created"
    ORDER_ACTIVATED = "order_activated"
    ORDER_REJECTED = "order_rejected"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_EXPIRED = "order_expired"
    FILL_RECORDED = "fill_recorded"
    SESSION_ADVANCED = "session_advanced"
    MARKET_EVENT_PROCESSED = "market_event_processed"


class ExecutionEventBase(FrozenModel):
    event_id: str = Field(min_length=1)
    occurred_at: datetime


class OrderCreatedEvent(ExecutionEventBase):
    kind: Literal[EventKind.ORDER_CREATED] = EventKind.ORDER_CREATED
    order: Order


class OrderActivatedEvent(ExecutionEventBase):
    kind: Literal[EventKind.ORDER_ACTIVATED] = EventKind.ORDER_ACTIVATED
    order_id: str = Field(min_length=1)
    reserved_cash: Decimal = Field(default=Decimal("0"), ge=0)


class OrderRejectedEvent(ExecutionEventBase):
    kind: Literal[EventKind.ORDER_REJECTED] = EventKind.ORDER_REJECTED
    order_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class OrderCancelledEvent(ExecutionEventBase):
    kind: Literal[EventKind.ORDER_CANCELLED] = EventKind.ORDER_CANCELLED
    order_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class OrderExpiredEvent(ExecutionEventBase):
    kind: Literal[EventKind.ORDER_EXPIRED] = EventKind.ORDER_EXPIRED
    order_id: str = Field(min_length=1)
    reason: str = Field(default="expired", min_length=1)


class FillRecordedEvent(ExecutionEventBase):
    kind: Literal[EventKind.FILL_RECORDED] = EventKind.FILL_RECORDED
    fill: Fill


class SessionAdvancedEvent(ExecutionEventBase):
    kind: Literal[EventKind.SESSION_ADVANCED] = EventKind.SESSION_ADVANCED
    trading_date: date


class MarketEventProcessedEvent(ExecutionEventBase):
    kind: Literal[EventKind.MARKET_EVENT_PROCESSED] = EventKind.MARKET_EVENT_PROCESSED
    market_event_id: str = Field(min_length=1)
    market_digest: str = Field(min_length=1)


ExecutionEvent = Annotated[
    Union[
        OrderCreatedEvent,
        OrderActivatedEvent,
        OrderRejectedEvent,
        OrderCancelledEvent,
        OrderExpiredEvent,
        FillRecordedEvent,
        SessionAdvancedEvent,
        MarketEventProcessedEvent,
    ],
    Field(discriminator="kind"),
]
EXECUTION_EVENT_ADAPTER = TypeAdapter(ExecutionEvent)


def canonical_digest(value: FrozenModel) -> str:
    payload = value.model_dump(mode="json", by_alias=False)
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stable_id(prefix: str, *parts: object) -> str:
    encoded = "\x1f".join(str(part) for part in parts).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:24]
    return f"{prefix}_{digest}"
