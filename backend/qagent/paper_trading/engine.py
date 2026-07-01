from collections import defaultdict
from datetime import date, datetime, time
from decimal import Decimal, ROUND_DOWN
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import BaseModel

from qagent.providers.base import MarketDataProvider
from qagent.storage.paper import PaperTradeRecord, PaperTradingRepository
from qagent.storage.repository import OpportunitySnapshotRecord


OPEN_STATUSES = {"pending", "open"}
CLOSED_STATUSES = {"target_1_hit", "stopped", "time_exit"}
A_SHARE_TZ = ZoneInfo("Asia/Shanghai")
A_SHARE_MORNING_START = time(9, 30)
A_SHARE_MORNING_END = time(11, 30)
A_SHARE_AFTERNOON_START = time(13, 0)
A_SHARE_AFTERNOON_END = time(15, 0)


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


class PaperLedgerSummary(BaseModel):
    initial_capital: Decimal
    allocation_per_trade_pct: float
    allocation_per_trade: Decimal
    max_positions: int
    total_trades: int
    pending_trades: int
    open_trades: int
    closed_trades: int
    target_hit_count: int
    stopped_count: int
    time_exit_count: int
    planned_capital: Decimal
    allocated_capital: Decimal
    market_value: Decimal
    cash_available: Decimal
    total_equity: Decimal
    total_pnl: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_return_pct: float
    open_exposure_pct: float
    win_rate: float | None
    average_return_pct: float | None
    best_return_pct: float | None
    worst_return_pct: float | None
    max_drawdown_pct: float
    total_fees: Decimal
    total_slippage: Decimal
    turnover: Decimal
    transaction_cost_bps: float
    slippage_bps: float
    take_profit_pct: float


class PaperLedgerPoint(BaseModel):
    date: date
    equity: Decimal
    pnl: Decimal
    drawdown_pct: float
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    event_count: int


class PaperLedgerItem(BaseModel):
    trade_id: str
    instrument_id: str
    strategy_id: str | None
    status: str
    outcome: str
    signal_date: date
    entry_date: date | None
    exit_date: date | None
    latest_date: date | None
    trigger_price: Decimal
    entry_price: Decimal | None
    exit_price: Decimal | None
    latest_price: Decimal | None
    capital_allocated: Decimal
    shares: Decimal
    market_value: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_pnl: Decimal
    return_pct: float | None
    risk_pct: float | None
    reward_pct: float | None
    holding_days: int
    notes: str


class PaperLedgerTransaction(BaseModel):
    transaction_id: str
    trade_id: str
    instrument_id: str
    action: str
    side: str
    trade_date: date
    price: Decimal
    shares: Decimal
    gross_amount: Decimal
    fee: Decimal
    slippage: Decimal
    cash_flow: Decimal
    cash_balance: Decimal
    notes: str


class PaperLedgerPosition(BaseModel):
    trade_id: str
    instrument_id: str
    strategy_id: str | None
    entry_date: date
    latest_date: date | None
    shares: Decimal
    cost_basis: Decimal
    latest_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    return_pct: float
    weight_pct: float


class PaperLedger(BaseModel):
    summary: PaperLedgerSummary
    curve: list[PaperLedgerPoint]
    items: list[PaperLedgerItem]
    transactions: list[PaperLedgerTransaction]
    positions: list[PaperLedgerPosition]
    data_health: dict[str, str]


class PaperValidationSummary(BaseModel):
    total_trades: int
    triggered_trades: int
    pending_trades: int
    open_trades: int
    closed_trades: int
    target_hit_count: int
    stopped_count: int
    time_exit_count: int
    primary_window_days: int
    win_rate: float | None
    average_return_pct: float | None
    total_return_pct: float
    max_drawdown_pct: float
    verdict: str
    headline: str


class PaperValidationWindow(BaseModel):
    window_days: int
    eligible_trades: int
    evaluated_trades: int
    pending_trades: int
    positive_trades: int
    negative_trades: int
    win_rate: float | None
    average_return_pct: float | None
    total_pnl: Decimal
    total_return_pct: float | None
    max_drawdown_pct: float
    target_hit_count: int
    stopped_count: int
    time_exit_count: int


class PaperValidationItem(BaseModel):
    trade_id: str
    instrument_id: str
    strategy_id: str | None
    status: str
    validation_state: str
    signal_date: date
    entry_date: date | None
    exit_date: date | None
    latest_date: date | None
    days_since_signal: int
    holding_days: int
    return_pct: float | None
    pnl: Decimal
    capital_allocated: Decimal
    outcome: str
    next_action: str


class PaperValidationSampleAge(BaseModel):
    average_days_since_signal: float
    newest_days_since_signal: int
    oldest_days_since_signal: int
    mature_5d: int
    mature_10d: int
    mature_20d: int
    pending_5d: int
    pending_10d: int
    pending_20d: int
    days_to_next_5d: int | None
    days_to_next_10d: int | None
    days_to_next_20d: int | None


class PaperValidationBatch(BaseModel):
    batch_id: str
    batch_date: date
    age_days: int
    total_trades: int
    triggered_trades: int
    pending_trades: int
    open_trades: int
    closed_trades: int
    win_rate: float | None
    average_return_pct: float | None
    total_pnl: Decimal
    total_return_pct: float | None
    max_drawdown_pct: float
    top_instruments: list[str]
    windows: list[PaperValidationWindow]


class PaperValidationCredibility(BaseModel):
    score: float
    level: str
    summary: str
    warnings: list[str]
    evidence: list[str]
    concentration_pct: float | None


class PaperValidationResult(BaseModel):
    summary: PaperValidationSummary
    windows: list[PaperValidationWindow]
    sample_age: PaperValidationSampleAge
    batches: list[PaperValidationBatch]
    credibility: PaperValidationCredibility
    items: list[PaperValidationItem]
    curve: list[PaperLedgerPoint]
    data_health: dict[str, str]


