from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from qagent.storage.tables import PaperAccountSettingsRow, PaperTradeRow, utc_now


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


class PaperAccountSettings(BaseModel):
    account_id: str
    session_id: str
    label: str
    status: str
    initial_capital: Decimal
    allocation_per_trade_pct: Decimal
    max_positions: int
    transaction_cost_bps: Decimal
    slippage_bps: Decimal
    take_profit_pct: Decimal
    started_at: datetime


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
        notes: str = "",
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
                notes=notes,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._trade_from_row(row)

    def get_trade_by_source_snapshot_id(
        self,
        source_snapshot_id: str,
    ) -> PaperTradeRecord | None:
        with self.session_factory() as session:
            row = (
                session.query(PaperTradeRow)
                .filter(PaperTradeRow.source_snapshot_id == source_snapshot_id)
                .one_or_none()
            )
            return self._trade_from_row(row) if row is not None else None

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

    def delete_trade(self, trade_id: str) -> bool:
        with self.session_factory() as session:
            row = session.get(PaperTradeRow, trade_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def clear_trades(self) -> int:
        with self.session_factory() as session:
            count = session.query(PaperTradeRow).delete()
            session.commit()
            return int(count)

    def get_account_settings(self) -> PaperAccountSettings:
        with self.session_factory() as session:
            row = session.get(PaperAccountSettingsRow, "default")
            if row is None:
                return self._default_account_settings()
            return self._account_from_row(row)

    def start_account_session(
        self,
        *,
        label: str,
        initial_capital: Decimal,
        allocation_per_trade_pct: Decimal,
        max_positions: int,
        transaction_cost_bps: Decimal,
        slippage_bps: Decimal,
        take_profit_pct: Decimal,
    ) -> PaperAccountSettings:
        with self.session_factory() as session:
            now = utc_now()
            row = session.get(PaperAccountSettingsRow, "default")
            if row is None:
                row = PaperAccountSettingsRow(
                    account_id="default",
                    session_id=f"paper-session-{uuid4().hex[:12]}",
                    label=label,
                    status="active",
                    initial_capital=initial_capital,
                    allocation_per_trade_pct=allocation_per_trade_pct,
                    max_positions=max_positions,
                    transaction_cost_bps=transaction_cost_bps,
                    slippage_bps=slippage_bps,
                    take_profit_pct=take_profit_pct,
                    started_at=now,
                )
                session.add(row)
            else:
                row.session_id = f"paper-session-{uuid4().hex[:12]}"
                row.label = label
                row.status = "active"
                row.initial_capital = initial_capital
                row.allocation_per_trade_pct = allocation_per_trade_pct
                row.max_positions = max_positions
                row.transaction_cost_bps = transaction_cost_bps
                row.slippage_bps = slippage_bps
                row.take_profit_pct = take_profit_pct
                row.started_at = now
            session.commit()
            session.refresh(row)
            return self._account_from_row(row)

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

    @staticmethod
    def _default_account_settings() -> PaperAccountSettings:
        return PaperAccountSettings(
            account_id="default",
            session_id="paper-session-default",
            label="默认模拟盘",
            status="draft",
            initial_capital=Decimal("100000"),
            allocation_per_trade_pct=Decimal("10"),
            max_positions=5,
            transaction_cost_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            take_profit_pct=Decimal("100"),
            started_at=utc_now(),
        )

    @staticmethod
    def _account_from_row(row: PaperAccountSettingsRow) -> PaperAccountSettings:
        return PaperAccountSettings(
            account_id=row.account_id,
            session_id=row.session_id,
            label=row.label,
            status=row.status,
            initial_capital=row.initial_capital,
            allocation_per_trade_pct=row.allocation_per_trade_pct,
            max_positions=row.max_positions,
            transaction_cost_bps=row.transaction_cost_bps,
            slippage_bps=row.slippage_bps,
            take_profit_pct=row.take_profit_pct,
            started_at=row.started_at,
        )


def _float_or_none(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)
