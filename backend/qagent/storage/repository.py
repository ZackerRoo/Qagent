from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, aliased, sessionmaker

from qagent.domain.models import OpportunityCard
from qagent.historical_evidence.models import (
    HistoricalEvidenceBundle,
    HistoricalIndexCoverageStats,
    HistoricalInstrumentProfile,
    HistoricalInstrumentEvidenceStats,
    normalize_historical_security_type,
)
from qagent.market.universes import UniverseCreate, UniverseRecord, normalize_symbols
from qagent.storage.replay_evidence import ReplayEvidenceRepository
from qagent.storage.tables import (
    AlertRuleRow,
    AutomationSchedulerStateRow,
    BriefRunRow,
    DeliveryOutboxRow,
    FullMarketScanJobRow,
    FundamentalSnapshotRow,
    HistoricalBackfillJobRow,
    HistoricalIndexMembershipRow,
    HistoricalIndexSnapshotRow,
    HistoricalIndustrySnapshotRow,
    HistoricalInstrumentProfileRow,
    HistoricalTradabilityRow,
    OpportunitySnapshotRow,
    PositionRow,
    ScanResultCacheRow,
    ScanRunRow,
    TradableInstrumentRow,
    TradableUniverseSnapshotRow,
    UniverseRow,
    WatchlistItemRow,
    WalkForwardRunRow,
    WalkForwardJobRow,
)
from qagent.strategy_data.models import FundamentalSnapshot


class WatchlistCreate(BaseModel):
    instrument_id: str
    thesis: str | None = None
    status: str = "watch"
    tags: list[str] = Field(default_factory=list)


class WatchlistItem(BaseModel):
    instrument_id: str
    thesis: str | None
    status: str
    tags: list[str]


class PositionCreate(BaseModel):
    instrument_id: str
    shares: Decimal
    entry_price: Decimal
    entry_date: date
    strategy_tag: str | None = None
    initial_stop: Decimal | None = None
    target_1: Decimal | None = None
    target_2: Decimal | None = None
    thesis: str | None = None


class Position(BaseModel):
    instrument_id: str
    shares: Decimal
    entry_price: Decimal
    entry_date: date
    strategy_tag: str | None
    initial_stop: Decimal | None
    target_1: Decimal | None
    target_2: Decimal | None
    thesis: str | None


class AlertRuleCreate(BaseModel):
    rule_id: str
    instrument_id: str
    kind: str
    operator: str
    threshold: Decimal


class StoredAlertRule(BaseModel):
    rule_id: str
    instrument_id: str
    kind: str
    operator: str
    threshold: Decimal


class ScanRunRecord(BaseModel):
    run_id: str
    provider: str
    mode: str
    symbols: list[str]
    scanned: int
    cards: int
    data_health: dict[str, str]
    created_at: datetime


class ScanResultCacheRecord(BaseModel):
    cache_id: str
    cache_key: str
    provider: str
    mode: str
    symbols: list[str]
    payload: dict[str, object]
    created_at: datetime


class ScanRunSnapshotBundle(BaseModel):
    run: ScanRunRecord
    snapshots: list[OpportunitySnapshotRecord]


class FullMarketScanJobRecord(BaseModel):
    job_id: str
    provider: str
    status: str
    batch_size: int
    total_symbols: int
    scanned_symbols: int
    total_batches: int
    completed_batches: int
    cards: int
    errors: int
    include_etfs: bool
    sync_if_empty: bool
    symbols: list[str]
    message: str
    data_health: dict[str, str]
    result_cache_key: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @property
    def progress(self) -> int:
        if self.total_symbols <= 0:
            return 0
        if self.status == "succeeded":
            return 100
        return max(0, min(99, int(self.scanned_symbols * 100 / self.total_symbols)))


class HistoricalBackfillJobRecord(BaseModel):
    job_id: str
    provider: str
    status: str
    start_date: date
    end_date: date
    symbols: list[str]
    total_symbols: int
    processed_symbols: int
    succeeded_symbols: int
    failed_symbols: int
    rows_written: int
    fundamental_rows_written: int
    current_instrument: str | None
    errors: list[str]
    data_health: dict[str, str]
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @property
    def progress(self) -> int:
        if self.total_symbols <= 0:
            return 0
        return max(0, min(100, int(self.processed_symbols * 100 / self.total_symbols)))


class WalkForwardRunRecord(BaseModel):
    run_id: str
    provider: str
    status: str
    start_date: date
    end_date: date
    dataset_revision: int
    rebalance_step_sessions: int
    lookback_days: int
    snapshot_count: int
    top_5_trade_count: int
    top_10_trade_count: int
    top_5_return_pct: float
    top_10_return_pct: float
    top_5_oos_trades: int
    top_10_oos_trades: int
    top_5_oos_gate: str
    top_10_oos_gate: str
    reproducibility_digest: str
    payload: dict[str, object]
    data_health: dict[str, str]
    created_at: datetime
    updated_at: datetime


class WalkForwardJobRecord(BaseModel):
    job_id: str
    provider: str
    status: str
    phase: str
    start_date: date
    end_date: date
    dataset_revision: int
    rebalance_step_sessions: int
    lookback_days: int
    total_snapshots: int
    processed_snapshots: int
    current_date: date | None
    checkpoints: list[dict[str, object]]
    experiment_manifest: dict[str, object]
    result_run_id: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @property
    def progress(self) -> int:
        if self.status == "succeeded":
            return 100
        if self.status == "failed":
            return max(
                0,
                min(
                    100,
                    int(self.processed_snapshots * 100 / self.total_snapshots),
                ),
            ) if self.total_snapshots else 0
        if self.total_snapshots <= 0:
            return 0
        return max(
            0,
            min(99, int(self.processed_snapshots * 100 / self.total_snapshots)),
        )


class AutomationSchedulerStateRecord(BaseModel):
    enabled: bool
    settings: dict[str, object]
    updated_at: datetime


class OpportunitySnapshotRecord(BaseModel):
    snapshot_id: str
    run_id: str
    card_id: str
    instrument_id: str
    market: str
    status: str
    signal_date: date | None
    latest_close: Decimal | None
    primary_strategy_id: str | None
    score: Decimal
    strategy_score: Decimal
    rank_score: Decimal
    trigger_price: Decimal | None
    initial_stop: Decimal | None
    target_1: Decimal | None
    card: dict[str, object]


class BriefRunRecord(BaseModel):
    brief_id: str
    provider: str
    symbols: list[str]
    headline: str
    opportunity_count: int
    entry_watch_count: int
    risk_alert_count: int
    catalyst_count: int
    validation_count: int
    data_health: dict[str, str]
    payload: dict[str, object]
    created_at: datetime


class DeliveryOutboxRecord(BaseModel):
    delivery_id: str
    brief_id: str | None
    channel: str
    recipient: str | None
    subject: str
    markdown: str
    payload: dict[str, object]
    status: str
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None


class StoredTradableInstrument(BaseModel):
    instrument_id: str
    symbol: str
    name: str
    label: str
    asset_type: str
    exchange: str
    source: str
    tags: list[str] = Field(default_factory=list)
    synced_at: datetime | None = None


class TradableCatalogSummary(BaseModel):
    total_count: int
    stock_count: int
    etf_count: int
    other_count: int
    exchanges: dict[str, int] = Field(default_factory=dict)
    last_synced_at: datetime | None = None


class TradableCatalogSearchResult(BaseModel):
    items: list[StoredTradableInstrument]
    summary: TradableCatalogSummary
    data_health: dict[str, str] = Field(default_factory=dict)


def _serialize_tags(tags: list[str]) -> str:
    return ",".join(tag.strip() for tag in tags if tag.strip())


def _parse_tags(value: str | None) -> list[str]:
    if not value:
        return []
    return [tag for tag in value.split(",") if tag]


class QagentRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def replay_evidence(
        self,
        provider_mode: str,
        *,
        owner_run_id: str | None = None,
        run_status_lookup: Callable[[str], str | None] | None = None,
    ) -> ReplayEvidenceRepository:
        return ReplayEvidenceRepository(
            self.session_factory,
            provider_mode=provider_mode,
            owner_run_id=owner_run_id,
            run_status_lookup=run_status_lookup,
        )

    def save_automation_scheduler_state(
        self,
        *,
        enabled: bool,
        settings: dict[str, object],
    ) -> AutomationSchedulerStateRecord:
        with self.session_factory() as session:
            now = datetime.now(timezone.utc)
            row = session.get(AutomationSchedulerStateRow, "default")
            if row is None:
                row = AutomationSchedulerStateRow(
                    state_id="default",
                    enabled=enabled,
                    settings_json=json.dumps(settings, sort_keys=True),
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.enabled = enabled
                row.settings_json = json.dumps(settings, sort_keys=True)
                row.updated_at = now
            session.commit()
            session.refresh(row)
            return self._automation_scheduler_state_from_row(row)

    def get_automation_scheduler_state(self) -> AutomationSchedulerStateRecord | None:
        with self.session_factory() as session:
            row = session.get(AutomationSchedulerStateRow, "default")
            return self._automation_scheduler_state_from_row(row) if row is not None else None

    def upsert_watchlist_item(self, item: WatchlistCreate) -> WatchlistItem:
        with self.session_factory() as session:
            row = session.get(WatchlistItemRow, item.instrument_id)
            if row is None:
                row = WatchlistItemRow(instrument_id=item.instrument_id)
                session.add(row)
            row.thesis = item.thesis
            row.status = item.status
            row.tags = _serialize_tags(item.tags)
            session.commit()
            session.refresh(row)
            return self._watchlist_from_row(row)

    def list_watchlist_items(self) -> list[WatchlistItem]:
        with self.session_factory() as session:
            rows = session.query(WatchlistItemRow).order_by(WatchlistItemRow.instrument_id).all()
            return [self._watchlist_from_row(row) for row in rows]

    def upsert_position(self, position: PositionCreate) -> Position:
        with self.session_factory() as session:
            row = session.get(PositionRow, position.instrument_id)
            if row is None:
                row = PositionRow(instrument_id=position.instrument_id)
                session.add(row)
            row.shares = position.shares
            row.entry_price = position.entry_price
            row.entry_date = position.entry_date
            row.strategy_tag = position.strategy_tag
            row.initial_stop = position.initial_stop
            row.target_1 = position.target_1
            row.target_2 = position.target_2
            row.thesis = position.thesis
            session.commit()
            session.refresh(row)
            return self._position_from_row(row)

    def list_positions(self) -> list[Position]:
        with self.session_factory() as session:
            rows = session.query(PositionRow).order_by(PositionRow.instrument_id).all()
            return [self._position_from_row(row) for row in rows]

    def upsert_alert_rule(self, rule: AlertRuleCreate) -> StoredAlertRule:
        with self.session_factory() as session:
            row = session.get(AlertRuleRow, rule.rule_id)
            if row is None:
                row = AlertRuleRow(rule_id=rule.rule_id)
                session.add(row)
            row.instrument_id = rule.instrument_id
            row.kind = rule.kind
            row.operator = rule.operator
            row.threshold = rule.threshold
            session.commit()
            session.refresh(row)
            return self._alert_rule_from_row(row)

    def list_alert_rules(self) -> list[StoredAlertRule]:
        with self.session_factory() as session:
            rows = session.query(AlertRuleRow).order_by(AlertRuleRow.rule_id).all()
            return [self._alert_rule_from_row(row) for row in rows]

    def upsert_universe(self, universe: UniverseCreate) -> UniverseRecord:
        with self.session_factory() as session:
            row = session.get(UniverseRow, universe.universe_id)
            if row is None:
                row = UniverseRow(universe_id=universe.universe_id)
                session.add(row)
            row.name = universe.name
            row.description = universe.description
            row.market_scope = universe.market_scope
            row.tags = _serialize_tags(universe.tags)
            row.symbols = json.dumps(normalize_symbols(universe.symbols))
            row.source = "custom"
            session.commit()
            session.refresh(row)
            return self._universe_from_row(row)

    def list_custom_universes(self) -> list[UniverseRecord]:
        with self.session_factory() as session:
            rows = session.query(UniverseRow).order_by(UniverseRow.name).all()
            return [self._universe_from_row(row) for row in rows]

    def get_universe(self, universe_id: str) -> UniverseRecord | None:
        with self.session_factory() as session:
            row = session.get(UniverseRow, universe_id)
            if row is None:
                return None
            return self._universe_from_row(row)

    def replace_tradable_instruments(
        self,
        instruments: list,
        data_health: dict[str, str] | None = None,
    ) -> TradableCatalogSummary:
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            session.query(TradableInstrumentRow).delete()
            for instrument in instruments:
                tags = _instrument_tags(instrument)
                session.add(
                    TradableInstrumentRow(
                        instrument_id=instrument.instrument_id,
                        symbol=instrument.symbol,
                        name=instrument.name,
                        label=instrument.label,
                        asset_type=instrument.asset_type,
                        exchange=instrument.exchange,
                        source=instrument.source,
                        tags=_serialize_tags(tags),
                        synced_at=now,
                    )
                )
            session.commit()
        return self.tradable_catalog_summary()

    def tradable_catalog_summary(self) -> TradableCatalogSummary:
        with self.session_factory() as session:
            rows = session.query(TradableInstrumentRow).all()
            return _tradable_summary(rows)

    def search_tradable_instruments(
        self,
        query: str = "",
        asset_type: str | None = None,
        limit: int = 50,
    ) -> TradableCatalogSearchResult:
        normalized_query = query.strip().upper()
        normalized_asset = asset_type.strip().lower() if asset_type else None
        with self.session_factory() as session:
            rows = session.query(TradableInstrumentRow).all()
        filtered = []
        for row in rows:
            if normalized_asset and row.asset_type.lower() != normalized_asset:
                continue
            if normalized_query and not _matches_tradable_row(row, normalized_query):
                continue
            filtered.append(row)
        if normalized_query:
            filtered.sort(key=lambda row: _tradable_match_rank(row, normalized_query))
        else:
            filtered.sort(key=lambda row: (_asset_browse_rank(row.asset_type), row.symbol))
        capped = filtered[: max(limit, 0)]
        return TradableCatalogSearchResult(
            items=[self._tradable_instrument_from_row(row) for row in capped],
            summary=_tradable_summary(rows),
            data_health={
                "tradable_catalog": "sqlite",
                "tradable_matched": str(len(filtered)),
                "tradable_returned": str(len(capped)),
            },
        )

    def list_tradable_instruments(
        self,
        asset_types: set[str] | None = None,
        limit: int = 500,
    ) -> list[StoredTradableInstrument]:
        normalized_types = {item.lower() for item in asset_types or set()}
        with self.session_factory() as session:
            rows = session.query(TradableInstrumentRow).all()
        if normalized_types:
            rows = [row for row in rows if row.asset_type.lower() in normalized_types]
        rows.sort(key=lambda row: (_asset_browse_rank(row.asset_type), row.symbol))
        return [self._tradable_instrument_from_row(row) for row in rows[: max(limit, 0)]]

    def capture_tradable_universe_snapshot(self, as_of_date: date) -> int:
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            instruments = session.query(TradableInstrumentRow).all()
            for instrument in instruments:
                key = (as_of_date, instrument.instrument_id)
                row = session.get(TradableUniverseSnapshotRow, key)
                if row is None:
                    row = TradableUniverseSnapshotRow(
                        as_of_date=as_of_date,
                        instrument_id=instrument.instrument_id,
                    )
                    session.add(row)
                row.symbol = instrument.symbol
                row.name = instrument.name
                row.asset_type = instrument.asset_type
                row.exchange = instrument.exchange
                row.source = instrument.source
                row.active = True
                row.captured_at = now
            session.commit()
        return len(instruments)

    def upsert_historical_universe_snapshots(
        self,
        profiles: list[HistoricalInstrumentProfile],
        snapshot_dates: list[date],
    ) -> int:
        now = datetime.now(timezone.utc)
        records: list[dict[str, object]] = []
        for snapshot_date in sorted(set(snapshot_dates)):
            for profile in profiles:
                asset_type = normalize_historical_security_type(profile.security_type)
                if asset_type is None:
                    continue
                if profile.listing_date is not None and profile.listing_date > snapshot_date:
                    continue
                if (
                    profile.delisting_date is not None
                    and profile.delisting_date < snapshot_date
                ):
                    continue
                symbol = profile.instrument_id.split(":", 1)[-1]
                records.append(
                    {
                        "as_of_date": snapshot_date,
                        "instrument_id": profile.instrument_id,
                        "symbol": symbol,
                        "name": profile.name or symbol,
                        "asset_type": asset_type,
                        "exchange": (
                            "SH" if symbol.startswith(("5", "6", "9")) else "SZ"
                        ),
                        "source": profile.provider,
                        "active": True,
                        "captured_at": now,
                    }
                )
        with self.session_factory() as session:
            _sqlite_upsert_chunks(
                session,
                TradableUniverseSnapshotRow,
                records,
                ["as_of_date", "instrument_id"],
            )
            session.commit()
        return len(records)

    def count_tradable_universe_snapshots(
        self,
        as_of_date: date | None = None,
        instrument_ids: list[str] | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> int:
        with self.session_factory() as session:
            query = session.query(TradableUniverseSnapshotRow)
            if as_of_date is not None:
                query = query.filter(TradableUniverseSnapshotRow.as_of_date == as_of_date)
            if instrument_ids:
                query = query.filter(
                    TradableUniverseSnapshotRow.instrument_id.in_(instrument_ids)
                )
            if start is not None:
                query = query.filter(TradableUniverseSnapshotRow.as_of_date >= start)
            if end is not None:
                query = query.filter(TradableUniverseSnapshotRow.as_of_date <= end)
            return query.count()

    def tradable_universe_snapshot_stats(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> dict[str, tuple[int, date | None, date | None]]:
        if not instrument_ids:
            return {}
        with self.session_factory() as session:
            rows = (
                session.query(
                    TradableUniverseSnapshotRow.instrument_id,
                    func.count(TradableUniverseSnapshotRow.instrument_id),
                    func.min(TradableUniverseSnapshotRow.as_of_date),
                    func.max(TradableUniverseSnapshotRow.as_of_date),
                )
                .filter(
                    TradableUniverseSnapshotRow.instrument_id.in_(instrument_ids),
                    TradableUniverseSnapshotRow.as_of_date <= end,
                )
                .group_by(TradableUniverseSnapshotRow.instrument_id)
                .all()
            )
        return {
            instrument_id: (int(count), first_date, last_date)
            for instrument_id, count, first_date, last_date in rows
        }

    def upsert_fundamental_snapshots(
        self,
        provider_mode: str,
        snapshots: list[FundamentalSnapshot],
    ) -> int:
        deduplicated = {
            (
                snapshot.instrument_id,
                snapshot.as_of_date,
                (snapshot.provider or "unknown").strip().lower(),
            ): snapshot
            for snapshot in snapshots
        }
        return self.replay_evidence(provider_mode).upsert_fundamentals(
            list(deduplicated.values())
        )

    def list_fundamental_snapshots(
        self,
        provider_mode: str,
        instrument_ids: list[str] | None = None,
        start: date | None = None,
        end: date | None = None,
        limit: int = 50_000,
    ) -> list[FundamentalSnapshot]:
        normalized_mode = provider_mode.strip().lower()
        with self.session_factory() as session:
            row_alias, revision_rank = _latest_revision_alias(
                FundamentalSnapshotRow,
                ("provider_mode", "instrument_id", "as_of_date", "source_provider"),
            )
            query = session.query(row_alias).filter(
                row_alias.provider_mode == normalized_mode,
                revision_rank == 1,
            )
            if instrument_ids:
                query = query.filter(row_alias.instrument_id.in_(instrument_ids))
            if start is not None:
                query = query.filter(row_alias.as_of_date >= start)
            if end is not None:
                query = query.filter(row_alias.as_of_date <= end)
            rows = (
                query.order_by(
                    row_alias.as_of_date.asc(),
                    row_alias.instrument_id.asc(),
                    row_alias.source_provider.asc(),
                )
                .limit(max(limit, 0))
                .all()
            )
            return [self._fundamental_snapshot_from_row(row) for row in rows]

    def fundamental_snapshot_stats(
        self,
        provider_mode: str,
        instrument_ids: list[str],
        end: date,
    ) -> dict[str, tuple[int, date | None, date | None]]:
        if not instrument_ids:
            return {}
        normalized_mode = provider_mode.strip().lower()
        with self.session_factory() as session:
            row_alias, revision_rank = _latest_revision_alias(
                FundamentalSnapshotRow,
                ("provider_mode", "instrument_id", "as_of_date", "source_provider"),
            )
            rows = (
                session.query(
                    row_alias.instrument_id,
                    func.count(row_alias.instrument_id),
                    func.min(row_alias.as_of_date),
                    func.max(row_alias.as_of_date),
                )
                .filter(
                    row_alias.provider_mode == normalized_mode,
                    row_alias.instrument_id.in_(instrument_ids),
                    row_alias.as_of_date <= end,
                    revision_rank == 1,
                )
                .group_by(row_alias.instrument_id)
                .all()
            )
        return {
            instrument_id: (int(count), first_date, last_date)
            for instrument_id, count, first_date, last_date in rows
        }

    def upsert_historical_evidence(
        self,
        provider_mode: str,
        bundle: HistoricalEvidenceBundle,
    ) -> dict[str, int]:
        return self.replay_evidence(provider_mode).upsert_point_in_time_evidence(bundle)

    def historical_evidence_stats(
        self,
        provider_mode: str,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> dict[str, HistoricalInstrumentEvidenceStats]:
        if not instrument_ids:
            return {}
        mode = provider_mode.strip().lower()
        result = {
            instrument_id: HistoricalInstrumentEvidenceStats()
            for instrument_id in instrument_ids
        }
        with self.session_factory() as session:
            tradability_alias, tradability_rank = _latest_revision_alias(
                HistoricalTradabilityRow,
                ("provider_mode", "instrument_id", "trade_date", "source_provider"),
            )
            tradability_rows = (
                session.query(
                    tradability_alias.instrument_id,
                    func.count(tradability_alias.trade_date),
                    func.min(tradability_alias.trade_date),
                    func.max(tradability_alias.trade_date),
                    func.sum(
                        case(
                            (tradability_alias.trading_status == "suspended", 1),
                            else_=0,
                        )
                    ),
                    func.sum(
                        case((tradability_alias.is_st.is_(True), 1), else_=0)
                    ),
                )
                .filter(
                    tradability_alias.provider_mode == mode,
                    tradability_alias.instrument_id.in_(instrument_ids),
                    tradability_alias.trade_date >= start,
                    tradability_alias.trade_date <= end,
                    tradability_rank == 1,
                )
                .group_by(tradability_alias.instrument_id)
                .all()
            )
            profiles = (
                session.query(HistoricalInstrumentProfileRow)
                .filter(
                    HistoricalInstrumentProfileRow.provider_mode == mode,
                    HistoricalInstrumentProfileRow.instrument_id.in_(instrument_ids),
                    HistoricalInstrumentProfileRow.snapshot_date >= start,
                    HistoricalInstrumentProfileRow.snapshot_date <= end,
                )
                .order_by(
                    HistoricalInstrumentProfileRow.snapshot_date.asc(),
                    HistoricalInstrumentProfileRow.dataset_revision.desc(),
                )
                .all()
            )
            latest_profiles = {}
            for row in profiles:
                latest_profiles.setdefault((row.instrument_id, row.snapshot_date), row)
            profiles = list(latest_profiles.values())
            industry_alias, industry_rank = _latest_revision_alias(
                HistoricalIndustrySnapshotRow,
                ("provider_mode", "instrument_id", "snapshot_date", "source_provider"),
            )
            industry_rows = (
                session.query(
                    industry_alias.instrument_id,
                    func.count(industry_alias.snapshot_date),
                    func.min(industry_alias.snapshot_date),
                    func.max(industry_alias.snapshot_date),
                )
                .filter(
                    industry_alias.provider_mode == mode,
                    industry_alias.instrument_id.in_(instrument_ids),
                    industry_alias.snapshot_date >= start,
                    industry_alias.snapshot_date <= end,
                    industry_rank == 1,
                )
                .group_by(industry_alias.instrument_id)
                .all()
            )
            industry_names = (
                session.query(
                    industry_alias.instrument_id,
                    industry_alias.industry,
                )
                .filter(
                    industry_alias.provider_mode == mode,
                    industry_alias.instrument_id.in_(instrument_ids),
                    industry_alias.snapshot_date >= start,
                    industry_alias.snapshot_date <= end,
                    industry_rank == 1,
                )
                .distinct()
                .all()
            )
            membership_snapshot_alias, membership_snapshot_rank = _latest_revision_alias(
                HistoricalIndexSnapshotRow,
                ("provider_mode", "index_id", "snapshot_date", "source_provider"),
            )
            membership_rows = (
                session.query(
                    HistoricalIndexMembershipRow.instrument_id,
                    func.count(HistoricalIndexMembershipRow.index_id),
                )
                .join(
                    membership_snapshot_alias,
                    (
                        HistoricalIndexMembershipRow.provider_mode
                        == membership_snapshot_alias.provider_mode
                    )
                    & (
                        HistoricalIndexMembershipRow.index_id
                        == membership_snapshot_alias.index_id
                    )
                    & (
                        HistoricalIndexMembershipRow.snapshot_date
                        == membership_snapshot_alias.snapshot_date
                    )
                    & (
                        HistoricalIndexMembershipRow.source_provider
                        == membership_snapshot_alias.source_provider
                    )
                    & (
                        HistoricalIndexMembershipRow.dataset_revision
                        == membership_snapshot_alias.dataset_revision
                    ),
                )
                .filter(
                    HistoricalIndexMembershipRow.provider_mode == mode,
                    HistoricalIndexMembershipRow.instrument_id.in_(instrument_ids),
                    HistoricalIndexMembershipRow.snapshot_date >= start,
                    HistoricalIndexMembershipRow.snapshot_date <= end,
                    membership_snapshot_alias.status == "ready",
                    membership_snapshot_rank == 1,
                )
                .group_by(HistoricalIndexMembershipRow.instrument_id)
                .all()
            )
            membership_ids = (
                session.query(
                    HistoricalIndexMembershipRow.instrument_id,
                    HistoricalIndexMembershipRow.index_id,
                )
                .join(
                    membership_snapshot_alias,
                    (
                        HistoricalIndexMembershipRow.provider_mode
                        == membership_snapshot_alias.provider_mode
                    )
                    & (
                        HistoricalIndexMembershipRow.index_id
                        == membership_snapshot_alias.index_id
                    )
                    & (
                        HistoricalIndexMembershipRow.snapshot_date
                        == membership_snapshot_alias.snapshot_date
                    )
                    & (
                        HistoricalIndexMembershipRow.source_provider
                        == membership_snapshot_alias.source_provider
                    )
                    & (
                        HistoricalIndexMembershipRow.dataset_revision
                        == membership_snapshot_alias.dataset_revision
                    ),
                )
                .filter(
                    HistoricalIndexMembershipRow.provider_mode == mode,
                    HistoricalIndexMembershipRow.instrument_id.in_(instrument_ids),
                    HistoricalIndexMembershipRow.snapshot_date >= start,
                    HistoricalIndexMembershipRow.snapshot_date <= end,
                    membership_snapshot_alias.status == "ready",
                    membership_snapshot_rank == 1,
                )
                .distinct()
                .all()
            )

        for instrument_id, count, first_date, last_date, suspended, st_count in tradability_rows:
            stats = result[instrument_id]
            stats.tradability_rows = int(count)
            stats.first_tradability_date = first_date
            stats.last_tradability_date = last_date
            stats.suspended_rows = int(suspended or 0)
            stats.st_rows = int(st_count or 0)
        for row in profiles:
            stats = result[row.instrument_id]
            stats.profile_rows += 1
            stats.listing_date = row.listing_date
            stats.delisting_date = row.delisting_date
            stats.listing_status = row.listing_status
        for instrument_id, count, first_date, last_date in industry_rows:
            stats = result[instrument_id]
            stats.industry_rows = int(count)
            stats.first_industry_date = first_date
            stats.last_industry_date = last_date
        for instrument_id, industry in industry_names:
            result[instrument_id].industries.append(industry)
        for instrument_id, count in membership_rows:
            result[instrument_id].benchmark_membership_rows = int(count)
        for instrument_id, index_id in membership_ids:
            result[instrument_id].benchmark_ids.append(index_id)
        for stats in result.values():
            stats.industries.sort()
            stats.benchmark_ids.sort()
        return result

    def historical_index_snapshot_stats(
        self,
        provider_mode: str,
        start: date,
        end: date,
    ) -> HistoricalIndexCoverageStats:
        mode = provider_mode.strip().lower()
        with self.session_factory() as session:
            snapshot_alias, snapshot_rank = _latest_revision_alias(
                HistoricalIndexSnapshotRow,
                ("provider_mode", "index_id", "snapshot_date", "source_provider"),
            )
            row = (
                session.query(
                    func.count(snapshot_alias.index_id),
                    func.sum(
                        case((snapshot_alias.status == "ready", 1), else_=0)
                    ),
                    func.sum(
                        case((snapshot_alias.status == "failed", 1), else_=0)
                    ),
                    func.min(snapshot_alias.snapshot_date),
                    func.max(snapshot_alias.snapshot_date),
                )
                .filter(
                    snapshot_alias.provider_mode == mode,
                    snapshot_alias.snapshot_date >= start,
                    snapshot_alias.snapshot_date <= end,
                    snapshot_rank == 1,
                )
                .one()
            )
            index_ids = [
                value
                for (value,) in (
                    session.query(snapshot_alias.index_id)
                    .filter(
                        snapshot_alias.provider_mode == mode,
                        snapshot_alias.snapshot_date >= start,
                        snapshot_alias.snapshot_date <= end,
                        snapshot_rank == 1,
                    )
                    .distinct()
                    .all()
                )
            ]
        return HistoricalIndexCoverageStats(
            total_snapshots=int(row[0] or 0),
            ready_snapshots=int(row[1] or 0),
            failed_snapshots=int(row[2] or 0),
            first_snapshot_date=row[3],
            last_snapshot_date=row[4],
            index_ids=sorted(index_ids),
        )

    def save_scan_run(
        self,
        provider: str,
        mode: str,
        symbols: list[str],
        result,
    ) -> ScanRunRecord:
        run_id = f"scan-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
        item_by_instrument = {item.instrument_id: item for item in result.items}
        with self.session_factory() as session:
            run_row = ScanRunRow(
                run_id=run_id,
                provider=provider,
                mode=mode,
                symbols=json.dumps(symbols),
                scanned=len(result.items),
                cards=len(result.cards),
                data_health=json.dumps(result.data_health, sort_keys=True),
            )
            session.add(run_row)
            for card in result.cards:
                item = item_by_instrument.get(card.instrument_id)
                session.add(self._snapshot_row_from_card(run_id, card, item))
            session.commit()
            session.refresh(run_row)
            return self._scan_run_from_row(run_row)

    def list_scan_runs(self, limit: int = 20, provider: str | None = None) -> list[ScanRunRecord]:
        with self.session_factory() as session:
            query = session.query(ScanRunRow)
            if provider:
                query = query.filter(ScanRunRow.provider == provider)
            rows = (
                query
                .order_by(ScanRunRow.created_at.desc(), ScanRunRow.run_id.desc())
                .limit(limit)
                .all()
            )
            return [self._scan_run_from_row(row) for row in rows]

    def save_scan_result_cache(
        self,
        cache_key: str,
        provider: str,
        mode: str,
        symbols: list[str],
        payload: dict[str, object],
    ) -> ScanResultCacheRecord:
        cache_id = f"scan-cache-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
        with self.session_factory() as session:
            row = ScanResultCacheRow(
                cache_id=cache_id,
                cache_key=cache_key,
                provider=provider,
                mode=mode,
                symbols=json.dumps(symbols),
                payload_json=json.dumps(payload, sort_keys=True),
            )
            session.add(row)
            session.flush()
            stale_ids = [
                cache_id
                for (cache_id,) in (
                    session.query(ScanResultCacheRow.cache_id)
                    .filter(ScanResultCacheRow.cache_key == cache_key)
                    .order_by(
                        ScanResultCacheRow.created_at.desc(),
                        ScanResultCacheRow.cache_id.desc(),
                    )
                    .offset(3)
                    .all()
                )
            ]
            if stale_ids:
                session.query(ScanResultCacheRow).filter(
                    ScanResultCacheRow.cache_id.in_(stale_ids)
                ).delete(synchronize_session=False)
            session.commit()
            session.refresh(row)
            return self._scan_result_cache_from_row(row)

    def get_recent_scan_result_cache(
        self,
        cache_key: str,
        max_age: timedelta,
    ) -> ScanResultCacheRecord | None:
        earliest = datetime.now(timezone.utc) - max_age
        with self.session_factory() as session:
            row = (
                session.query(ScanResultCacheRow)
                .filter(
                    ScanResultCacheRow.cache_key == cache_key,
                    ScanResultCacheRow.created_at >= earliest,
                )
                .order_by(ScanResultCacheRow.created_at.desc(), ScanResultCacheRow.cache_id.desc())
                .first()
            )
            if row is None:
                return None
            return self._scan_result_cache_from_row(row)

    def get_latest_scan_result_cache_by_modes(
        self,
        provider: str,
        modes: set[str],
        max_age: timedelta,
    ) -> ScanResultCacheRecord | None:
        earliest = datetime.now(timezone.utc) - max_age
        normalized_modes = {mode.strip() for mode in modes if mode.strip()}
        if not normalized_modes:
            return None
        with self.session_factory() as session:
            row = (
                session.query(ScanResultCacheRow)
                .filter(
                    ScanResultCacheRow.provider == provider,
                    ScanResultCacheRow.mode.in_(normalized_modes),
                    ScanResultCacheRow.created_at >= earliest,
                )
                .order_by(ScanResultCacheRow.created_at.desc(), ScanResultCacheRow.cache_id.desc())
                .first()
            )
            if row is None:
                return None
            return self._scan_result_cache_from_row(row)

    def get_recent_scan_run_with_snapshots(
        self,
        provider: str,
        scanned: int,
        max_age: timedelta,
    ) -> ScanRunSnapshotBundle | None:
        earliest = datetime.now(timezone.utc) - max_age
        with self.session_factory() as session:
            run_row = (
                session.query(ScanRunRow)
                .filter(
                    ScanRunRow.provider == provider,
                    ScanRunRow.scanned == scanned,
                    ScanRunRow.created_at >= earliest,
                )
                .order_by(ScanRunRow.created_at.desc(), ScanRunRow.run_id.desc())
                .first()
            )
            if run_row is None:
                return None
            snapshot_rows = (
                session.query(OpportunitySnapshotRow)
                .filter(OpportunitySnapshotRow.run_id == run_row.run_id)
                .order_by(
                    OpportunitySnapshotRow.rank_score.desc(),
                    OpportunitySnapshotRow.score.desc(),
                    OpportunitySnapshotRow.snapshot_id.desc(),
                )
                .all()
            )
            return ScanRunSnapshotBundle(
                run=self._scan_run_from_row(run_row),
                snapshots=[self._opportunity_snapshot_from_row(row) for row in snapshot_rows],
            )

    def create_full_market_scan_job(
        self,
        provider: str,
        symbols: list[str],
        batch_size: int,
        include_etfs: bool,
        sync_if_empty: bool,
    ) -> FullMarketScanJobRecord:
        now = datetime.now(timezone.utc)
        total_symbols = len(symbols)
        total_batches = (total_symbols + batch_size - 1) // batch_size if batch_size > 0 else 0
        job_id = f"full-scan-{now.strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
        with self.session_factory() as session:
            row = FullMarketScanJobRow(
                job_id=job_id,
                provider=provider,
                status="queued",
                batch_size=batch_size,
                total_symbols=total_symbols,
                scanned_symbols=0,
                total_batches=total_batches,
                completed_batches=0,
                cards=0,
                errors=0,
                include_etfs=include_etfs,
                sync_if_empty=sync_if_empty,
                symbols=json.dumps(symbols),
                message="Queued full-market batch scan",
                data_health=json.dumps({}),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._full_market_scan_job_from_row(row)

    def update_full_market_scan_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        scanned_symbols: int | None = None,
        completed_batches: int | None = None,
        cards: int | None = None,
        errors: int | None = None,
        message: str | None = None,
        data_health: dict[str, str] | None = None,
        result_cache_key: str | None = None,
    ) -> FullMarketScanJobRecord | None:
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            row = session.get(FullMarketScanJobRow, job_id)
            if row is None:
                return None
            if status is not None:
                row.status = status
                if status == "running" and row.started_at is None:
                    row.started_at = now
                if status in {"succeeded", "failed", "cancelled"}:
                    row.finished_at = now
            if scanned_symbols is not None:
                row.scanned_symbols = scanned_symbols
            if completed_batches is not None:
                row.completed_batches = completed_batches
            if cards is not None:
                row.cards = cards
            if errors is not None:
                row.errors = errors
            if message is not None:
                row.message = message
            if data_health is not None:
                row.data_health = json.dumps(data_health, sort_keys=True)
            if result_cache_key is not None:
                row.result_cache_key = result_cache_key
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return self._full_market_scan_job_from_row(row)

    def get_full_market_scan_job(self, job_id: str) -> FullMarketScanJobRecord | None:
        with self.session_factory() as session:
            row = session.get(FullMarketScanJobRow, job_id)
            if row is None:
                return None
            return self._full_market_scan_job_from_row(row)

    def get_latest_full_market_scan_job(
        self,
        provider: str | None = None,
    ) -> FullMarketScanJobRecord | None:
        with self.session_factory() as session:
            query = session.query(FullMarketScanJobRow)
            if provider:
                query = query.filter(FullMarketScanJobRow.provider == provider)
            row = (
                query.order_by(
                    FullMarketScanJobRow.created_at.desc(),
                    FullMarketScanJobRow.job_id.desc(),
                )
                .first()
            )
            if row is None:
                return None
            return self._full_market_scan_job_from_row(row)

    def create_historical_backfill_job(
        self,
        provider: str,
        symbols: list[str],
        start: date,
        end: date,
    ) -> HistoricalBackfillJobRecord:
        now = datetime.now(timezone.utc)
        job_id = f"history-backfill-{now.strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
        with self.session_factory() as session:
            row = HistoricalBackfillJobRow(
                job_id=job_id,
                provider=provider,
                status="queued",
                start_date=start,
                end_date=end,
                symbols=json.dumps(symbols),
                total_symbols=len(symbols),
                processed_symbols=0,
                succeeded_symbols=0,
                failed_symbols=0,
                rows_written=0,
                fundamental_rows_written=0,
                errors_json="[]",
                data_health="{}",
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._historical_backfill_job_from_row(row)

    def update_historical_backfill_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        processed_symbols: int | None = None,
        succeeded_symbols: int | None = None,
        failed_symbols: int | None = None,
        rows_written: int | None = None,
        fundamental_rows_written: int | None = None,
        current_instrument: str | None = None,
        errors: list[str] | None = None,
        data_health: dict[str, str] | None = None,
    ) -> HistoricalBackfillJobRecord | None:
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            row = session.get(HistoricalBackfillJobRow, job_id)
            if row is None:
                return None
            if status is not None:
                row.status = status
                if status == "running" and row.started_at is None:
                    row.started_at = now
                if status in {"succeeded", "succeeded_with_errors", "failed", "cancelled"}:
                    row.finished_at = now
            if processed_symbols is not None:
                row.processed_symbols = processed_symbols
            if succeeded_symbols is not None:
                row.succeeded_symbols = succeeded_symbols
            if failed_symbols is not None:
                row.failed_symbols = failed_symbols
            if rows_written is not None:
                row.rows_written = rows_written
            if fundamental_rows_written is not None:
                row.fundamental_rows_written = fundamental_rows_written
            if current_instrument is not None:
                row.current_instrument = current_instrument
            if errors is not None:
                row.errors_json = json.dumps(errors)
            if data_health is not None:
                row.data_health = json.dumps(data_health, sort_keys=True)
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return self._historical_backfill_job_from_row(row)

    def get_historical_backfill_job(
        self,
        job_id: str,
    ) -> HistoricalBackfillJobRecord | None:
        with self.session_factory() as session:
            row = session.get(HistoricalBackfillJobRow, job_id)
            return self._historical_backfill_job_from_row(row) if row is not None else None

    def get_latest_historical_backfill_job(
        self,
        provider: str | None = None,
    ) -> HistoricalBackfillJobRecord | None:
        with self.session_factory() as session:
            query = session.query(HistoricalBackfillJobRow)
            if provider:
                query = query.filter(HistoricalBackfillJobRow.provider == provider)
            row = query.order_by(
                HistoricalBackfillJobRow.created_at.desc(),
                HistoricalBackfillJobRow.job_id.desc(),
            ).first()
            return self._historical_backfill_job_from_row(row) if row is not None else None

    def save_walk_forward_run(
        self,
        result,
        *,
        status: str = "succeeded",
    ) -> WalkForwardRunRecord:
        payload = result.model_dump(mode="json")
        data_health = dict(result.data_health)
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            row = session.get(WalkForwardRunRow, result.owner_run_id)
            values = {
                "provider": result.provider_mode,
                "status": status,
                "start_date": result.start_date,
                "end_date": result.end_date,
                "dataset_revision": result.dataset_revision,
                "rebalance_step_sessions": result.rebalance_step_sessions,
                "lookback_days": int(
                    data_health.get("walk_forward_lookback_days", 0) or 0
                ),
                "snapshot_count": len(result.snapshots),
                "top_5_trade_count": result.top_5_metrics.trade_count,
                "top_10_trade_count": result.top_10_metrics.trade_count,
                "top_5_return_pct": result.top_5_metrics.total_return_pct,
                "top_10_return_pct": result.top_10_metrics.total_return_pct,
                "top_5_oos_trades": int(
                    data_health.get("walk_forward_top_5_oos_trades", 0) or 0
                ),
                "top_10_oos_trades": int(
                    data_health.get("walk_forward_top_10_oos_trades", 0) or 0
                ),
                "top_5_oos_gate": data_health.get(
                    "walk_forward_top_5_oos_gate", "insufficient"
                ),
                "top_10_oos_gate": data_health.get(
                    "walk_forward_top_10_oos_gate", "insufficient"
                ),
                "reproducibility_digest": result.reproducibility_digest,
                "payload_json": json.dumps(payload, ensure_ascii=True, sort_keys=True),
                "data_health": json.dumps(data_health, ensure_ascii=True, sort_keys=True),
                "updated_at": now,
            }
            if row is None:
                row = WalkForwardRunRow(
                    run_id=result.owner_run_id,
                    created_at=now,
                    **values,
                )
                session.add(row)
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            session.commit()
            session.refresh(row)
            return self._walk_forward_run_from_row(row)

    def create_walk_forward_job(
        self,
        *,
        job_id: str,
        provider: str,
        start: date,
        end: date,
        dataset_revision: int,
        rebalance_step_sessions: int,
        lookback_days: int,
        total_snapshots: int,
        experiment_manifest: dict[str, object],
    ) -> WalkForwardJobRecord:
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            row = WalkForwardJobRow(
                job_id=job_id,
                provider=provider,
                status="queued",
                phase="queued",
                start_date=start,
                end_date=end,
                dataset_revision=dataset_revision,
                rebalance_step_sessions=rebalance_step_sessions,
                lookback_days=lookback_days,
                total_snapshots=total_snapshots,
                processed_snapshots=0,
                checkpoints_json="[]",
                experiment_manifest_json=json.dumps(
                    experiment_manifest,
                    ensure_ascii=True,
                    sort_keys=True,
                ),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._walk_forward_job_from_row(row)

    def update_walk_forward_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        phase: str | None = None,
        processed_snapshots: int | None = None,
        current_date: date | None = None,
        checkpoints: list[dict[str, object]] | None = None,
        result_run_id: str | None = None,
        error: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> WalkForwardJobRecord:
        with self.session_factory() as session:
            row = session.get(WalkForwardJobRow, job_id)
            if row is None:
                raise ValueError(f"walk-forward job not found: {job_id}")
            values = {
                "status": status,
                "phase": phase,
                "processed_snapshots": processed_snapshots,
                "current_date": current_date,
                "result_run_id": result_run_id,
                "error": error,
                "started_at": started_at,
                "finished_at": finished_at,
            }
            for key, value in values.items():
                if value is not None:
                    setattr(row, key, value)
            if checkpoints is not None:
                row.checkpoints_json = json.dumps(
                    checkpoints,
                    ensure_ascii=True,
                    sort_keys=True,
                )
            row.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(row)
            return self._walk_forward_job_from_row(row)

    def get_walk_forward_job(self, job_id: str) -> WalkForwardJobRecord | None:
        with self.session_factory() as session:
            row = session.get(WalkForwardJobRow, job_id)
            return self._walk_forward_job_from_row(row) if row is not None else None

    def list_walk_forward_jobs(
        self,
        *,
        provider: str | None = None,
        limit: int = 20,
    ) -> list[WalkForwardJobRecord]:
        bounded_limit = max(1, min(limit, 100))
        with self.session_factory() as session:
            query = session.query(WalkForwardJobRow)
            if provider:
                query = query.filter(WalkForwardJobRow.provider == provider)
            rows = query.order_by(
                WalkForwardJobRow.created_at.desc(),
                WalkForwardJobRow.job_id.desc(),
            ).limit(bounded_limit).all()
            return [self._walk_forward_job_from_row(row) for row in rows]

    def get_walk_forward_run(self, run_id: str) -> WalkForwardRunRecord | None:
        with self.session_factory() as session:
            row = session.get(WalkForwardRunRow, run_id)
            return self._walk_forward_run_from_row(row) if row is not None else None

    def list_walk_forward_runs(
        self,
        *,
        provider: str | None = None,
        limit: int = 20,
    ) -> list[WalkForwardRunRecord]:
        bounded_limit = max(1, min(limit, 100))
        with self.session_factory() as session:
            query = session.query(WalkForwardRunRow)
            if provider:
                query = query.filter(WalkForwardRunRow.provider == provider)
            rows = query.order_by(
                WalkForwardRunRow.created_at.desc(),
                WalkForwardRunRow.run_id.desc(),
            ).limit(bounded_limit).all()
            return [self._walk_forward_run_from_row(row) for row in rows]

    def list_opportunity_snapshots(
        self,
        instrument_id: str | None = None,
        limit: int = 50,
        provider: str | None = None,
    ) -> list[OpportunitySnapshotRecord]:
        with self.session_factory() as session:
            query = session.query(OpportunitySnapshotRow)
            if instrument_id:
                query = query.filter(OpportunitySnapshotRow.instrument_id == instrument_id)
            if provider:
                query = query.join(ScanRunRow, OpportunitySnapshotRow.run_id == ScanRunRow.run_id)
                query = query.filter(ScanRunRow.provider == provider)
            rows = (
                query.order_by(
                    OpportunitySnapshotRow.created_at.desc(),
                    OpportunitySnapshotRow.snapshot_id.desc(),
                )
                .limit(limit)
                .all()
            )
            return [self._opportunity_snapshot_from_row(row) for row in rows]

    def list_top_daily_opportunity_snapshots(
        self,
        *,
        start: date,
        end: date,
        top_n: int = 5,
        provider: str | None = None,
    ) -> list[OpportunitySnapshotRecord]:
        bounded_top_n = max(1, min(top_n, 20))
        with self.session_factory() as session:
            per_instrument = session.query(
                OpportunitySnapshotRow.snapshot_id.label("snapshot_id"),
                OpportunitySnapshotRow.signal_date.label("signal_date"),
                OpportunitySnapshotRow.instrument_id.label("instrument_id"),
                OpportunitySnapshotRow.rank_score.label("rank_score"),
                OpportunitySnapshotRow.strategy_score.label("strategy_score"),
                OpportunitySnapshotRow.score.label("score"),
                OpportunitySnapshotRow.created_at.label("created_at"),
                func.row_number()
                .over(
                    partition_by=(
                        OpportunitySnapshotRow.signal_date,
                        OpportunitySnapshotRow.instrument_id,
                    ),
                    order_by=(
                        OpportunitySnapshotRow.rank_score.desc(),
                        OpportunitySnapshotRow.strategy_score.desc(),
                        OpportunitySnapshotRow.score.desc(),
                        OpportunitySnapshotRow.created_at.desc(),
                        OpportunitySnapshotRow.snapshot_id.desc(),
                    ),
                )
                .label("instrument_rank"),
            ).filter(
                OpportunitySnapshotRow.signal_date.isnot(None),
                OpportunitySnapshotRow.signal_date >= start,
                OpportunitySnapshotRow.signal_date <= end,
            )
            if provider:
                per_instrument = per_instrument.join(
                    ScanRunRow,
                    OpportunitySnapshotRow.run_id == ScanRunRow.run_id,
                ).filter(ScanRunRow.provider == provider)
            unique_instruments = per_instrument.subquery()
            per_day = (
                session.query(
                    unique_instruments.c.snapshot_id,
                    unique_instruments.c.signal_date,
                    func.row_number()
                    .over(
                        partition_by=unique_instruments.c.signal_date,
                        order_by=(
                            unique_instruments.c.rank_score.desc(),
                            unique_instruments.c.strategy_score.desc(),
                            unique_instruments.c.score.desc(),
                            unique_instruments.c.created_at.desc(),
                            unique_instruments.c.snapshot_id.desc(),
                        ),
                    )
                    .label("daily_rank"),
                )
                .filter(unique_instruments.c.instrument_rank == 1)
                .subquery()
            )
            rows = (
                session.query(OpportunitySnapshotRow)
                .join(per_day, OpportunitySnapshotRow.snapshot_id == per_day.c.snapshot_id)
                .filter(per_day.c.daily_rank <= bounded_top_n)
                .order_by(
                    OpportunitySnapshotRow.signal_date.desc(),
                    OpportunitySnapshotRow.rank_score.desc(),
                )
                .all()
            )
            return [self._opportunity_snapshot_from_row(row) for row in rows]

    def list_latest_signal_opportunity_snapshots(
        self,
        limit: int = 50,
        provider: str | None = None,
    ) -> list[OpportunitySnapshotRecord]:
        with self.session_factory() as session:
            latest_query = session.query(func.max(OpportunitySnapshotRow.signal_date)).filter(
                OpportunitySnapshotRow.signal_date.isnot(None),
                OpportunitySnapshotRow.trigger_price.isnot(None),
            )
            if provider:
                latest_query = latest_query.join(
                    ScanRunRow,
                    OpportunitySnapshotRow.run_id == ScanRunRow.run_id,
                ).filter(ScanRunRow.provider == provider)
            latest_signal_date = latest_query.scalar()
            if latest_signal_date is None:
                return []

            query = session.query(OpportunitySnapshotRow).filter(
                OpportunitySnapshotRow.signal_date == latest_signal_date,
                OpportunitySnapshotRow.trigger_price.isnot(None),
            )
            if provider:
                query = query.join(ScanRunRow, OpportunitySnapshotRow.run_id == ScanRunRow.run_id)
                query = query.filter(ScanRunRow.provider == provider)
            rows = (
                query.order_by(
                    OpportunitySnapshotRow.rank_score.desc(),
                    OpportunitySnapshotRow.score.desc(),
                    OpportunitySnapshotRow.created_at.desc(),
                    OpportunitySnapshotRow.snapshot_id.desc(),
                )
                .all()
            )
            snapshots: list[OpportunitySnapshotRecord] = []
            seen_instruments: set[str] = set()
            for row in rows:
                if row.instrument_id in seen_instruments:
                    continue
                snapshots.append(self._opportunity_snapshot_from_row(row))
                seen_instruments.add(row.instrument_id)
                if len(snapshots) >= limit:
                    break
            return snapshots

    def list_latest_opportunity_snapshots_by_card_ids(
        self,
        card_ids: list[str],
        provider: str | None = None,
    ) -> list[OpportunitySnapshotRecord]:
        ordered_ids = [card_id for card_id in _dedupe_strings(card_ids) if card_id]
        if not ordered_ids:
            return []
        with self.session_factory() as session:
            query = session.query(OpportunitySnapshotRow).filter(
                OpportunitySnapshotRow.card_id.in_(ordered_ids),
                OpportunitySnapshotRow.trigger_price.isnot(None),
            )
            if provider:
                query = query.join(ScanRunRow, OpportunitySnapshotRow.run_id == ScanRunRow.run_id)
                query = query.filter(ScanRunRow.provider == provider)
            rows = (
                query.order_by(
                    OpportunitySnapshotRow.created_at.desc(),
                    OpportunitySnapshotRow.snapshot_id.desc(),
                )
                .all()
            )
            latest_by_card_id: dict[str, OpportunitySnapshotRecord] = {}
            for row in rows:
                if row.card_id not in latest_by_card_id:
                    latest_by_card_id[row.card_id] = self._opportunity_snapshot_from_row(row)
            return [
                latest_by_card_id[card_id]
                for card_id in ordered_ids
                if card_id in latest_by_card_id
            ]

    def list_latest_opportunity_snapshots_by_instruments(
        self,
        instrument_ids: list[str],
        provider: str | None = None,
    ) -> list[OpportunitySnapshotRecord]:
        ordered_ids = [instrument_id for instrument_id in _dedupe_strings(instrument_ids) if instrument_id]
        if not ordered_ids:
            return []
        with self.session_factory() as session:
            query = session.query(OpportunitySnapshotRow).filter(
                OpportunitySnapshotRow.instrument_id.in_(ordered_ids),
                OpportunitySnapshotRow.trigger_price.isnot(None),
            )
            if provider:
                query = query.join(ScanRunRow, OpportunitySnapshotRow.run_id == ScanRunRow.run_id)
                query = query.filter(ScanRunRow.provider == provider)
            rows = (
                query.order_by(
                    OpportunitySnapshotRow.created_at.desc(),
                    OpportunitySnapshotRow.snapshot_id.desc(),
                )
                .all()
            )
            latest_by_instrument: dict[str, OpportunitySnapshotRecord] = {}
            for row in rows:
                if row.instrument_id not in latest_by_instrument:
                    latest_by_instrument[row.instrument_id] = self._opportunity_snapshot_from_row(row)
            return [
                latest_by_instrument[instrument_id]
                for instrument_id in ordered_ids
                if instrument_id in latest_by_instrument
            ]

    def save_brief_run(self, brief) -> BriefRunRecord:
        brief_id = f"brief-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
        payload = brief.model_dump(mode="json")
        with self.session_factory() as session:
            row = BriefRunRow(
                brief_id=brief_id,
                provider=brief.provider,
                symbols=json.dumps(brief.symbols),
                headline=brief.headline,
                opportunity_count=len(brief.top_opportunities),
                entry_watch_count=len(brief.entry_watch),
                risk_alert_count=len(brief.risk_alerts),
                catalyst_count=len(brief.catalyst_watch),
                validation_count=len(brief.strategy_validation),
                data_health=json.dumps(brief.data_health, sort_keys=True),
                brief_json=json.dumps(payload, sort_keys=True),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._brief_run_from_row(row)

    def list_brief_runs(self, limit: int = 20, provider: str | None = None) -> list[BriefRunRecord]:
        with self.session_factory() as session:
            query = session.query(BriefRunRow)
            if provider:
                query = query.filter(BriefRunRow.provider == provider)
            rows = (
                query
                .order_by(BriefRunRow.created_at.desc(), BriefRunRow.brief_id.desc())
                .limit(limit)
                .all()
            )
            return [self._brief_run_from_row(row) for row in rows]

    def get_brief_run(self, brief_id: str) -> BriefRunRecord | None:
        with self.session_factory() as session:
            row = session.get(BriefRunRow, brief_id)
            if row is None:
                return None
            return self._brief_run_from_row(row)

    def enqueue_brief_delivery(
        self,
        brief_run: BriefRunRecord,
        channel: str = "markdown",
        recipient: str | None = None,
        markdown: str = "",
    ) -> DeliveryOutboxRecord:
        delivery_id = (
            f"delivery-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
        )
        payload = {
            "brief_id": brief_run.brief_id,
            "provider": brief_run.provider,
            "symbols": brief_run.symbols,
            "opportunity_count": brief_run.opportunity_count,
            "entry_watch_count": brief_run.entry_watch_count,
            "risk_alert_count": brief_run.risk_alert_count,
            "catalyst_count": brief_run.catalyst_count,
            "validation_count": brief_run.validation_count,
        }
        with self.session_factory() as session:
            row = DeliveryOutboxRow(
                delivery_id=delivery_id,
                brief_id=brief_run.brief_id,
                channel=channel,
                recipient=recipient,
                subject=brief_run.headline,
                markdown=markdown,
                payload_json=json.dumps(payload, sort_keys=True),
                status="queued",
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._delivery_outbox_from_row(row)

    def enqueue_delivery(
        self,
        subject: str,
        markdown: str,
        channel: str = "markdown",
        recipient: str | None = None,
        payload: dict[str, object] | None = None,
        brief_id: str | None = None,
    ) -> DeliveryOutboxRecord:
        delivery_id = (
            f"delivery-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
        )
        with self.session_factory() as session:
            row = DeliveryOutboxRow(
                delivery_id=delivery_id,
                brief_id=brief_id or "",
                channel=channel,
                recipient=recipient,
                subject=subject,
                markdown=markdown,
                payload_json=json.dumps(payload or {}, sort_keys=True),
                status="queued",
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._delivery_outbox_from_row(row)

    def list_delivery_outbox(
        self,
        status: str | None = None,
        limit: int = 20,
        provider: str | None = None,
    ) -> list[DeliveryOutboxRecord]:
        with self.session_factory() as session:
            query = session.query(DeliveryOutboxRow)
            if status:
                query = query.filter(DeliveryOutboxRow.status == status)
            if provider:
                query = query.join(BriefRunRow, DeliveryOutboxRow.brief_id == BriefRunRow.brief_id)
                query = query.filter(BriefRunRow.provider == provider)
            rows = (
                query.order_by(
                    DeliveryOutboxRow.created_at.desc(),
                    DeliveryOutboxRow.delivery_id.desc(),
                )
                .limit(limit)
                .all()
            )
            return [self._delivery_outbox_from_row(row) for row in rows]

    def mark_delivery_sent(self, delivery_id: str) -> DeliveryOutboxRecord | None:
        with self.session_factory() as session:
            row = session.get(DeliveryOutboxRow, delivery_id)
            if row is None:
                return None
            row.status = "sent"
            row.sent_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(row)
            return self._delivery_outbox_from_row(row)

    @staticmethod
    def _watchlist_from_row(row: WatchlistItemRow) -> WatchlistItem:
        return WatchlistItem(
            instrument_id=row.instrument_id,
            thesis=row.thesis,
            status=row.status,
            tags=_parse_tags(row.tags),
        )

    @staticmethod
    def _position_from_row(row: PositionRow) -> Position:
        return Position(
            instrument_id=row.instrument_id,
            shares=row.shares,
            entry_price=row.entry_price,
            entry_date=row.entry_date,
            strategy_tag=row.strategy_tag,
            initial_stop=row.initial_stop,
            target_1=row.target_1,
            target_2=row.target_2,
            thesis=row.thesis,
        )

    @staticmethod
    def _alert_rule_from_row(row: AlertRuleRow) -> StoredAlertRule:
        return StoredAlertRule(
            rule_id=row.rule_id,
            instrument_id=row.instrument_id,
            kind=row.kind,
            operator=row.operator,
            threshold=row.threshold,
        )

    @staticmethod
    def _universe_from_row(row: UniverseRow) -> UniverseRecord:
        return UniverseRecord(
            universe_id=row.universe_id,
            name=row.name,
            description=row.description,
            market_scope=row.market_scope,
            tags=_parse_tags(row.tags),
            symbols=json.loads(row.symbols or "[]"),
            source=row.source,
        )

    @staticmethod
    def _tradable_instrument_from_row(row: TradableInstrumentRow) -> StoredTradableInstrument:
        return StoredTradableInstrument(
            instrument_id=row.instrument_id,
            symbol=row.symbol,
            name=row.name,
            label=row.label,
            asset_type=row.asset_type,
            exchange=row.exchange,
            source=row.source,
            tags=_parse_tags(row.tags),
            synced_at=row.synced_at,
        )

    @staticmethod
    def _fundamental_snapshot_from_row(row: FundamentalSnapshotRow) -> FundamentalSnapshot:
        return FundamentalSnapshot(
            instrument_id=row.instrument_id,
            as_of_date=row.as_of_date,
            revenue_growth_pct=row.revenue_growth_pct,
            earnings_growth_pct=row.earnings_growth_pct,
            gross_margin_pct=row.gross_margin_pct,
            operating_margin_pct=row.operating_margin_pct,
            net_margin_pct=row.net_margin_pct,
            return_on_equity_pct=row.return_on_equity_pct,
            market_cap=row.market_cap,
            pe_ratio=row.pe_ratio,
            forward_pe=row.forward_pe,
            peg_ratio=row.peg_ratio,
            price_to_sales=row.price_to_sales,
            provider=row.source_provider,
        )

    @staticmethod
    def _snapshot_row_from_card(
        run_id: str,
        card: OpportunityCard,
        item,
    ) -> OpportunitySnapshotRow:
        signal_date = getattr(item, "latest_trade_date", None) if item else None
        latest_close = _decimal_or_none(getattr(item, "latest_close", None) if item else None)
        return OpportunitySnapshotRow(
            snapshot_id=f"{run_id}:{card.card_id}",
            run_id=run_id,
            card_id=card.card_id,
            instrument_id=card.instrument_id,
            market=card.market.value,
            status=card.status.value,
            signal_date=signal_date,
            latest_close=latest_close,
            primary_strategy_id=card.primary_strategy_id,
            score=Decimal(str(card.score)),
            strategy_score=Decimal(str(card.strategy_score)),
            rank_score=Decimal(str(card.rank_score)),
            trigger_price=card.entry_plan.trigger_price,
            initial_stop=card.exit_plan.initial_stop,
            target_1=card.exit_plan.target_1,
            card_json=json.dumps(card.model_dump(mode="json"), sort_keys=True),
        )

    @staticmethod
    def _scan_run_from_row(row: ScanRunRow) -> ScanRunRecord:
        return ScanRunRecord(
            run_id=row.run_id,
            provider=row.provider,
            mode=row.mode,
            symbols=json.loads(row.symbols or "[]"),
            scanned=row.scanned,
            cards=row.cards,
            data_health=json.loads(row.data_health or "{}"),
            created_at=row.created_at,
        )

    @staticmethod
    def _scan_result_cache_from_row(row: ScanResultCacheRow) -> ScanResultCacheRecord:
        return ScanResultCacheRecord(
            cache_id=row.cache_id,
            cache_key=row.cache_key,
            provider=row.provider,
            mode=row.mode,
            symbols=json.loads(row.symbols or "[]"),
            payload=json.loads(row.payload_json),
            created_at=row.created_at,
        )

    @staticmethod
    def _full_market_scan_job_from_row(row: FullMarketScanJobRow) -> FullMarketScanJobRecord:
        return FullMarketScanJobRecord(
            job_id=row.job_id,
            provider=row.provider,
            status=row.status,
            batch_size=row.batch_size,
            total_symbols=row.total_symbols,
            scanned_symbols=row.scanned_symbols,
            total_batches=row.total_batches,
            completed_batches=row.completed_batches,
            cards=row.cards,
            errors=row.errors,
            include_etfs=bool(row.include_etfs),
            sync_if_empty=bool(row.sync_if_empty),
            symbols=json.loads(row.symbols or "[]"),
            message=row.message or "",
            data_health=json.loads(row.data_health or "{}"),
            result_cache_key=row.result_cache_key,
            created_at=row.created_at,
            updated_at=row.updated_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )

    @staticmethod
    def _historical_backfill_job_from_row(
        row: HistoricalBackfillJobRow,
    ) -> HistoricalBackfillJobRecord:
        return HistoricalBackfillJobRecord(
            job_id=row.job_id,
            provider=row.provider,
            status=row.status,
            start_date=row.start_date,
            end_date=row.end_date,
            symbols=json.loads(row.symbols or "[]"),
            total_symbols=row.total_symbols,
            processed_symbols=row.processed_symbols,
            succeeded_symbols=row.succeeded_symbols,
            failed_symbols=row.failed_symbols,
            rows_written=row.rows_written,
            fundamental_rows_written=row.fundamental_rows_written,
            current_instrument=row.current_instrument,
            errors=json.loads(row.errors_json or "[]"),
            data_health=json.loads(row.data_health or "{}"),
            created_at=row.created_at,
            updated_at=row.updated_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )

    @staticmethod
    def _walk_forward_run_from_row(row: WalkForwardRunRow) -> WalkForwardRunRecord:
        return WalkForwardRunRecord(
            run_id=row.run_id,
            provider=row.provider,
            status=row.status,
            start_date=row.start_date,
            end_date=row.end_date,
            dataset_revision=row.dataset_revision,
            rebalance_step_sessions=row.rebalance_step_sessions,
            lookback_days=row.lookback_days,
            snapshot_count=row.snapshot_count,
            top_5_trade_count=row.top_5_trade_count,
            top_10_trade_count=row.top_10_trade_count,
            top_5_return_pct=float(row.top_5_return_pct),
            top_10_return_pct=float(row.top_10_return_pct),
            top_5_oos_trades=row.top_5_oos_trades,
            top_10_oos_trades=row.top_10_oos_trades,
            top_5_oos_gate=row.top_5_oos_gate,
            top_10_oos_gate=row.top_10_oos_gate,
            reproducibility_digest=row.reproducibility_digest,
            payload=json.loads(row.payload_json),
            data_health=json.loads(row.data_health),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _walk_forward_job_from_row(row: WalkForwardJobRow) -> WalkForwardJobRecord:
        return WalkForwardJobRecord(
            job_id=row.job_id,
            provider=row.provider,
            status=row.status,
            phase=row.phase,
            start_date=row.start_date,
            end_date=row.end_date,
            dataset_revision=row.dataset_revision,
            rebalance_step_sessions=row.rebalance_step_sessions,
            lookback_days=row.lookback_days,
            total_snapshots=row.total_snapshots,
            processed_snapshots=row.processed_snapshots,
            current_date=row.current_date,
            checkpoints=json.loads(row.checkpoints_json or "[]"),
            experiment_manifest=json.loads(row.experiment_manifest_json or "{}"),
            result_run_id=row.result_run_id,
            error=row.error,
            created_at=row.created_at,
            updated_at=row.updated_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )

    @staticmethod
    def _opportunity_snapshot_from_row(row: OpportunitySnapshotRow) -> OpportunitySnapshotRecord:
        return OpportunitySnapshotRecord(
            snapshot_id=row.snapshot_id,
            run_id=row.run_id,
            card_id=row.card_id,
            instrument_id=row.instrument_id,
            market=row.market,
            status=row.status,
            signal_date=row.signal_date,
            latest_close=row.latest_close,
            primary_strategy_id=row.primary_strategy_id,
            score=row.score,
            strategy_score=row.strategy_score,
            rank_score=row.rank_score,
            trigger_price=row.trigger_price,
            initial_stop=row.initial_stop,
            target_1=row.target_1,
            card=json.loads(row.card_json),
        )

    @staticmethod
    def _brief_run_from_row(row: BriefRunRow) -> BriefRunRecord:
        return BriefRunRecord(
            brief_id=row.brief_id,
            provider=row.provider,
            symbols=json.loads(row.symbols or "[]"),
            headline=row.headline,
            opportunity_count=row.opportunity_count,
            entry_watch_count=row.entry_watch_count,
            risk_alert_count=row.risk_alert_count,
            catalyst_count=row.catalyst_count,
            validation_count=row.validation_count,
            data_health=json.loads(row.data_health or "{}"),
            payload=json.loads(row.brief_json),
            created_at=row.created_at,
        )

    @staticmethod
    def _delivery_outbox_from_row(row: DeliveryOutboxRow) -> DeliveryOutboxRecord:
        return DeliveryOutboxRecord(
            delivery_id=row.delivery_id,
            brief_id=row.brief_id or None,
            channel=row.channel,
            recipient=row.recipient,
            subject=row.subject,
            markdown=row.markdown,
            payload=json.loads(row.payload_json or "{}"),
            status=row.status,
            created_at=row.created_at,
            updated_at=row.updated_at,
            sent_at=row.sent_at,
        )

    @staticmethod
    def _automation_scheduler_state_from_row(
        row: AutomationSchedulerStateRow,
    ) -> AutomationSchedulerStateRecord:
        try:
            settings = json.loads(row.settings_json or "{}")
        except json.JSONDecodeError:
            settings = {}
        return AutomationSchedulerStateRecord(
            enabled=row.enabled,
            settings=settings if isinstance(settings, dict) else {},
            updated_at=row.updated_at,
        )


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _instrument_tags(instrument) -> list[str]:
    tags = [instrument.asset_type, instrument.exchange]
    name = instrument.name
    if "ETF" in name.upper():
        tags.extend(["etf", "index_tool"])
    if "半导体" in name or "芯片" in name:
        tags.extend(["semiconductor", "chip"])
    if "科创" in name:
        tags.append("star_market")
    return tags


def _tradable_summary(rows: list[TradableInstrumentRow]) -> TradableCatalogSummary:
    exchanges: dict[str, int] = {}
    last_synced_at = None
    for row in rows:
        exchanges[row.exchange] = exchanges.get(row.exchange, 0) + 1
        if row.synced_at and (last_synced_at is None or row.synced_at > last_synced_at):
            last_synced_at = row.synced_at
    stock_count = sum(1 for row in rows if row.asset_type == "stock")
    etf_count = sum(1 for row in rows if row.asset_type == "etf")
    return TradableCatalogSummary(
        total_count=len(rows),
        stock_count=stock_count,
        etf_count=etf_count,
        other_count=len(rows) - stock_count - etf_count,
        exchanges=exchanges,
        last_synced_at=last_synced_at,
    )


def _matches_tradable_row(row: TradableInstrumentRow, query: str) -> bool:
    haystack = " ".join(
        [
            row.instrument_id,
            row.symbol,
            row.name,
            row.label,
            row.asset_type,
            row.exchange,
            row.tags,
            f"{row.symbol}.{row.exchange}",
        ]
    ).upper()
    return query in haystack


def _tradable_match_rank(row: TradableInstrumentRow, query: str) -> tuple[int, int, int, str]:
    symbol = row.symbol.upper()
    name = row.name.upper()
    label = row.label.upper()
    token = row.instrument_id.upper()
    exchange_label = f"{symbol}.{row.exchange}".upper()
    asset_rank = _asset_sort_rank(row.asset_type)
    if query in {symbol, exchange_label, token}:
        return (0, asset_rank, 0, symbol)
    if query in {name, label}:
        return (1, asset_rank, len(name), symbol)
    if symbol.startswith(query):
        return (2, asset_rank, len(symbol), symbol)
    if name.startswith(query):
        return (3, asset_rank, len(name), symbol)
    if label.startswith(query):
        return (4, asset_rank, len(label), symbol)
    if query in name:
        return (5, asset_rank, name.index(query), symbol)
    if query in label:
        return (6, asset_rank, label.index(query), symbol)
    return (9, asset_rank, len(label), symbol)


def _asset_sort_rank(asset_type: str) -> int:
    return {"etf": 0, "stock": 1}.get(asset_type, 2)


def _asset_browse_rank(asset_type: str) -> int:
    return {"stock": 0, "etf": 1}.get(asset_type, 2)


def _latest_revision_alias(model, identity_columns: tuple[str, ...]):
    ranked = select(
        model,
        func.row_number()
        .over(
            partition_by=tuple(getattr(model, key) for key in identity_columns),
            order_by=model.dataset_revision.desc(),
        )
        .label("revision_rank"),
    ).subquery()
    return aliased(model, ranked), ranked.c.revision_rank


def _sqlite_upsert_chunks(
    session: Session,
    model,
    records: list[dict[str, object]],
    index_elements: list[str],
    chunk_size: int = 400,
) -> None:
    if not records:
        return
    update_columns = [key for key in records[0] if key not in index_elements]
    for offset in range(0, len(records), chunk_size):
        statement = sqlite_insert(model).values(records[offset : offset + chunk_size])
        excluded = statement.excluded
        statement = statement.on_conflict_do_update(
            index_elements=[getattr(model, key) for key in index_elements],
            set_={key: getattr(excluded, key) for key in update_columns},
        )
        session.execute(statement)


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        result.append(normalized)
        seen.add(normalized)
    return result