def seed_paper_trades_from_snapshots(
    repo: PaperTradingRepository,
    snapshots: list[OpportunitySnapshotRecord],
    provider: str,
    max_created: int | None = None,
    max_signal_age_days: int | None = 0,
    as_of: datetime | None = None,
) -> PaperSeedResult:
    created = 0
    skipped = 0
    existing = {trade.source_snapshot_id for trade in repo.list_trades(limit=1000)}
    current_date = _a_share_local_datetime(as_of).date()
    for snapshot in snapshots:
        if max_created is not None and created >= max_created:
            skipped += 1
            continue
        if snapshot.snapshot_id in existing:
            skipped += 1
            continue
        if snapshot.signal_date is None or snapshot.trigger_price is None:
            skipped += 1
            continue
        if (
            max_signal_age_days is not None
            and (current_date - snapshot.signal_date).days > max_signal_age_days
        ):
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
    as_of: datetime | None = None,
) -> PaperUpdateResult:
    trades = repo.list_trades(limit=1000)
    active = [trade for trade in trades if trade.status in OPEN_STATUSES]
    execution_time = _a_share_local_datetime(as_of)
    execution_session = _a_share_execution_session(execution_time)
    fills_deferred = 0
    for trade in active:
        bars = provider.get_daily_bars(
            [trade.instrument_id],
            start=trade.signal_date,
            end=date(2100, 1, 1),
        )
        if bars.empty:
            continue
        updated, deferred = _evaluate_trade(
            trade,
            bars,
            max_holding_days,
            max_entry_wait_days,
            as_of=execution_time,
        )
        fills_deferred += deferred
        repo.update_trade(trade.trade_id, **updated)
    refreshed = repo.list_trades(limit=1000)
    provider_errors = getattr(provider, "last_errors", [])
    data_health = {
        "provider": provider.name,
        "trades": str(len(refreshed)),
        "active_checked": str(len(active)),
        **paper_execution_data_health(
            as_of=execution_time,
            fills_deferred=fills_deferred,
            session=execution_session,
        ),
    }
    if provider_errors:
        data_health["errors"] = " | ".join(provider_errors[:3])
    return PaperUpdateResult(
        summary=summarize_paper_trades(refreshed),
        trades=refreshed,
        data_health=data_health,
    )


def paper_execution_data_health(
    as_of: datetime | None = None,
    *,
    fills_deferred: int = 0,
    session: str | None = None,
) -> dict[str, str]:
    execution_time = _a_share_local_datetime(as_of)
    return {
        "paper_execution_session": session or _a_share_execution_session(execution_time),
        "paper_execution_fills_deferred": str(fills_deferred),
    }


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


def build_paper_ledger(
    trades: list[PaperTradeRecord],
    initial_capital: Decimal = Decimal("100000"),
    allocation_per_trade_pct: Decimal = Decimal("10"),
    max_positions: int = 5,
    transaction_cost_bps: Decimal = Decimal("0"),
    slippage_bps: Decimal = Decimal("0"),
    take_profit_pct: Decimal = Decimal("100"),
) -> PaperLedger:
    if initial_capital <= 0:
        raise ValueError("initial_capital must be greater than zero")
    if allocation_per_trade_pct <= 0 or allocation_per_trade_pct > 100:
        raise ValueError("allocation_per_trade_pct must be between 0 and 100")
    if max_positions <= 0:
        raise ValueError("max_positions must be greater than zero")
    if transaction_cost_bps < 0 or slippage_bps < 0:
        raise ValueError("transaction_cost_bps and slippage_bps must be non-negative")
    if take_profit_pct <= 0 or take_profit_pct > 100:
        raise ValueError("take_profit_pct must be between 0 and 100")

    allocation_per_trade = _money(initial_capital * allocation_per_trade_pct / Decimal("100"))
    items: list[PaperLedgerItem] = []
    planned_capital = Decimal("0")

    for trade in trades:
        item = _ledger_item(trade, allocation_per_trade)
        items.append(item)
        planned_capital += allocation_per_trade if trade.status == "pending" else Decimal("0")

    account = _build_account_ledger(
        trades=trades,
        initial_capital=initial_capital,
        allocation_per_trade=allocation_per_trade,
        max_positions=max_positions,
        transaction_cost_bps=transaction_cost_bps,
        slippage_bps=slippage_bps,
        take_profit_pct=take_profit_pct,
    )

    returns = [item.return_pct for item in items if item.return_pct is not None]
    closed_returns = [
        item.return_pct
        for item in items
        if item.status in CLOSED_STATUSES and item.return_pct is not None
    ]
    max_drawdown_pct = min((point.drawdown_pct for point in account["curve"]), default=0.0)

    return PaperLedger(
        summary=PaperLedgerSummary(
            initial_capital=_money(initial_capital),
            allocation_per_trade_pct=round(float(allocation_per_trade_pct), 4),
            allocation_per_trade=allocation_per_trade,
            max_positions=max_positions,
            total_trades=len(trades),
            pending_trades=sum(1 for trade in trades if trade.status == "pending"),
            open_trades=sum(1 for trade in trades if trade.status == "open"),
            closed_trades=sum(1 for trade in trades if trade.status in CLOSED_STATUSES),
            target_hit_count=sum(1 for trade in trades if trade.status == "target_1_hit"),
            stopped_count=sum(1 for trade in trades if trade.status == "stopped"),
            time_exit_count=sum(1 for trade in trades if trade.status == "time_exit"),
            planned_capital=_money(planned_capital),
            allocated_capital=account["allocated_capital"],
            market_value=account["market_value"],
            cash_available=account["cash_available"],
            total_equity=account["total_equity"],
            total_pnl=account["total_pnl"],
            realized_pnl=account["realized_pnl"],
            unrealized_pnl=account["unrealized_pnl"],
            total_return_pct=_pct(account["total_pnl"], initial_capital),
            open_exposure_pct=_pct(account["market_value"], account["total_equity"]),
            win_rate=round(
                sum(1 for value in closed_returns if value > 0) / len(closed_returns),
                4,
            )
            if closed_returns
            else None,
            average_return_pct=round(sum(returns) / len(returns), 4) if returns else None,
            best_return_pct=round(max(returns), 4) if returns else None,
            worst_return_pct=round(min(returns), 4) if returns else None,
            max_drawdown_pct=max_drawdown_pct,
            total_fees=account["total_fees"],
            total_slippage=account["total_slippage"],
            turnover=account["turnover"],
            transaction_cost_bps=round(float(transaction_cost_bps), 4),
            slippage_bps=round(float(slippage_bps), 4),
            take_profit_pct=round(float(take_profit_pct), 4),
        ),
        curve=account["curve"],
        items=items,
        transactions=account["transactions"],
        positions=account["positions"],
        data_health={
            "ledger_method": "chronological_cash_ledger",
            "allocation_per_trade_pct": str(allocation_per_trade_pct),
            "max_positions": str(max_positions),
            "transaction_cost_bps": str(transaction_cost_bps),
            "slippage_bps": str(slippage_bps),
            "take_profit_pct": str(take_profit_pct),
            "price_source": "paper_trade_latest_fields",
        },
    )


