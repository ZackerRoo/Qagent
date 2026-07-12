from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from qagent.db import Base, SQLiteScaledDecimal, UTCDateTime


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
    turnover: Mapped[Decimal | None] = mapped_column(Numeric(28, 4), nullable=True)
    adjusted_open: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    adjusted_high: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    adjusted_low: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    adjusted_close: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    adjustment_factor: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)
    adjustment_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
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


class FundamentalSnapshotRow(Base):
    __tablename__ = "fundamental_snapshots"

    provider_mode: Mapped[str] = mapped_column(String(32), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    source_provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    revenue_growth_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    earnings_growth_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    gross_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    operating_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    net_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    return_on_equity_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    market_cap: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    pe_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    forward_pe: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    peg_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    price_to_sales: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    dataset_revision: Mapped[int] = mapped_column(Integer, primary_key=True, default=0, index=True)
    cached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class HistoricalTradabilityRow(Base):
    __tablename__ = "historical_tradability"

    provider_mode: Mapped[str] = mapped_column(String(32), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    trading_status: Mapped[str] = mapped_column(String(32), index=True)
    is_st: Mapped[bool | None] = mapped_column(Boolean, nullable=True, index=True)
    pct_change_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    source_provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_revision: Mapped[int] = mapped_column(Integer, primary_key=True, default=0, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HistoricalInstrumentProfileRow(Base):
    __tablename__ = "historical_instrument_profiles"

    provider_mode: Mapped[str] = mapped_column(String(32), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    listing_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    delisting_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    security_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    listing_status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    source_provider: Mapped[str] = mapped_column(String(64))
    dataset_revision: Mapped[int] = mapped_column(Integer, primary_key=True, default=0, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HistoricalIndustrySnapshotRow(Base):
    __tablename__ = "historical_industry_snapshots"

    provider_mode: Mapped[str] = mapped_column(String(32), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    source_provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    industry: Mapped[str] = mapped_column(String(128), index=True)
    classification: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dataset_revision: Mapped[int] = mapped_column(Integer, primary_key=True, default=0, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HistoricalIndexSnapshotRow(Base):
    __tablename__ = "historical_index_snapshots"

    provider_mode: Mapped[str] = mapped_column(String(32), primary_key=True)
    index_id: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    source_provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dataset_revision: Mapped[int] = mapped_column(Integer, primary_key=True, default=0, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HistoricalIndexMembershipRow(Base):
    __tablename__ = "historical_index_memberships"

    provider_mode: Mapped[str] = mapped_column(String(32), primary_key=True)
    index_id: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    source_provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_revision: Mapped[int] = mapped_column(Integer, primary_key=True, default=0, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HistoricalReplayBarRow(Base):
    __tablename__ = "historical_replay_bars"

    provider_mode: Mapped[str] = mapped_column(String(32), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    raw_open: Mapped[Decimal] = mapped_column(SQLiteScaledDecimal(20, 8))
    raw_high: Mapped[Decimal] = mapped_column(SQLiteScaledDecimal(20, 8))
    raw_low: Mapped[Decimal] = mapped_column(SQLiteScaledDecimal(20, 8))
    raw_close: Mapped[Decimal] = mapped_column(SQLiteScaledDecimal(20, 8))
    adjusted_open: Mapped[Decimal | None] = mapped_column(SQLiteScaledDecimal(20, 8), nullable=True)
    adjusted_high: Mapped[Decimal | None] = mapped_column(SQLiteScaledDecimal(20, 8), nullable=True)
    adjusted_low: Mapped[Decimal | None] = mapped_column(SQLiteScaledDecimal(20, 8), nullable=True)
    adjusted_close: Mapped[Decimal | None] = mapped_column(
        SQLiteScaledDecimal(20, 8), nullable=True
    )
    volume: Mapped[Decimal] = mapped_column(SQLiteScaledDecimal(28, 4))
    turnover: Mapped[Decimal | None] = mapped_column(SQLiteScaledDecimal(28, 4), nullable=True)
    adjustment_factor: Mapped[Decimal | None] = mapped_column(
        SQLiteScaledDecimal(24, 12), nullable=True
    )
    adjustment_mode: Mapped[str] = mapped_column(String(32))
    source_provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_revision: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HistoricalCorporateActionRow(Base):
    __tablename__ = "historical_corporate_actions"
    __table_args__ = (
        CheckConstraint(
            "action_type IN "
            "('cash_dividend', 'split', 'bonus', 'rights', 'merger', 'conversion', 'other')",
            name="ck_historical_corporate_actions_type",
        ),
        CheckConstraint(
            "announcement_date IS NOT NULL "
            "AND (ex_date IS NOT NULL OR effective_date IS NOT NULL OR payable_date IS NOT NULL) "
            "AND ((action_type = 'cash_dividend' "
            "AND record_date IS NOT NULL AND payable_date IS NOT NULL "
            "AND cash_per_share IS NOT NULL AND CAST(cash_per_share AS NUMERIC) > 0) "
            "OR (action_type IN ('split', 'bonus') "
            "AND record_date IS NOT NULL AND ex_date IS NOT NULL "
            "AND effective_date IS NOT NULL "
            "AND share_ratio IS NOT NULL AND CAST(share_ratio AS NUMERIC) > 0) "
            "OR action_type IN ('rights', 'merger', 'conversion', 'other'))",
            name="ck_historical_corporate_actions_evidence",
        ),
    )

    provider_mode: Mapped[str] = mapped_column(String(32), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    action_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    announcement_date: Mapped[date] = mapped_column(Date)
    record_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    ex_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    payable_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    action_type: Mapped[str] = mapped_column(String(64), index=True)
    cash_per_share: Mapped[Decimal | None] = mapped_column(
        SQLiteScaledDecimal(20, 8), nullable=True
    )
    share_ratio: Mapped[Decimal | None] = mapped_column(SQLiteScaledDecimal(24, 12), nullable=True)
    rights_ratio: Mapped[Decimal | None] = mapped_column(SQLiteScaledDecimal(24, 12), nullable=True)
    subscription_price: Mapped[Decimal | None] = mapped_column(
        SQLiteScaledDecimal(20, 8), nullable=True
    )
    previous_raw_close: Mapped[Decimal | None] = mapped_column(
        SQLiteScaledDecimal(20, 8), nullable=True
    )
    ex_right_reference_price: Mapped[Decimal | None] = mapped_column(
        SQLiteScaledDecimal(20, 8), nullable=True
    )
    source_provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_revision: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HistoricalUniverseManifestRow(Base):
    __tablename__ = "historical_universe_manifests"

    provider_mode: Mapped[str] = mapped_column(String(32), primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    source_revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_run_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    expected_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stored_count: Mapped[int] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HistoricalReplayUniverseMemberRow(Base):
    __tablename__ = "historical_replay_universe_members"

    provider_mode: Mapped[str] = mapped_column(String(32), primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    source_revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    owner_run_id: Mapped[str] = mapped_column(String(64), index=True)
    security_type: Mapped[str] = mapped_column(String(32), index=True)
    listing_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    delisting_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean)
    source_provider: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HistoricalLifecycleManifestRow(Base):
    __tablename__ = "historical_lifecycle_manifests"

    provider_mode: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    expected_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stored_count: Mapped[int] = mapped_column(Integer)
    effective_through: Mapped[date] = mapped_column(Date)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HistoricalCorporateActionCoverageRow(Base):
    __tablename__ = "historical_corporate_action_coverage"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ready', 'ready_none', 'partial', 'unsupported')",
            name="ck_historical_corporate_action_coverage_status",
        ),
        CheckConstraint(
            "action_count >= 0 "
            "AND (status != 'ready' OR action_count > 0) "
            "AND (status != 'ready_none' OR action_count = 0)",
            name="ck_historical_corporate_action_coverage_count",
        ),
    )

    provider_mode: Mapped[str] = mapped_column(String(32), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    start_date: Mapped[date] = mapped_column(Date, primary_key=True)
    end_date: Mapped[date] = mapped_column(Date, primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    action_count: Mapped[int] = mapped_column(Integer)
    source_provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_revision: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HistoricalTradingRuleRow(Base):
    __tablename__ = "historical_trading_rules"

    rule_set_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    limit_rule_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    market: Mapped[str] = mapped_column(String(16))
    board: Mapped[str] = mapped_column(String(32))
    is_st: Mapped[bool] = mapped_column(Boolean)
    security_type: Mapped[str] = mapped_column(String(32))
    effective_from: Mapped[date] = mapped_column(Date, primary_key=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    limit_pct: Mapped[Decimal | None] = mapped_column(SQLiteScaledDecimal(18, 8), nullable=True)
    tick_size: Mapped[Decimal] = mapped_column(SQLiteScaledDecimal(18, 8))
    board_lot: Mapped[int] = mapped_column(Integer)
    minimum_order_quantity: Mapped[int] = mapped_column(Integer, default=100)
    quantity_step: Mapped[int] = mapped_column(Integer, default=100)
    settlement_days: Mapped[int] = mapped_column(Integer)
    ipo_no_limit_sessions: Mapped[int] = mapped_column(Integer)


class HistoricalInstrumentRuleMetadataRow(Base):
    __tablename__ = "historical_instrument_rule_metadata"

    provider_mode: Mapped[str] = mapped_column(String(32), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    effective_from: Mapped[date] = mapped_column(Date, primary_key=True)
    rule_set_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    fee_schedule_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    security_type: Mapped[str] = mapped_column(String(32), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    board: Mapped[str] = mapped_column(String(32))
    settlement_days: Mapped[int] = mapped_column(Integer)
    board_lot: Mapped[int] = mapped_column(Integer, default=100)
    minimum_order_quantity: Mapped[int] = mapped_column(Integer, default=100)
    quantity_step: Mapped[int] = mapped_column(Integer, default=100)
    limit_rule_key: Mapped[str] = mapped_column(String(128), index=True)
    fee_rule_key: Mapped[str] = mapped_column(String(128), index=True)
    source_provider: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HistoricalFeeRuleRow(Base):
    __tablename__ = "historical_fee_rules"

    fee_schedule_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    fee_rule_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    effective_from: Mapped[date] = mapped_column(Date, primary_key=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    side: Mapped[str] = mapped_column(String(16), primary_key=True)
    security_type: Mapped[str] = mapped_column(String(32))
    exchange: Mapped[str] = mapped_column(String(16))
    commission_bps: Mapped[Decimal] = mapped_column(SQLiteScaledDecimal(18, 8))
    minimum_commission: Mapped[Decimal] = mapped_column(SQLiteScaledDecimal(20, 8))
    stamp_duty_bps: Mapped[Decimal] = mapped_column(SQLiteScaledDecimal(18, 8))
    transfer_fee_bps: Mapped[Decimal] = mapped_column(SQLiteScaledDecimal(18, 8))


class HistoricalTerminalSettlementRow(Base):
    __tablename__ = "historical_terminal_settlements"
    __table_args__ = (
        CheckConstraint(
            "settlement_type IN ('cash', 'conversion')",
            name="ck_historical_terminal_settlements_type",
        ),
        CheckConstraint(
            "(settlement_type = 'cash' "
            "AND cash_per_share IS NOT NULL AND CAST(cash_per_share AS NUMERIC) > 0) "
            "OR (settlement_type = 'conversion' "
            "AND conversion_instrument_id IS NOT NULL "
            "AND length(trim(conversion_instrument_id)) > 0 "
            "AND conversion_ratio IS NOT NULL "
            "AND CAST(conversion_ratio AS NUMERIC) > 0)",
            name="ck_historical_terminal_settlements_evidence",
        ),
    )

    provider_mode: Mapped[str] = mapped_column(String(32), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    effective_date: Mapped[date] = mapped_column(Date, primary_key=True)
    settlement_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    cash_per_share: Mapped[Decimal | None] = mapped_column(
        SQLiteScaledDecimal(20, 8), nullable=True
    )
    conversion_instrument_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    conversion_ratio: Mapped[Decimal | None] = mapped_column(
        SQLiteScaledDecimal(24, 12), nullable=True
    )
    source_provider: Mapped[str] = mapped_column(String(64))
    dataset_revision: Mapped[int] = mapped_column(Integer, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HistoricalDataRevisionRow(Base):
    __tablename__ = "historical_data_revisions"

    provider_mode: Mapped[str] = mapped_column(String(32), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)


class HistoricalDatasetLeaseRow(Base):
    __tablename__ = "historical_dataset_leases"

    provider_mode: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_run_id: Mapped[str] = mapped_column(String(64), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    lease_expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    heartbeat_at: Mapped[datetime] = mapped_column(UTCDateTime())


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


class HistoricalBackfillJobRow(Base):
    __tablename__ = "historical_backfill_jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="queued")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    symbols: Mapped[str] = mapped_column(Text, default="[]")
    total_symbols: Mapped[int] = mapped_column(Integer, default=0)
    processed_symbols: Mapped[int] = mapped_column(Integer, default=0)
    succeeded_symbols: Mapped[int] = mapped_column(Integer, default=0)
    failed_symbols: Mapped[int] = mapped_column(Integer, default=0)
    rows_written: Mapped[int] = mapped_column(Integer, default=0)
    fundamental_rows_written: Mapped[int] = mapped_column(Integer, default=0)
    current_instrument: Mapped[str | None] = mapped_column(String(32), nullable=True)
    errors_json: Mapped[str] = mapped_column(Text, default="[]")
    data_health: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WalkForwardRunRow(Base):
    __tablename__ = "walk_forward_runs"

    run_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="succeeded")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    dataset_revision: Mapped[int] = mapped_column(Integer)
    rebalance_step_sessions: Mapped[int] = mapped_column(Integer)
    lookback_days: Mapped[int] = mapped_column(Integer)
    snapshot_count: Mapped[int] = mapped_column(Integer, default=0)
    top_5_trade_count: Mapped[int] = mapped_column(Integer, default=0)
    top_10_trade_count: Mapped[int] = mapped_column(Integer, default=0)
    top_5_return_pct: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    top_10_return_pct: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    top_5_oos_trades: Mapped[int] = mapped_column(Integer, default=0)
    top_10_oos_trades: Mapped[int] = mapped_column(Integer, default=0)
    top_5_oos_gate: Mapped[str] = mapped_column(String(32), default="insufficient")
    top_10_oos_gate: Mapped[str] = mapped_column(String(32), default="insufficient")
    reproducibility_digest: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    data_health: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class TradableUniverseSnapshotRow(Base):
    __tablename__ = "tradable_universe_snapshots"

    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    symbol: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(128))
    asset_type: Mapped[str] = mapped_column(String(32), index=True)
    exchange: Mapped[str] = mapped_column(String(16), index=True)
    source: Mapped[str] = mapped_column(String(96), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AutomationSchedulerStateRow(Base):
    __tablename__ = "automation_scheduler_state"

    state_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    settings_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


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
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("scan_runs.run_id"), index=True)
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


class PaperAccountSettingsRow(Base):
    __tablename__ = "paper_account_settings"

    account_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(96), index=True)
    label: Mapped[str] = mapped_column(String(128), default="正式模拟盘")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    initial_capital: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    allocation_per_trade_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    max_positions: Mapped[int] = mapped_column(Integer, default=5)
    transaction_cost_bps: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    slippage_bps: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    take_profit_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
