from datetime import date
from decimal import Decimal

import pandas as pd
from pydantic import BaseModel

from qagent.providers.base import MarketDataProvider
from qagent.storage.paper import PaperTradeRecord, PaperTradingRepository
from qagent.storage.repository import OpportunitySnapshotRecord


OPEN_STATUSES = {"pending", "open"}
CLOSED_STATUSES = {"target_1_hit", "stopped", "time_exit"}


class PaperSeedResult(BaseModel):
    scanned: int
    created: int
    skipped: int


class PaperTradingSummary(BaseModel):
    total: int
    pending: int
    open: int
    closed: int
    target_hit_count: int
    stopped_count: int
    time_exit_count: int
    win_rate: float | None
    average_realized_return_pct: float | None
    average_unrealized_return_pct: float | None


class PaperUpdateResult(BaseModel):
    summary: PaperTradingSummary
    trades: list[PaperTradeRecord]
    data_health: dict[str, str]


def seed_paper_trades_from_snapshots(
    repo: PaperTradingRepository,
    snapshots: list[OpportunitySnapshotRecord],
    provider: str,
) -> PaperSeedResult:
    created = 0
    skipped = 0
    existing = {trade.source_snapshot_id for trade in repo.list_trades(limit=1000)}
    for snapshot in snapshots:
        if snapshot.snapshot_id in existing:
            skipped += 1
            continue
        if snapshot.signal_date is None or snapshot.trigger_price is None:
            skipped += 1
            continue
        repo.create_trade(
            source_snapshot_id=snapshot.snapshot_id,
            provider=provider,
            instrument_id=snapshot.instrument_id,
            strategy_id=snapshot.primary_strategy_id,
            signal_date=snapshot.signal_date,
            trigger_price=snapshot.trigger_price,
            initial_stop=snapshot.initial_stop,
            target_1=snapshot.target_1,
            rank_score=snapshot.rank_score,
        )
        created += 1
    return PaperSeedResult(scanned=len(snapshots), created=created, skipped=skipped)


def update_paper_trades(
    repo: PaperTradingRepository,
    provider: MarketDataProvider,
    max_holding_days: int = 20,
    max_entry_wait_days: int = 10,
) -> PaperUpdateResult:
    trades = repo.list_trades(limit=1000)
    active = [trade for trade in trades if trade.status in OPEN_STATUSES]
    for trade in active:
        bars = provider.get_daily_bars(
            [trade.instrument_id],
            start=trade.signal_date,
            end=date(2100, 1, 1),
        )
        if bars.empty:
            continue
        updated = _evaluate_trade(trade, bars, max_holding_days, max_entry_wait_days)
        repo.update_trade(trade.trade_id, **updated)
    refreshed = repo.list_trades(limit=1000)
    provider_errors = getattr(provider, "last_errors", [])
    data_health = {
        "provider": provider.name,
        "trades": str(len(refreshed)),
        "active_checked": str(len(active)),
    }
    if provider_errors:
        data_health["errors"] = " | ".join(provider_errors[:3])
    return PaperUpdateResult(
        summary=summarize_paper_trades(refreshed),
        trades=refreshed,
        data_health=data_health,
    )


def summarize_paper_trades(trades: list[PaperTradeRecord]) -> PaperTradingSummary:
    closed = [trade for trade in trades if trade.status in CLOSED_STATUSES]
    winning = [
        trade
        for trade in closed
        if trade.realized_return_pct is not None and trade.realized_return_pct > 0
    ]
    realized = [
        trade.realized_return_pct
        for trade in closed
        if trade.realized_return_pct is not None
    ]
    unrealized = [
        trade.unrealized_return_pct
        for trade in trades
        if trade.status == "open" and trade.unrealized_return_pct is not None
    ]
    return PaperTradingSummary(
        total=len(trades),
        pending=sum(1 for trade in trades if trade.status == "pending"),
        open=sum(1 for trade in trades if trade.status == "open"),
        closed=len(closed),
        target_hit_count=sum(1 for trade in trades if trade.status == "target_1_hit"),
        stopped_count=sum(1 for trade in trades if trade.status == "stopped"),
        time_exit_count=sum(1 for trade in trades if trade.status == "time_exit"),
        win_rate=round(len(winning) / len(closed), 4) if closed else None,
        average_realized_return_pct=round(sum(realized) / len(realized), 4)
        if realized
        else None,
        average_unrealized_return_pct=round(sum(unrealized) / len(unrealized), 4)
        if unrealized
        else None,
    )