def build_paper_validation(
    trades: list[PaperTradeRecord],
    ledger: PaperLedger,
    windows: tuple[int, ...] = (5, 10, 20),
    as_of: date | None = None,
) -> PaperValidationResult:
    if not windows:
        raise ValueError("windows must not be empty")
    as_of = as_of or _validation_as_of(trades)
    ledger_items = {item.trade_id: item for item in ledger.items}
    items = [
        _validation_item(
            trade=trade,
            ledger_item=ledger_items.get(trade.trade_id),
            allocation_per_trade=ledger.summary.allocation_per_trade,
            as_of=as_of,
        )
        for trade in trades
    ]
    window_results = [
        _validation_window(
            items=items,
            window_days=window,
            allocation_per_trade=ledger.summary.allocation_per_trade,
            max_drawdown_pct=ledger.summary.max_drawdown_pct,
        )
        for window in windows
    ]
    sample_age = _validation_sample_age(items, windows)
    batches = _validation_batches(
        items=items,
        windows=windows,
        allocation_per_trade=ledger.summary.allocation_per_trade,
    )
    primary_window = window_results[-1]
    verdict = _validation_verdict(
        total_trades=len(items),
        evaluated_trades=primary_window.evaluated_trades,
        total_return_pct=ledger.summary.total_return_pct,
        max_drawdown_pct=ledger.summary.max_drawdown_pct,
    )
    credibility = _validation_credibility(
        items=items,
        sample_age=sample_age,
        primary_window=primary_window,
        total_return_pct=ledger.summary.total_return_pct,
        max_drawdown_pct=ledger.summary.max_drawdown_pct,
    )
    return PaperValidationResult(
        summary=PaperValidationSummary(
            total_trades=len(items),
            triggered_trades=sum(1 for item in items if item.entry_date is not None),
            pending_trades=sum(1 for trade in trades if trade.status == "pending"),
            open_trades=sum(1 for trade in trades if trade.status == "open"),
            closed_trades=sum(1 for trade in trades if trade.status in CLOSED_STATUSES),
            target_hit_count=ledger.summary.target_hit_count,
            stopped_count=ledger.summary.stopped_count,
            time_exit_count=ledger.summary.time_exit_count,
            primary_window_days=windows[-1],
            win_rate=ledger.summary.win_rate,
            average_return_pct=ledger.summary.average_return_pct,
            total_return_pct=ledger.summary.total_return_pct,
            max_drawdown_pct=ledger.summary.max_drawdown_pct,
            verdict=verdict,
            headline=_validation_headline(verdict, primary_window, ledger.summary.total_return_pct),
        ),
        windows=window_results,
        sample_age=sample_age,
        batches=batches,
        credibility=credibility,
        items=items,
        curve=ledger.curve,
        data_health={
            **ledger.data_health,
            "validation_windows": ",".join(str(window) for window in windows),
            "validation_items": str(len(items)),
            "validation_batches": str(len(batches)),
            "validation_credibility": credibility.level,
            "validation_primary_window": str(windows[-1]),
        },
    )


def _build_account_ledger(
    trades: list[PaperTradeRecord],
    initial_capital: Decimal,
    allocation_per_trade: Decimal,
    max_positions: int,
    transaction_cost_bps: Decimal,
    slippage_bps: Decimal,
    take_profit_pct: Decimal,
) -> dict[str, object]:
    fee_rate = _bps_rate(transaction_cost_bps)
    slippage_rate = _bps_rate(slippage_bps)
    active_lots: list[dict[str, object]] = []
    transactions: list[PaperLedgerTransaction] = []
    positions: list[PaperLedgerPosition] = []
    cash = initial_capital
    total_fees = Decimal("0")
    total_slippage = Decimal("0")
    turnover = Decimal("0")
    realized_pnl = Decimal("0")
    dates = {
        trade.signal_date
        for trade in trades
        if trade.signal_date is not None
    }
    for trade in trades:
        if trade.entry_date is not None:
            dates.add(trade.entry_date)
        if trade.exit_date is not None:
            dates.add(trade.exit_date)
        if trade.latest_date is not None:
            dates.add(trade.latest_date)
    if not dates:
        dates.add(date.today())

    entries_by_date: dict[date, list[PaperTradeRecord]] = {}
    for trade in sorted(
        trades,
        key=lambda item: (item.entry_date or item.signal_date, item.trade_id),
    ):
        if trade.entry_date is None or trade.entry_price is None:
            continue
        if trade.status not in {"open", *CLOSED_STATUSES}:
            continue
        entries_by_date.setdefault(trade.entry_date, []).append(trade)

    curve = [
        PaperLedgerPoint(
            date=min(dates),
            equity=_money(initial_capital),
            pnl=Decimal("0.00"),
            drawdown_pct=0.0,
            realized_pnl=Decimal("0.00"),
            unrealized_pnl=Decimal("0.00"),
            event_count=0,
        )
    ]
    high_watermark = initial_capital

    for current_date in sorted(dates):
        event_count = 0
        exiting_lots = [
            lot
            for lot in active_lots
            if lot["exit_date"] == current_date and lot["status"] in CLOSED_STATUSES
        ]
        for lot in exiting_lots:
            generated = _sell_lot_transactions(
                lot=lot,
                cash=cash,
                fee_rate=fee_rate,
                slippage_rate=slippage_rate,
                take_profit_pct=take_profit_pct,
            )
            for transaction, pnl, fee, slippage, gross in generated:
                cash = transaction.cash_balance
                realized_pnl += pnl
                total_fees += fee
                total_slippage += slippage
                turnover += gross
                transactions.append(transaction)
                event_count += 1
            active_lots.remove(lot)

        for trade in entries_by_date.get(current_date, []):
            if len(active_lots) >= max_positions:
                continue
            buy = _buy_lot(
                trade=trade,
                cash=cash,
                allocation_per_trade=allocation_per_trade,
                fee_rate=fee_rate,
                slippage_rate=slippage_rate,
            )
            if buy is None:
                continue
            lot, transaction, fee, slippage, gross = buy
            cash = transaction.cash_balance
            total_fees += fee
            total_slippage += slippage
            turnover += gross
            transactions.append(transaction)
            active_lots.append(lot)
            event_count += 1

        market_value, unrealized_pnl = _active_lot_market_value(active_lots, current_date)
        equity = cash + market_value
        high_watermark = max(high_watermark, equity)
        drawdown_pct = _pct(equity - high_watermark, high_watermark)
        if current_date != curve[0].date or event_count:
            curve.append(
                PaperLedgerPoint(
                    date=current_date,
                    equity=_money(equity),
                    pnl=_money(equity - initial_capital),
                    drawdown_pct=drawdown_pct,
                    realized_pnl=_money(realized_pnl),
                    unrealized_pnl=_money(unrealized_pnl),
                    event_count=event_count,
                )
            )

    final_market_value, final_unrealized_pnl = _active_lot_market_value(
        active_lots,
        max(dates),
    )
    total_equity = _money(cash + final_market_value)
    for lot in active_lots:
        latest_price = _lot_mark_price(lot, max(dates))
        market_value = _money(Decimal(str(lot["shares"])) * latest_price)
        cost_basis = Decimal(str(lot["cost_basis"]))
        positions.append(
            PaperLedgerPosition(
                trade_id=str(lot["trade_id"]),
                instrument_id=str(lot["instrument_id"]),
                strategy_id=lot["strategy_id"] if isinstance(lot["strategy_id"], str) else None,
                entry_date=lot["entry_date"],
                latest_date=lot["latest_date"],
                shares=Decimal(str(lot["shares"])),
                cost_basis=_money(cost_basis),
                latest_price=latest_price,
                market_value=market_value,
                unrealized_pnl=_money(market_value - cost_basis),
                return_pct=_pct(market_value - cost_basis, cost_basis),
                weight_pct=_pct(market_value, total_equity),
            )
        )

    return {
        "allocated_capital": _money(sum((position.cost_basis for position in positions), Decimal("0"))),
        "market_value": _money(final_market_value),
        "cash_available": _money(cash),
        "total_equity": total_equity,
        "total_pnl": _money(total_equity - initial_capital),
        "realized_pnl": _money(realized_pnl),
        "unrealized_pnl": _money(final_unrealized_pnl),
        "total_fees": _money(total_fees),
        "total_slippage": _money(total_slippage),
        "turnover": _money(turnover),
        "curve": curve,
        "transactions": transactions,
        "positions": positions,
    }


