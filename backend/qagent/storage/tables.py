from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
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


class UniverseRow(Base):
    __tablename__ = "universes"

    universe_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    market_scope: Mapped[str] = mapped_column(String(16), default="mixed")
    tags: Mapped[str] = mapped_column(Text, default="")
    symbols: Mapped[str] = mapped_column(Text, default="[]")
    source: Mapped[str] = mapped_column(String(32), default="custom")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class TradableInstrumentRow(Base):
    __tablename__ = "tradable_instruments"

    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    label: Mapped[str] = mapped_column(String(160))
    asset_type: Mapped[str] = mapped_column(String(32), index=True)
    exchange: Mapped[str] = mapped_column(String(16), index=True)
    source: Mapped[str] = mapped_column(String(96), default="")
    tags: Mapped[str] = mapped_column(Text, default="")
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class MarketBarCacheRow(Base):
    __tablename__ = "market_bar_cache"

    provider_mode: Mapped[str] = mapped_column(String(32), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    source_provider: Mapped[str] = mapped_column(String(64), default="")
    open: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    high: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    low: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    close: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    volume: Mapped[Decimal] = mapped_column(Numeric(24, 4))
    cached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class MarketDataCacheSpanRow(Base):
    __tablename__ = "market_data_cache_spans"

    provider_mode: Mapped[str] = mapped_column(String(32), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    start_date: Mapped[date] = mapped_column(Date, primary_key=True)
    end_date: Mapped[date] = mapped_column(Date, primary_key=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    cached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AShareEnhancedCacheRow(Base):
    __tablename__ = "a_share_enhanced_cache"

    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    payload_json: Mapped[str] = mapped_column(Text)
    cached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
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


class ScanResultCacheRow(Base):
    __tablename__ = "scan_result_cache"

    cache_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(160), index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    mode: Mapped[str] = mapped_column(String(32), index=True)
    symbols: Mapped[str] = mapped_column(Text, default="[]")
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class FullMarketScanJobRow(Base):
    __tablename__ = "full_market_scan_jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="queued")
    batch_size: Mapped[int] = mapped_column(Integer, default=200)
    total_symbols: Mapped[int] = mapped_column(Integer, default=0)
    scanned_symbols: Mapped[int] = mapped_column(Integer, default=0)
    total_batches: Mapped[int] = mapped_column(Integer, default=0)
    completed_batches: Mapped[int] = mapped_column(Integer, default=0)
    cards: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    include_etfs: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_if_empty: Mapped[bool] = mapped_column(Boolean, default=True)
    symbols: Mapped[str] = mapped_column(Text, default="[]")
    message: Mapped[str] = mapped_column(Text, default="")
    data_health: Mapped[str] = mapped_column(Text, default="{}")
    result_cache_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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


class DeliveryOutboxRow(Base):
    __tablename__ = "delivery_outbox"

    delivery_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    brief_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("brief_runs.brief_id"), index=True, nullable=True
    )
    channel: Mapped[str] = mapped_column(String(32), index=True)
    recipient: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str] = mapped_column(Text)
    markdown: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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


class PaperTradeRow(Base):
    __tablename__ = "paper_trades"

    trade_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    source_snapshot_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    instrument_id: Mapped[str] = mapped_column(String(32), index=True)
    strategy_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    signal_date: Mapped[date] = mapped_column(Date)
    trigger_price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    initial_stop: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    target_1: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    rank_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    entry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    entry_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    exit_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    latest_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    latest_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    unrealized_return_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    realized_return_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    holding_days: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