def _evaluate_trade(
    trade: PaperTradeRecord,
    bars: pd.DataFrame,
    max_holding_days: int,
    max_entry_wait_days: int,
) -> dict[str, object]:
    ordered = bars.sort_values("trade_date").reset_index(drop=True)
    if pd.api.types.is_datetime64_any_dtype(ordered["trade_date"]):
        ordered["trade_date"] = ordered["trade_date"].dt.date
    entry_date = trade.entry_date
    entry_price = trade.entry_price
    status = trade.status
    notes = trade.notes

    for _, row in ordered.iterrows():
        trade_date = row["trade_date"]
        high = Decimal(str(row["high"]))
        low = Decimal(str(row["low"]))
        close = Decimal(str(row["close"]))
        if status == "pending":
            wait_days = max((trade_date - trade.signal_date).days, 0)
            if high >= trade.trigger_price:
                status = "open"
                entry_date = trade_date
                entry_price = trade.trigger_price
                notes = "Entry triggered by daily high crossing trigger."
            elif wait_days > max_entry_wait_days:
                return {
                    "status": "time_exit",
                    "latest_date": trade_date,
                    "latest_price": close,
                    "exit_date": trade_date,
                    "exit_price": close,
                    "realized_return_pct": Decimal("0"),
                    "holding_days": 0,
                    "notes": "Entry trigger expired before execution.",
                }
            else:
                continue

        if status == "open" and entry_date is not None and entry_price is not None:
            holding_days = max((trade_date - entry_date).days, 0)
            if trade.initial_stop is not None and low <= trade.initial_stop:
                return _closed_update(
                    status="stopped",
                    entry_date=entry_date,
                    entry_price=entry_price,
                    exit_date=trade_date,
                    exit_price=trade.initial_stop,
                    latest_price=close,
                    holding_days=holding_days,
                    notes="Initial stop touched.",
                )
            if trade.target_1 is not None and high >= trade.target_1:
                return _closed_update(
                    status="target_1_hit",
                    entry_date=entry_date,
                    entry_price=entry_price,
                    exit_date=trade_date,
                    exit_price=trade.target_1,
                    latest_price=close,
                    holding_days=holding_days,
                    notes="Target 1 touched.",
                )
            if holding_days >= max_holding_days:
                return _closed_update(
                    status="time_exit",
                    entry_date=entry_date,
                    entry_price=entry_price,
                    exit_date=trade_date,
                    exit_price=close,
                    latest_price=close,
                    holding_days=holding_days,
                    notes="Maximum holding window reached.",
                )

    latest = ordered.iloc[-1]
    latest_date = latest["trade_date"]
    latest_price = Decimal(str(latest["close"]))
    if status == "open" and entry_date is not None and entry_price is not None:
        return {
            "status": "open",
            "entry_date": entry_date,
            "entry_price": entry_price,
            "latest_date": latest_date,
            "latest_price": latest_price,
            "unrealized_return_pct": Decimal(str(_return_pct(entry_price, latest_price))),
            "holding_days": max((latest_date - entry_date).days, 0),
            "notes": notes,
        }
    return {
        "status": "pending",
        "latest_date": latest_date,
        "latest_price": latest_price,
        "holding_days": 0,
        "notes": notes,
    }


def _closed_update(
    status: str,
    entry_date: date,
    entry_price: Decimal,
    exit_date: date,
    exit_price: Decimal,
    latest_price: Decimal,
    holding_days: int,
    notes: str,
) -> dict[str, object]:
    return {
        "status": status,
        "entry_date": entry_date,
        "entry_price": entry_price,
        "exit_date": exit_date,
        "exit_price": exit_price,
        "latest_date": exit_date,
        "latest_price": latest_price,
        "realized_return_pct": Decimal(str(_return_pct(entry_price, exit_price))),
        "unrealized_return_pct": None,
        "holding_days": holding_days,
        "notes": notes,
    }


def _return_pct(entry_price: Decimal, exit_price: Decimal) -> float:
    if entry_price <= 0:
        return 0
    return round(float((exit_price / entry_price - Decimal("1")) * Decimal("100")), 4)