def _validation_as_of(trades: list[PaperTradeRecord]) -> date:
    dates = [
        value
        for trade in trades
        for value in (trade.latest_date, trade.exit_date, trade.entry_date, trade.signal_date)
        if value is not None
    ]
    return max(dates) if dates else date.today()


def _validation_item(
    trade: PaperTradeRecord,
    ledger_item: PaperLedgerItem | None,
    allocation_per_trade: Decimal,
    as_of: date,
) -> PaperValidationItem:
    return_pct = (
        trade.realized_return_pct
        if trade.realized_return_pct is not None
        else trade.unrealized_return_pct
    )
    capital_allocated = allocation_per_trade if trade.entry_date is not None else Decimal("0")
    pnl = Decimal("0")
    outcome = _outcome_label(trade.status, return_pct)
    if ledger_item is not None:
        pnl = ledger_item.total_pnl
        outcome = ledger_item.outcome
        if ledger_item.return_pct is not None:
            return_pct = ledger_item.return_pct
    state = _validation_state(trade)
    return PaperValidationItem(
        trade_id=trade.trade_id,
        instrument_id=trade.instrument_id,
        strategy_id=trade.strategy_id,
        status=trade.status,
        validation_state=state,
        signal_date=trade.signal_date,
        entry_date=trade.entry_date,
        exit_date=trade.exit_date,
        latest_date=trade.latest_date,
        days_since_signal=max((as_of - trade.signal_date).days, 0),
        holding_days=trade.holding_days,
        return_pct=return_pct,
        pnl=_money(pnl),
        capital_allocated=_money(capital_allocated),
        outcome=outcome,
        next_action=_validation_next_action(state, return_pct),
    )


def _validation_window(
    items: list[PaperValidationItem],
    window_days: int,
    allocation_per_trade: Decimal,
    max_drawdown_pct: float,
) -> PaperValidationWindow:
    evaluated = [
        item
        for item in items
        if item.status in CLOSED_STATUSES or item.days_since_signal >= window_days
    ]
    returns = [item.return_pct if item.return_pct is not None else 0.0 for item in evaluated]
    total_pnl = sum((item.pnl for item in evaluated), Decimal("0"))
    denominator = allocation_per_trade * Decimal(str(len(evaluated)))
    return PaperValidationWindow(
        window_days=window_days,
        eligible_trades=len(items),
        evaluated_trades=len(evaluated),
        pending_trades=len(items) - len(evaluated),
        positive_trades=sum(1 for value in returns if value > 0),
        negative_trades=sum(1 for value in returns if value < 0),
        win_rate=round(sum(1 for value in returns if value > 0) / len(returns), 4)
        if returns
        else None,
        average_return_pct=round(sum(returns) / len(returns), 4) if returns else None,
        total_pnl=_money(total_pnl),
        total_return_pct=_pct(total_pnl, denominator) if denominator > 0 else None,
        max_drawdown_pct=max_drawdown_pct,
        target_hit_count=sum(1 for item in evaluated if item.status == "target_1_hit"),
        stopped_count=sum(1 for item in evaluated if item.status == "stopped"),
        time_exit_count=sum(1 for item in evaluated if item.status == "time_exit"),
    )


def _validation_sample_age(
    items: list[PaperValidationItem],
    windows: tuple[int, ...],
) -> PaperValidationSampleAge:
    if not items:
        return PaperValidationSampleAge(
            average_days_since_signal=0.0,
            newest_days_since_signal=0,
            oldest_days_since_signal=0,
            mature_5d=0,
            mature_10d=0,
            mature_20d=0,
            pending_5d=0,
            pending_10d=0,
            pending_20d=0,
            days_to_next_5d=None,
            days_to_next_10d=None,
            days_to_next_20d=None,
        )
    ages = [item.days_since_signal for item in items]
    return PaperValidationSampleAge(
        average_days_since_signal=round(sum(ages) / len(ages), 2),
        newest_days_since_signal=min(ages),
        oldest_days_since_signal=max(ages),
        mature_5d=_mature_count(items, 5),
        mature_10d=_mature_count(items, 10),
        mature_20d=_mature_count(items, 20),
        pending_5d=len(items) - _mature_count(items, 5),
        pending_10d=len(items) - _mature_count(items, 10),
        pending_20d=len(items) - _mature_count(items, 20),
        days_to_next_5d=_days_to_next_mature(items, 5),
        days_to_next_10d=_days_to_next_mature(items, 10),
        days_to_next_20d=_days_to_next_mature(items, 20),
    )


