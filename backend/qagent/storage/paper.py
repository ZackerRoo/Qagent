from datetime import date
from decimal import Decimal
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from qagent.storage.tables import PaperTradeRow


class PaperTradeRecord(BaseModel):
    trade_id: str
    source_snapshot_id: str
    provider: str
    instrument_id: str
    strategy_id: str | None
    status: str
    signal_date: date
    trigger_price: Decimal
    initial_stop: Decimal | None
    target_1: Decimal | None
    rank_score: Decimal | None
    entry_date: date | None
    entry_price: Decimal | None
    exit_date: date | None
    exit_price: Decimal | None
    latest_date: date | None
    latest_price: Decimal | None
    unrealized_return_pct: float | None
    realized_return_pct: float | None
    holding_days: int
    notes: str


class PaperTradingRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def create_trade(
        self,
        source_snapshot_id: str,
        provider: str,
        instrument_id: str,
        strategy_id: str | None,
        signal_date: date,
        trigger_price: Decimal,
        initial_stop: Decimal | None,
        target_1: Decimal | None,
        rank_score: Decimal | None = None,
    ) -> PaperTradeRecord:
        with self.session_factory() as session:
            existing = (
                session.query(PaperTradeRow)
                .filter(PaperTradeRow.source_snapshot_id == source_snapshot_id)
                .one_or_none()
            )
            if existing is not None:
                return self._trade_from_row(existing)
            row = PaperTradeRow(
                trade_id=f"paper-{uuid4().hex[:12]}",
                source_snapshot_id=source_snapshot_id,
                provider=provider,
                instrument_id=instrument_id,
                strategy_id=strategy_id,
                status="pending",
                signal_date=signal_date,
                trigger_price=trigger_price,
                initial_stop=initial_stop,
                target_1=target_1,
                rank_score=rank_score,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._trade_from_row(row)

    def list_trades(
        self,
        status: str | None = None,
        limit: int = 100,
    ) -> list[PaperTradeRecord]:
        with self.session_factory() as session:
            query = session.query(PaperTradeRow)
            if status:
                query = query.filter(PaperTradeRow.status == status)
            rows = (
                query.order_by(PaperTradeRow.created_at.desc(), PaperTradeRow.trade_id.desc())
                .limit(limit)
                .all()
            )
            return [self._trade_from_row(row) for row in rows]

    def update_trade(self, trade_id: str, **changes: object) -> PaperTradeRecord | None:
        with self.session_factory() as session:
            row = session.get(PaperTradeRow, trade_id)
            if row is None:
                return None
            for key, value in changes.items():
                setattr(row, key, value)
            session.commit()
            session.refresh(row)
            return self._trade_from_row(row)

    @staticmethod
    def _trade_from_row(row: PaperTradeRow) -> PaperTradeRecord:
        return PaperTradeRecord(
            trade_id=row.trade_id,
            source_snapshot_id=row.source_snapshot_id,
            provider=row.provider,
            instrument_id=row.instrument_id,
            strategy_id=row.strategy_id,
            status=row.status,
            signal_date=row.signal_date,
            trigger_price=row.trigger_price,
            initial_stop=row.initial_stop,
            target_1=row.target_1,
            rank_score=row.rank_score,
            entry_date=row.entry_date,
            entry_price=row.entry_price,
            exit_date=row.exit_date,
            exit_price=row.exit_price,
            latest_date=row.latest_date,
            latest_price=row.latest_price,
            unrealized_return_pct=_float_or_none(row.unrealized_return_pct),
            realized_return_pct=_float_or_none(row.realized_return_pct),
            holding_days=row.holding_days,
            notes=row.notes,
        )


def _float_or_none(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)
