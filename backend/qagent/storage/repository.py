import json
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from qagent.domain.models import OpportunityCard
from qagent.storage.tables import (
    AlertRuleRow,
    BriefRunRow,
    OpportunitySnapshotRow,
    PositionRow,
    ScanRunRow,
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


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))