def _validation_batches(
    items: list[PaperValidationItem],
    windows: tuple[int, ...],
    allocation_per_trade: Decimal,
) -> list[PaperValidationBatch]:
    grouped: defaultdict[date, list[PaperValidationItem]] = defaultdict(list)
    for item in items:
        grouped[item.signal_date].append(item)
    batches: list[PaperValidationBatch] = []
    for batch_date, batch_items in sorted(grouped.items(), reverse=True):
        returns = [
            item.return_pct
            for item in batch_items
            if item.return_pct is not None and (
                item.status in CLOSED_STATUSES or item.days_since_signal >= windows[-1]
            )
        ]
        total_pnl = sum((item.pnl for item in batch_items), Decimal("0"))
        denominator = allocation_per_trade * Decimal(str(max(len(batch_items), 1)))
        batch_windows = [
            _validation_window(
                items=batch_items,
                window_days=window,
                allocation_per_trade=allocation_per_trade,
                max_drawdown_pct=_items_drawdown(batch_items),
            )
            for window in windows
        ]
        batches.append(
            PaperValidationBatch(
                batch_id=f"paper-batch-{batch_date:%Y%m%d}",
                batch_date=batch_date,
                age_days=max((item.days_since_signal for item in batch_items), default=0),
                total_trades=len(batch_items),
                triggered_trades=sum(1 for item in batch_items if item.entry_date is not None),
                pending_trades=sum(1 for item in batch_items if item.status == "pending"),
                open_trades=sum(1 for item in batch_items if item.status == "open"),
                closed_trades=sum(1 for item in batch_items if item.status in CLOSED_STATUSES),
                win_rate=round(sum(1 for value in returns if value > 0) / len(returns), 4)
                if returns
                else None,
                average_return_pct=round(sum(returns) / len(returns), 4) if returns else None,
                total_pnl=_money(total_pnl),
                total_return_pct=_pct(total_pnl, denominator) if denominator > 0 else None,
                max_drawdown_pct=_items_drawdown(batch_items),
                top_instruments=[item.instrument_id for item in batch_items[:5]],
                windows=batch_windows,
            )
        )
    return batches


def _validation_credibility(
    items: list[PaperValidationItem],
    sample_age: PaperValidationSampleAge,
    primary_window: PaperValidationWindow,
    total_return_pct: float,
    max_drawdown_pct: float,
) -> PaperValidationCredibility:
    if not items:
        return PaperValidationCredibility(
            score=0.0,
            level="insufficient",
            summary="还没有模拟样本，不能判断推荐有效性。",
            warnings=["请先把今日推荐加入模拟盘。"],
            evidence=[],
            concentration_pct=None,
        )

    closed_count = sum(1 for item in items if item.status in CLOSED_STATUSES)
    sample_score = min(len(items) / 20, 1) * 0.25
    closed_score = min(closed_count / 10, 1) * 0.25
    maturity_score = min(sample_age.mature_10d / max(len(items), 1), 1) * 0.2
    drawdown_score = max(0.0, min(1.0, (12 + max_drawdown_pct) / 12)) * 0.15
    concentration_pct = _pnl_concentration(items)
    concentration_score = (1 - min((concentration_pct or 0) / 100, 1)) * 0.15
    score = round(sample_score + closed_score + maturity_score + drawdown_score + concentration_score, 4)
    warnings: list[str] = []
    if len(items) < 20:
        warnings.append("样本少于 20 笔，先看方向，不宜过度相信胜率。")
    if sample_age.mature_10d < 5:
        warnings.append("10日成熟样本少于 5 笔，短期胜率还在积累。")
    if closed_count < 5:
        warnings.append("闭环交易少于 5 笔，止盈/止损统计还不稳定。")
    if concentration_pct is not None and concentration_pct > 60:
        warnings.append("收益集中度偏高，可能主要由少数标的贡献。")
    if max_drawdown_pct <= -8:
        warnings.append("最大回撤超过 8%，需要降低仓位或复核策略。")
    if score >= 0.75:
        level = "high"
    elif score >= 0.5:
        level = "medium"
    elif score > 0:
        level = "low"
    else:
        level = "insufficient"
    if total_return_pct > 0 and warnings:
        summary = "当前收益为正，但样本成熟度仍需继续观察。"
    elif total_return_pct > 0:
        summary = "当前模拟验证为正，样本质量相对可用。"
    elif primary_window.evaluated_trades == 0:
        summary = "样本还未成熟，先等待 5/10/20 天窗口。"
    else:
        summary = "当前模拟验证偏弱，需要复核推荐和风控规则。"
    return PaperValidationCredibility(
        score=score,
        level=level,
        summary=summary,
        warnings=warnings,
        evidence=[
            f"模拟样本 {len(items)} 笔",
            f"已闭环 {closed_count} 笔",
            f"10日成熟样本 {sample_age.mature_10d} 笔",
            f"20日窗口可评价 {primary_window.evaluated_trades} 笔",
        ],
        concentration_pct=concentration_pct,
    )


def _mature_count(items: list[PaperValidationItem], window_days: int) -> int:
    return sum(
        1
        for item in items
        if item.status in CLOSED_STATUSES or item.days_since_signal >= window_days
    )


def _days_to_next_mature(
    items: list[PaperValidationItem],
    window_days: int,
) -> int | None:
    pending = [
        max(window_days - item.days_since_signal, 0)
        for item in items
        if item.status not in CLOSED_STATUSES and item.days_since_signal < window_days
    ]
    return min(pending) if pending else None


def _items_drawdown(items: list[PaperValidationItem]) -> float:
    returns = [item.return_pct for item in items if item.return_pct is not None]
    if not returns:
        return 0.0
    return round(min(0.0, min(returns)), 4)


def _pnl_concentration(items: list[PaperValidationItem]) -> float | None:
    pnl_values = [abs(float(item.pnl)) for item in items if item.pnl != 0]
    if not pnl_values:
        return None
    return round(max(pnl_values) / sum(pnl_values) * 100, 4)


