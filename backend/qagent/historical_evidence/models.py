from datetime import date

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
