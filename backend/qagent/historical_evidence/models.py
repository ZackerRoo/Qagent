from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class HistoricalTradabilityPoint(BaseModel):
    instrument_id: str
    trade_date: date
    trading_status: str
    is_st: bool | None = None
    pct_change_pct: float | None = None
    provider: str


class HistoricalInstrumentProfile(BaseModel):
    instrument_id: str
    name: str | None = None
    snapshot_date: date
    listing_date: date | None = None
    delisting_date: date | None = None
    security_type: str | None = None
    listing_status: str | None = None
    provider: str


class HistoricalIndustrySnapshot(BaseModel):
    instrument_id: str
    snapshot_date: date
    industry: str
    classification: str | None = None
    provider: str


class HistoricalIndexSnapshot(BaseModel):
    index_id: str
    snapshot_date: date
    status: str
    member_count: int = 0
    provider: str
    error: str | None = None


class HistoricalIndexMembership(BaseModel):
    index_id: str
    snapshot_date: date
    instrument_id: str
    provider: str


class HistoricalReplayBar(BaseModel):
    provider_mode: str
    instrument_id: str
    trade_date: date
    raw_open: Decimal
    raw_high: Decimal
    raw_low: Decimal
    raw_close: Decimal
    adjusted_open: Decimal | None = None
    adjusted_high: Decimal | None = None
    adjusted_low: Decimal | None = None
    adjusted_close: Decimal | None = None
    volume: Decimal
    turnover: Decimal | None = None
    adjustment_factor: Decimal | None = None
    adjustment_mode: str
    source_provider: str
    dataset_revision: int
    fetched_at: datetime


class HistoricalCorporateAction(BaseModel):
    provider_mode: str
    instrument_id: str
    action_id: str
    announcement_date: date | None = None
    record_date: date | None = None
    ex_date: date | None = None
    effective_date: date | None = None
    payable_date: date | None = None
    action_type: str
    cash_per_share: Decimal | None = None
    share_ratio: Decimal | None = None
    rights_ratio: Decimal | None = None
    subscription_price: Decimal | None = None
    previous_raw_close: Decimal | None = None
    ex_right_reference_price: Decimal | None = None
    source_provider: str
    dataset_revision: int
    fetched_at: datetime


class HistoricalUniverseManifest(BaseModel):
    provider_mode: str
    snapshot_date: date
    source_revision: int
    status: str
    expected_count: int | None = None
    stored_count: int
    error: str | None = None
    fetched_at: datetime


class HistoricalLifecycleManifest(BaseModel):
    provider_mode: str
    source_revision: int
    status: str
    expected_count: int | None = None
    stored_count: int
    effective_through: date
    error: str | None = None
    fetched_at: datetime


class HistoricalTradingRule(BaseModel):
    rule_set_version: str
    market: str
    board: str
    is_st: bool
    security_type: str
    effective_from: date
    effective_to: date | None = None
    limit_pct: Decimal | None = None
    tick_size: Decimal
    board_lot: int
    settlement_days: int
    ipo_no_limit_sessions: int


class HistoricalInstrumentRuleMetadata(BaseModel):
    provider_mode: str
    instrument_id: str
    effective_from: date
    effective_to: date | None = None
    security_type: str
    market: str
    board: str
    settlement_days: int
    limit_rule_key: str
    fee_rule_key: str
    source_provider: str
    fetched_at: datetime


class HistoricalFeeRule(BaseModel):
    fee_rule_key: str
    effective_from: date
    effective_to: date | None = None
    side: str
    security_type: str
    exchange: str
    commission_bps: Decimal
    minimum_commission: Decimal
    stamp_duty_bps: Decimal
    transfer_fee_bps: Decimal


class HistoricalTerminalSettlement(BaseModel):
    provider_mode: str
    instrument_id: str
    effective_date: date
    settlement_type: str
    cash_per_share: Decimal | None = None
    conversion_instrument_id: str | None = None
    conversion_ratio: Decimal | None = None
    source_provider: str
    dataset_revision: int
    fetched_at: datetime


class HistoricalEvidenceBundle(BaseModel):
    tradability: list[HistoricalTradabilityPoint] = Field(default_factory=list)
    profiles: list[HistoricalInstrumentProfile] = Field(default_factory=list)
    industries: list[HistoricalIndustrySnapshot] = Field(default_factory=list)
    index_snapshots: list[HistoricalIndexSnapshot] = Field(default_factory=list)
    index_memberships: list[HistoricalIndexMembership] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    data_health: dict[str, str] = Field(default_factory=dict)


class HistoricalInstrumentEvidenceStats(BaseModel):
    tradability_rows: int = 0
    first_tradability_date: date | None = None
    last_tradability_date: date | None = None
    suspended_rows: int = 0
    st_rows: int = 0
    profile_rows: int = 0
    listing_date: date | None = None
    delisting_date: date | None = None
    listing_status: str | None = None
    industry_rows: int = 0
    first_industry_date: date | None = None
    last_industry_date: date | None = None
    industries: list[str] = Field(default_factory=list)
    benchmark_membership_rows: int = 0
    benchmark_ids: list[str] = Field(default_factory=list)


class HistoricalIndexCoverageStats(BaseModel):
    total_snapshots: int = 0
    ready_snapshots: int = 0
    failed_snapshots: int = 0
    first_snapshot_date: date | None = None
    last_snapshot_date: date | None = None
    index_ids: list[str] = Field(default_factory=list)