def _validation_state(trade: PaperTradeRecord) -> str:
    if trade.status == "pending":
        return "waiting_entry"
    if trade.status == "open":
        return "open"
    if trade.status == "time_exit" and trade.entry_date is None:
        return "expired"
    if trade.status in CLOSED_STATUSES:
        return "closed"
    return "tracked"


def _validation_next_action(state: str, return_pct: float | None) -> str:
    if state == "waiting_entry":
        return "等待触发价，不追高。"
    if state == "open":
        if return_pct is not None and return_pct >= 0:
            return "继续跟踪目标价和推荐变弱提醒。"
        return "重点检查止损价和仓位风险。"
    if state == "expired":
        return "买点未触发，作为无成交样本记录。"
    if state == "closed":
        return "已闭环，纳入胜率、收益和回撤统计。"
    return "继续观察。"


def _validation_verdict(
    total_trades: int,
    evaluated_trades: int,
    total_return_pct: float,
    max_drawdown_pct: float,
) -> str:
    if total_trades == 0:
        return "no_data"
    if evaluated_trades == 0:
        return "building_sample"
    if total_return_pct > 0 and max_drawdown_pct > -8:
        return "profitable"
    if total_return_pct < 0 or max_drawdown_pct <= -8:
        return "risk"
    return "building_sample"


def _validation_headline(
    verdict: str,
    primary_window: PaperValidationWindow,
    total_return_pct: float,
) -> str:
    if verdict == "no_data":
        return "还没有模拟记录，先把今日推荐加入模拟盘。"
    if verdict == "building_sample":
        return f"{primary_window.window_days}日窗口样本仍在积累，先看触发率和回撤。"
    if verdict == "profitable":
        return f"{primary_window.window_days}日验证为正，总收益 {total_return_pct:.2f}%。"
    return f"{primary_window.window_days}日验证存在风险，总收益 {total_return_pct:.2f}%。"


def _buy_lot(
    trade: PaperTradeRecord,
    cash: Decimal,
    allocation_per_trade: Decimal,
    fee_rate: Decimal,
    slippage_rate: Decimal,
) -> tuple[dict[str, object], PaperLedgerTransaction, Decimal, Decimal, Decimal] | None:
    if trade.entry_date is None or trade.entry_price is None or trade.entry_price <= 0:
        return None
    all_in_rate = Decimal("1") + fee_rate + slippage_rate
    affordable_gross = cash / all_in_rate if all_in_rate > 0 else cash
    gross_target = min(allocation_per_trade, affordable_gross)
    if gross_target <= Decimal("1"):
        return None
    shares = _shares(gross_target / trade.entry_price)
    if shares <= 0:
        return None
    gross = _money(shares * trade.entry_price)
    fee = _money(gross * fee_rate)
    slippage = _money(gross * slippage_rate)
    cash_flow = -(gross + fee + slippage)
    cash_balance = _money(cash + cash_flow)
    lot = {
        "trade_id": trade.trade_id,
        "instrument_id": trade.instrument_id,
        "strategy_id": trade.strategy_id,
        "status": trade.status,
        "entry_date": trade.entry_date,
        "entry_price": trade.entry_price,
        "exit_date": trade.exit_date,
        "exit_price": trade.exit_price,
        "latest_date": trade.latest_date,
        "latest_price": trade.latest_price,
        "shares": shares,
        "cost_basis": gross + fee + slippage,
    }
    transaction = PaperLedgerTransaction(
        transaction_id=f"{trade.trade_id}-buy",
        trade_id=trade.trade_id,
        instrument_id=trade.instrument_id,
        action="entry_buy",
        side="buy",
        trade_date=trade.entry_date,
        price=trade.entry_price,
        shares=shares,
        gross_amount=gross,
        fee=fee,
        slippage=slippage,
        cash_flow=_money(cash_flow),
        cash_balance=cash_balance,
        notes="按推荐触发价模拟买入。",
    )
    return lot, transaction, fee, slippage, gross


def _sell_lot_transactions(
    lot: dict[str, object],
    cash: Decimal,
    fee_rate: Decimal,
    slippage_rate: Decimal,
    take_profit_pct: Decimal,
) -> list[tuple[PaperLedgerTransaction, Decimal, Decimal, Decimal, Decimal]]:
    status = str(lot["status"])
    exit_date = lot["exit_date"]
    exit_price = lot["exit_price"]
    if not isinstance(exit_date, date) or not isinstance(exit_price, Decimal):
        return []
    remaining_shares = Decimal(str(lot["shares"]))
    cost_basis = Decimal(str(lot["cost_basis"]))
    cost_per_share = cost_basis / remaining_shares if remaining_shares > 0 else Decimal("0")
    if remaining_shares <= 0:
        return []

    portions: list[tuple[str, Decimal]]
    if status == "target_1_hit" and take_profit_pct < 100:
        first = _shares(remaining_shares * take_profit_pct / Decimal("100"))
        portions = [
            ("partial_take_profit", first),
            ("final_take_profit", remaining_shares - first),
        ]
    else:
        action = (
            "take_profit_exit"
            if status == "target_1_hit"
            else "stop_loss_exit"
            if status == "stopped"
            else "time_exit"
        )
        portions = [(action, remaining_shares)]

    results: list[tuple[PaperLedgerTransaction, Decimal, Decimal, Decimal, Decimal]] = []
    cash_balance = cash
    for index, (action, shares) in enumerate(portions):
        if shares <= 0:
            continue
        if index == len(portions) - 1:
            shares = remaining_shares
        gross = _money(shares * exit_price)
        fee = _money(gross * fee_rate)
        slippage = _money(gross * slippage_rate)
        cash_flow = gross - fee - slippage
        cash_balance = _money(cash_balance + cash_flow)
        pnl = cash_flow - (cost_per_share * shares)
        transaction = PaperLedgerTransaction(
            transaction_id=f"{lot['trade_id']}-{action}",
            trade_id=str(lot["trade_id"]),
            instrument_id=str(lot["instrument_id"]),
            action=action,
            side="sell",
            trade_date=exit_date,
            price=exit_price,
            shares=shares,
            gross_amount=gross,
            fee=fee,
            slippage=slippage,
            cash_flow=_money(cash_flow),
            cash_balance=cash_balance,
            notes=_transaction_note(action),
        )
        results.append((transaction, pnl, fee, slippage, gross))
        remaining_shares -= shares
    return results


