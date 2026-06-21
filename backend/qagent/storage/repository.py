from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from qagent.storage.tables import AlertRuleRow, PositionRow, WatchlistItemRow


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
