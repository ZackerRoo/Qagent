from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from qagent.domain.models import OpportunityCard
from qagent.market.universes import UniverseCreate, UniverseRecord, normalize_symbols
from qagent.storage.tables import (
    AlertRuleRow,
    BriefRunRow,
    DeliveryOutboxRow,
    FullMarketScanJobRow,
    OpportunitySnapshotRow,
    PositionRow,
    ScanResultCacheRow,
    ScanRunRow,
    TradableInstrumentRow,
    UniverseRow,
    WatchlistItemRow,
)


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

    def list_scan_runs(self, limit: int = 20) -> list[ScanRunRecord]:
        with self.session_factory() as session:
            rows = (
                session.query(ScanRunRow)
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

    def list_opportunity_snapshots(
        self,
        instrument_id: str | None = None,
        limit: int = 50,
    ) -> list[OpportunitySnapshotRecord]:
        with self.session_factory() as session:
            query = session.query(OpportunitySnapshotRow)
            if instrument_id:
                query = query.filter(OpportunitySnapshotRow.instrument_id == instrument_id)
            rows = (
                query.order_by(
                    OpportunitySnapshotRow.created_at.desc(),
                    OpportunitySnapshotRow.snapshot_id.desc(),
                )
                .limit(limit)
                .all()
            )
            return [self._opportunity_snapshot_from_row(row) for row in rows]

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

    def list_brief_runs(self, limit: int = 20) -> list[BriefRunRecord]:
        with self.session_factory() as session:
            rows = (
                session.query(BriefRunRow)
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
    ) -> list[DeliveryOutboxRecord]:
        with self.session_factory() as session:
            query = session.query(DeliveryOutboxRow)
            if status:
                query = query.filter(DeliveryOutboxRow.status == status)
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