def _active_lot_market_value(
    lots: list[dict[str, object]],
    current_date: date,
) -> tuple[Decimal, Decimal]:
    market_value = Decimal("0")
    unrealized_pnl = Decimal("0")
    for lot in lots:
        shares = Decimal(str(lot["shares"]))
        cost_basis = Decimal(str(lot["cost_basis"]))
        mark_price = _lot_mark_price(lot, current_date)
        value = shares * mark_price
        market_value += value
        unrealized_pnl += value - cost_basis
    return _money(market_value), _money(unrealized_pnl)


def _lot_mark_price(lot: dict[str, object], current_date: date) -> Decimal:
    latest_date = lot.get("latest_date")
    latest_price = lot.get("latest_price")
    if isinstance(latest_date, date) and latest_date <= current_date and isinstance(latest_price, Decimal):
        return latest_price
    return Decimal(str(lot["entry_price"]))


def _transaction_note(action: str) -> str:
    notes = {
        "partial_take_profit": "到达目标价，按分批止盈规则卖出一部分。",
        "final_take_profit": "到达目标价后剩余仓位模拟退出。",
        "take_profit_exit": "到达目标价，模拟止盈退出。",
        "stop_loss_exit": "跌破止损价，模拟纪律退出。",
        "time_exit": "超过持有窗口，模拟时间退出。",
    }
    return notes.get(action, "模拟交易流水。")


def _ledger_item(
    trade: PaperTradeRecord,
    allocation_per_trade: Decimal,
) -> PaperLedgerItem:
    shares = Decimal("0")
    market_value = Decimal("0")
    realized_pnl = Decimal("0")
    unrealized_pnl = Decimal("0")
    return_pct: float | None = None
    capital_allocated = Decimal("0")

    if trade.entry_price and trade.entry_price > 0:
        shares = (allocation_per_trade / trade.entry_price).quantize(Decimal("0.0001"))
        if trade.status in CLOSED_STATUSES and trade.exit_price is not None:
            exit_value = shares * trade.exit_price
            realized_pnl = exit_value - allocation_per_trade
            return_pct = _return_pct(trade.entry_price, trade.exit_price)
        elif trade.status == "open":
            latest_price = trade.latest_price or trade.entry_price
            market_value = shares * latest_price
            unrealized_pnl = market_value - allocation_per_trade
            return_pct = _return_pct(trade.entry_price, latest_price)
            capital_allocated = allocation_per_trade

    risk_pct = (
        _signed_return_pct(trade.trigger_price, trade.initial_stop)
        if trade.initial_stop is not None
        else None
    )
    reward_pct = (
        _signed_return_pct(trade.trigger_price, trade.target_1)
        if trade.target_1 is not None
        else None
    )
    total_pnl = realized_pnl + unrealized_pnl

    return PaperLedgerItem(
        trade_id=trade.trade_id,
        instrument_id=trade.instrument_id,
        strategy_id=trade.strategy_id,
        status=trade.status,
        outcome=_outcome_label(trade.status, return_pct),
        signal_date=trade.signal_date,
        entry_date=trade.entry_date,
        exit_date=trade.exit_date,
        latest_date=trade.latest_date,
        trigger_price=trade.trigger_price,
        entry_price=trade.entry_price,
        exit_price=trade.exit_price,
        latest_price=trade.latest_price,
        capital_allocated=_money(capital_allocated),
        shares=shares,
        market_value=_money(market_value),
        realized_pnl=_money(realized_pnl),
        unrealized_pnl=_money(unrealized_pnl),
        total_pnl=_money(total_pnl),
        return_pct=return_pct,
        risk_pct=risk_pct,
        reward_pct=reward_pct,
        holding_days=trade.holding_days,
        notes=trade.notes,
    )


def _ledger_curve(
    trades: list[PaperTradeRecord],
    initial_capital: Decimal,
    events: dict[date, dict[str, object]],
) -> list[PaperLedgerPoint]:
    if trades:
        start_date = min(trade.signal_date for trade in trades)
    else:
        start_date = date.today()
    points = [
        PaperLedgerPoint(
            date=start_date,
            equity=_money(initial_capital),
            pnl=Decimal("0.00"),
            drawdown_pct=0.0,
            realized_pnl=Decimal("0.00"),
            unrealized_pnl=Decimal("0.00"),
            event_count=0,
        )
    ]
    equity = initial_capital
    running_realized = Decimal("0")
    running_unrealized = Decimal("0")
    high_watermark = initial_capital
    for event_date in sorted(events):
        event = events[event_date]
        running_realized += event["realized"]
        running_unrealized += event["unrealized"]
        equity = initial_capital + running_realized + running_unrealized
        high_watermark = max(high_watermark, equity)
        drawdown_pct = (
            _pct(equity - high_watermark, high_watermark)
            if high_watermark > 0
            else 0.0
        )
        points.append(
            PaperLedgerPoint(
                date=event_date,
                equity=_money(equity),
                pnl=_money(equity - initial_capital),
                drawdown_pct=drawdown_pct,
                realized_pnl=_money(running_realized),
                unrealized_pnl=_money(running_unrealized),
                event_count=int(event["count"]),
            )
        )
    return points


def _outcome_label(status: str, return_pct: float | None) -> str:
    if status == "pending":
        return "等待触发"
    if status == "open":
        if return_pct is not None and return_pct >= 0:
            return "浮盈跟踪"
        return "浮亏跟踪"
    if status == "target_1_hit":
        return "止盈达成"
    if status == "stopped":
        return "止损离场"
    if status == "time_exit":
        return "时间退出"
    return "已跟踪"


def _signed_return_pct(base: Decimal, value: Decimal) -> float:
    if base <= 0:
        return 0.0
    return round(float((value / base - Decimal("1")) * Decimal("100")), 4)


