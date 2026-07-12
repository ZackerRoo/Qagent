import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


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


CANONICAL_HISTORICAL_SECURITY_TYPES = frozenset({"stock", "etf"})
CANONICAL_HISTORICAL_LISTING_STATUSES = frozenset({"active", "delisted"})
_SECURITY_TYPE_ALIASES = {
    "1": "stock",
    "5": "etf",
    "stock": "stock",
    "etf": "etf",
}
_LISTING_STATUS_ALIASES = {
    "0": "delisted",
    "1": "active",
    "active": "active",
    "listed": "active",
    "delisted": "delisted",
}
_STOCK_SYMBOL_PREFIXES = (
    "000",
    "001",
    "002",
    "003",
    "300",
    "301",
    "600",
    "601",
    "603",
    "605",
    "688",
    "4",
    "8",
    "920",
)
_ETF_SYMBOL_PREFIXES = ("15", "16", "51", "52", "56", "58")


def normalize_historical_security_type(value: str | None) -> str | None:
    if value is None:
        return None
    return _SECURITY_TYPE_ALIASES.get(value.strip().lower())


def normalize_historical_listing_status(value: str | None) -> str | None:
    if value is None:
        return None
    return _LISTING_STATUS_ALIASES.get(value.strip().lower())


def normalize_and_validate_historical_profile(
    profile: HistoricalInstrumentProfile,
    effective_through: date,
) -> tuple[HistoricalInstrumentProfile, list[str]]:
    security_type = normalize_historical_security_type(profile.security_type)
    listing_status = normalize_historical_listing_status(profile.listing_status)
    normalized = profile.model_copy(
        update={
            "security_type": security_type,
            "listing_status": listing_status,
        }
    )
    errors: list[str] = []
    instrument_id = profile.instrument_id.strip()
    match = re.fullmatch(r"CN:(\d{6})", instrument_id)
    if match is None:
        errors.append(f"{profile.instrument_id}: instrument_id is not canonical CN:######")
    if security_type not in CANONICAL_HISTORICAL_SECURITY_TYPES:
        errors.append(
            f"{profile.instrument_id}: security_type is not canonical stock/etf"
        )
    elif match is not None:
        symbol = match.group(1)
        prefixes = (
            _STOCK_SYMBOL_PREFIXES
            if security_type == "stock"
            else _ETF_SYMBOL_PREFIXES
        )
        if not symbol.startswith(prefixes):
            errors.append(
                f"{profile.instrument_id}: security_type {security_type} "
                "does not match instrument_id"
            )
    if listing_status not in CANONICAL_HISTORICAL_LISTING_STATUSES:
        errors.append(
            f"{profile.instrument_id}: listing_status is not canonical active/delisted"
        )
    if profile.listing_date is None:
        errors.append(f"{profile.instrument_id}: listing_date is missing")
    elif profile.listing_date > effective_through:
        errors.append(
            f"{profile.instrument_id}: listing_date is after effective_through"
        )
    if listing_status == "delisted" and profile.delisting_date is None:
        errors.append(f"{profile.instrument_id}: delisting_date is required for delisted")
    if (
        profile.listing_date is not None
        and profile.delisting_date is not None
        and profile.delisting_date < profile.listing_date
    ):
        errors.append(f"{profile.instrument_id}: delisting_date is before listing_date")
    if (
        listing_status == "delisted"
        and profile.delisting_date is not None
        and profile.delisting_date > effective_through
    ):
        errors.append(
            f"{profile.instrument_id}: delisting_date is after effective_through"
        )
    if not profile.provider.strip():
        errors.append(f"{profile.instrument_id}: source provider is missing")
    return normalized, errors


class HistoricalInventoryManifest(BaseModel):
    status: Literal["ready", "partial"]
    expected_count: int | None = None
    effective_through: date
    error: str | None = None
    fetched_at: datetime
    source_provider: str

    @model_validator(mode="after")
    def validate_ready_inventory(self) -> Self:
        if self.status == "ready" and (
            self.expected_count is None
            or self.expected_count <= 0
            or self.error is not None
        ):
            raise ValueError(
                "ready inventory requires a positive expected_count and no error"
            )
        return self


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
    announcement_date: date
    record_date: date | None = None
    ex_date: date | None = None
    effective_date: date | None = None
    payable_date: date | None = None
    action_type: Literal[
        "cash_dividend", "split", "bonus", "rights", "merger", "conversion", "other"
    ]
    cash_per_share: Decimal | None = None
    share_ratio: Decimal | None = None
    rights_ratio: Decimal | None = None
    subscription_price: Decimal | None = None
    previous_raw_close: Decimal | None = None
    ex_right_reference_price: Decimal | None = None
    source_provider: str
    dataset_revision: int
    fetched_at: datetime

    @model_validator(mode="after")
    def validate_action_evidence(self) -> Self:
        if not any((self.ex_date, self.effective_date, self.payable_date)):
            raise ValueError("corporate action requires an effective or event date")
        if self.action_type == "cash_dividend":
            if self.record_date is None or self.payable_date is None:
                raise ValueError("cash dividend requires record_date and payable_date")
            if self.cash_per_share is None or self.cash_per_share <= 0:
                raise ValueError("cash dividend requires positive cash_per_share")
        elif self.action_type in {"split", "bonus"}:
            if self.record_date is None or self.ex_date is None or self.effective_date is None:
                raise ValueError("split or bonus requires record, ex, and effective dates")
            if self.share_ratio is None or self.share_ratio <= 0:
                raise ValueError("split or bonus requires positive share_ratio")
        return self


class HistoricalUniverseManifest(BaseModel):
    provider_mode: str
    snapshot_date: date
    source_revision: int
    owner_run_id: str
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
    limit_rule_key: str
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
    rule_set_version: str
    limit_rule_key: str
    fee_schedule_version: str
    fee_rule_key: str
    source_provider: str
    fetched_at: datetime


class HistoricalFeeRule(BaseModel):
    fee_schedule_version: str
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
    settlement_type: Literal["cash", "conversion"]
    cash_per_share: Decimal | None = None
    conversion_instrument_id: str | None = None
    conversion_ratio: Decimal | None = None
    source_provider: str
    dataset_revision: int
    fetched_at: datetime

    @model_validator(mode="after")
    def validate_settlement_evidence(self) -> Self:
        if self.settlement_type == "cash":
            if self.cash_per_share is None or self.cash_per_share <= 0:
                raise ValueError("cash settlement requires positive cash_per_share")
        elif (
            not self.conversion_instrument_id
            or self.conversion_ratio is None
            or self.conversion_ratio <= 0
        ):
            raise ValueError(
                "conversion settlement requires conversion instrument and positive ratio"
            )
        return self


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
