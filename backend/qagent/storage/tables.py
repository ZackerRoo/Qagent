from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Date, DateTime, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from qagent.db import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WatchlistItemRow(Base):
    __tablename__ = "watchlist_items"

    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    thesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="watch")
    tags: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class PositionRow(Base):
    __tablename__ = "positions"

    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    shares: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    entry_date: Mapped[date] = mapped_column(Date)
    strategy_tag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    initial_stop: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    target_1: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    target_2: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    thesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AlertRuleRow(Base):
    __tablename__ = "alert_rules"

    rule_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    operator: Mapped[str] = mapped_column(String(4))
    threshold: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