def _pct(value: Decimal, denominator: Decimal) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(value / denominator * Decimal("100")), 4)


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _money_down(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def _shares(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_DOWN)


def _bps_rate(value: Decimal) -> Decimal:
    return value / Decimal("10000")


def _a_share_local_datetime(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(A_SHARE_TZ)
    if value.tzinfo is None:
        return value.replace(tzinfo=A_SHARE_TZ)
    return value.astimezone(A_SHARE_TZ)


def _a_share_execution_session(value: datetime) -> str:
    local = _a_share_local_datetime(value)
    if local.weekday() >= 5:
        return "closed"
    current = local.time()
    if A_SHARE_MORNING_START <= current <= A_SHARE_MORNING_END:
        return "regular"
    if A_SHARE_AFTERNOON_START <= current <= A_SHARE_AFTERNOON_END:
        return "regular"
    if A_SHARE_MORNING_END < current < A_SHARE_AFTERNOON_START:
        return "midday_break"
    if current > A_SHARE_AFTERNOON_END:
        return "after_close"
    return "pre_open"


def _is_a_share_trade(trade: PaperTradeRecord) -> bool:
    return trade.instrument_id.upper().startswith("CN:")


def _a_share_can_fill_bar(
    trade: PaperTradeRecord,
    trade_date: date,
    as_of: datetime | None,
    *,
    status: str,
    entry_date: date | None,
) -> bool:
    if not _is_a_share_trade(trade) or as_of is None:
        return True
    local = _a_share_local_datetime(as_of)
    current_date = local.date()
    if trade_date > current_date:
        return False
    if trade_date < current_date:
        return True

    session = _a_share_execution_session(local)
    if session == "regular":
        return True
    if session == "after_close":
        if status == "pending":
            return trade.signal_date < trade_date
        if status == "open":
            return entry_date is not None and entry_date < trade_date
    return False


def _append_note(existing: str, note: str) -> str:
    if not existing:
        return note
    if note in existing:
        return existing
    return f"{existing} {note}"


def _evaluate_trade(
    trade: PaperTradeRecord,
    bars: pd.DataFrame,
    max_holding_days: int,
    max_entry_wait_days: int,
    as_of: datetime | None = None,
) -> tuple[dict[str, object], int]:
    ordered = bars.sort_values("trade_date").reset_index(drop=True)
    if pd.api.types.is_datetime64_any_dtype(ordered["trade_date"]):
        ordered["trade_date"] = ordered["trade_date"].dt.date
    entry_date = trade.entry_date
    entry_price = trade.entry_price
    status = trade.status
    notes = trade.notes
    deferred_fills = 0

    for _, row in ordered.iterrows():
        trade_date = row["trade_date"]
        if isinstance(trade_date, pd.Timestamp):
            trade_date = trade_date.date()
        elif isinstance(trade_date, datetime):
            trade_date = trade_date.date()
        high = Decimal(str(row["high"]))
        low = Decimal(str(row["low"]))
        close = Decimal(str(row["close"]))
        if status == "pending":
            wait_days = max((trade_date - trade.signal_date).days, 0)
            if high >= trade.trigger_price:
                if not _a_share_can_fill_bar(
                    trade,
                    trade_date,
                    as_of,
                    status=status,
                    entry_date=entry_date,
                ):
                    deferred_fills += 1
                    notes = _append_note(
                        notes,
                        "A股非交易时段：当天买点已出现，等待下个交易时段确认。",
                    )
                    continue
                status = "open"
                entry_date = trade_date
                entry_price = trade.trigger_price
                notes = _append_note(notes, "触发价被日内高点确认，模拟开仓。")
            elif wait_days > max_entry_wait_days:
                return (
                    {
                        "status": "time_exit",
                        "latest_date": trade_date,
                        "latest_price": close,
                        "exit_date": trade_date,
                        "exit_price": close,
                        "realized_return_pct": Decimal("0"),
                        "holding_days": 0,
                        "notes": "买点等待超时，未开仓退出跟踪。",
                    },
                    deferred_fills,
                )
            else:
                continue

        if status == "open" and entry_date is not None and entry_price is not None:
            holding_days = max((trade_date - entry_date).days, 0)
            if not _a_share_can_fill_bar(
                trade,
                trade_date,
                as_of,
                status=status,
                entry_date=entry_date,
            ):
                exit_condition_reached = (
                    (trade.initial_stop is not None and low <= trade.initial_stop)
                    or (trade.target_1 is not None and high >= trade.target_1)
                    or holding_days >= max_holding_days
                )
                if exit_condition_reached:
                    deferred_fills += 1
                    notes = _append_note(
                        notes,
                        "A股非交易时段：卖出条件已出现，等待交易时段确认。",
                    )
                continue
            if _is_a_share_trade(trade) and trade_date == entry_date:
                notes = _append_note(notes, "A股 T+1：买入当日不模拟卖出。")
                continue
            if trade.initial_stop is not None and low <= trade.initial_stop:
                return (
                    _closed_update(
                        status="stopped",
                        entry_date=entry_date,
                        entry_price=entry_price,
                        exit_date=trade_date,
                        exit_price=trade.initial_stop,
                        latest_price=close,
                        holding_days=holding_days,
                        notes="触及初始止损，模拟离场。",
                    ),
                    deferred_fills,
                )
            if trade.target_1 is not None and high >= trade.target_1:
                return (
                    _closed_update(
                        status="target_1_hit",
                        entry_date=entry_date,
                        entry_price=entry_price,
                        exit_date=trade_date,
                        exit_price=trade.target_1,
                        latest_price=close,
                        holding_days=holding_days,
                        notes="触及第一目标价，模拟止盈。",
                    ),
                    deferred_fills,
                )
            if holding_days >= max_holding_days:
                return (
                    _closed_update(
                        status="time_exit",
                        entry_date=entry_date,
                        entry_price=entry_price,
                        exit_date=trade_date,
                        exit_price=close,
                        latest_price=close,
                        holding_days=holding_days,
                        notes="达到最长持有窗口，按收盘价模拟退出。",
                    ),
                    deferred_fills,
                )

    latest = ordered.iloc[-1]
    latest_date = latest["trade_date"]
    latest_price = Decimal(str(latest["close"]))
    if status == "open" and entry_date is not None and entry_price is not None:
        return (
            {
                "status": "open",
                "entry_date": entry_date,
                "entry_price": entry_price,
                "latest_date": latest_date,
                "latest_price": latest_price,
                "unrealized_return_pct": Decimal(str(_return_pct(entry_price, latest_price))),
                "holding_days": max((latest_date - entry_date).days, 0),
                "notes": notes,
            },
            deferred_fills,
        )
    return (
        {
            "status": "pending",
            "latest_date": latest_date,
            "latest_price": latest_price,
            "holding_days": 0,
            "notes": notes,
        },
        deferred_fills,
    )


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
