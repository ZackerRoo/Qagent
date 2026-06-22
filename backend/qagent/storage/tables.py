from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text
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


class ScanRunRow(Base):
    __tablename__ = "scan_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    mode: Mapped[str] = mapped_column(String(32))
    symbols: Mapped[str] = mapped_column(Text, default="")
    scanned: Mapped[int] = mapped_column(Integer, default=0)
    cards: Mapped[int] = mapped_column(Integer, default=0)
    data_health: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class BriefRunRow(Base):
    __tablename__ = "brief_runs"

    brief_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    symbols: Mapped[str] = mapped_column(Text, default="")
    headline: Mapped[str] = mapped_column(Text)
    opportunity_count: Mapped[int] = mapped_column(Integer, default=0)
    entry_watch_count: Mapped[int] = mapped_column(Integer, default=0)
    risk_alert_count: Mapped[int] = mapped_column(Integer, default=0)
    catalyst_count: Mapped[int] = mapped_column(Integer, default=0)
    validation_count: Mapped[int] = mapped_column(Integer, default=0)
    data_health: Mapped[str] = mapped_column(Text, default="{}")
    brief_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class OpportunitySnapshotRow(Base):
    __tablename__ = "opportunity_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("scan_runs.run_id"), index=True
    )
    card_id: Mapped[str] = mapped_column(String(128), index=True)
    instrument_id: Mapped[str] = mapped_column(String(32), index=True)
    market: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(32))
    signal_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    latest_close: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    primary_strategy_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    score: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    strategy_score: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    rank_score: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    trigger_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    initial_stop: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    target_1: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    card_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
