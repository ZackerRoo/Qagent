from collections import defaultdict
from datetime import date, datetime, time, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Mapping
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import BaseModel, Field

from qagent.providers.base import MarketDataProvider
from qagent.storage.paper import PaperTradeRecord, PaperTradeSourceContext, PaperTradingRepository
from qagent.storage.repository import OpportunitySnapshotRecord


OPEN_STATUSES = {"pending", "open"}
CLOSED_STATUSES = {"target_1_hit", "stopped", "time_exit", "missed_entry"}
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


class PaperDailyReportSummary(BaseModel):
    total_trades: int
    new_opportunities: int
    triggered_today: int
    open_positions: int
    closed_today: int
    target_hits_today: int
    stopped_today: int
    total_return_pct: float
    max_drawdown_pct: float
    win_rate: float | None


class PaperDailyReportItem(BaseModel):
    trade_id: str
    instrument_id: str
    strategy_id: str | None
    status: str
    signal_date: date
    entry_date: date | None = None
    exit_date: date | None = None
    return_pct: float | None = None
    pnl: Decimal = Decimal("0")
    next_action: str
    notes: str


class PaperDailyAssetGroup(BaseModel):
    asset_type: str
    label: str
    total_trades: int
    pending_trades: int
    open_trades: int
    closed_trades: int
    positive_trades: int
    negative_trades: int
    win_rate: float | None
    average_return_pct: float | None
    total_pnl: Decimal
    total_return_pct: float | None
    best_return_pct: float | None
    worst_return_pct: float | None


class PaperDailyBenchmarkItem(BaseModel):
    benchmark_id: str | None = None
    name: str
    return_pct: float | None = None
    excess_return_pct: float | None = None
    summary: str


class PaperDailyBenchmark(BaseModel):
    total_return_pct: float
    items: list[PaperDailyBenchmarkItem]
    summary: str


class PaperMarketContext(BaseModel):
    regime: str
    title: str
    summary: str
    benchmark_name: str | None = None
    benchmark_return_pct: float | None = None
    excess_return_pct: float | None = None
    market_drag_score: float = 0.0
    strategy_drag_score: float = 0.0


class PaperTriggerQualitySummary(BaseModel):
    total_trades: int
    pending_count: int
    triggered_count: int
    missed_entry_count: int
    no_chase_missed_count: int
    stopped_count: int
    target_hit_count: int
    trigger_rate: float | None
    miss_rate: float | None
    stop_after_trigger_rate: float | None
    verdict: str
    summary: str


class PaperRiskGateStatus(BaseModel):
    action: str
    can_add_entries: bool
    title: str
    reason: str
    reasons: list[str]
    recovery_conditions: list[str]
    recovery_state: str = "normal"
    recovery_score: float = 1.0
    max_new_entries: int = 5
    position_size_multiplier: float = 1.0


class PaperFailureAttributionItem(BaseModel):
    dimension: str
    key: str
    label: str
    total_trades: int
    evaluated_trades: int
    closed_trades: int
    stopped_trades: int
    target_hit_trades: int
    win_rate: float | None
    average_return_pct: float | None
    total_pnl: Decimal
    total_return_pct: float | None
    worst_return_pct: float | None
    verdict: str
    note: str


class PaperEventTimelineItem(BaseModel):
    event_id: str
    trade_id: str
    instrument_id: str
    strategy_id: str | None
    event_date: date
    event_type: str
    title: str
    description: str
    status: str
    price: Decimal | None = None
    pnl: Decimal = Decimal("0")
    return_pct: float | None = None


class PaperDailyReport(BaseModel):
    report_date: date
    summary: PaperDailyReportSummary
    benchmark: PaperDailyBenchmark
    risk_gate: PaperRiskGateStatus
    market_context: PaperMarketContext = Field(
        default_factory=lambda: PaperMarketContext(
            regime="no_benchmark",
            title="暂无指数归因",
            summary="暂无指数基准数据，先看模拟盘绝对收益和回撤。",
        )
    )
    trigger_quality: PaperTriggerQualitySummary = Field(
        default_factory=lambda: PaperTriggerQualitySummary(
            total_trades=0,
            pending_count=0,
            triggered_count=0,
            missed_entry_count=0,
            no_chase_missed_count=0,
            stopped_count=0,
            target_hit_count=0,
            trigger_rate=None,
            miss_rate=None,
            stop_after_trigger_rate=None,
            verdict="waiting",
            summary="暂无模拟单，等待推荐进入验证。",
        )
    )
    failure_attribution: list[PaperFailureAttributionItem]
    event_timeline: list[PaperEventTimelineItem]
    new_opportunities: list[PaperDailyReportItem]
    triggered_today: list[PaperDailyReportItem]
    holdings: list[PaperDailyReportItem]
    closed_today: list[PaperDailyReportItem]
    asset_groups: list[PaperDailyAssetGroup]
    next_trade_day_focus: list[str]
    data_health: dict[str, str]


def seed_paper_trades_from_snapshots(
    repo: PaperTradingRepository,
    snapshots: list[OpportunitySnapshotRecord],
    provider: str,
    max_created: int | None = None,
    max_active_trades: int | None = None,
    max_signal_age_days: int | None = None,
    as_of: datetime | None = None,
    signal_date_override: date | None = None,
) -> PaperSeedResult:
    created = 0
    skipped = 0
    existing_trades = repo.list_trades(limit=1000, provider=provider)
    existing = {trade.source_snapshot_id for trade in existing_trades}
    active_instruments = {
        trade.instrument_id for trade in existing_trades if trade.status in OPEN_STATUSES
    }
    active_count = sum(1 for trade in existing_trades if trade.status in OPEN_STATUSES)
    current_date = _a_share_local_datetime(as_of).date()
    for snapshot in snapshots:
        if max_created is not None and created >= max_created:
            skipped += 1
            continue
        if max_active_trades is not None and active_count + created >= max_active_trades:
            skipped += 1
            continue
        if snapshot.snapshot_id in existing:
            skipped += 1
            continue
        if snapshot.instrument_id in active_instruments:
            skipped += 1
            continue
        signal_date = signal_date_override or snapshot.signal_date
        if signal_date is None or snapshot.trigger_price is None:
            skipped += 1
            continue
        if (
            max_signal_age_days is not None
            and (current_date - signal_date).days > max_signal_age_days
        ):
            skipped += 1
            continue
        repo.create_trade(
            source_snapshot_id=snapshot.snapshot_id,
            provider=provider,
            instrument_id=snapshot.instrument_id,
            strategy_id=snapshot.primary_strategy_id,
            signal_date=signal_date,
            trigger_price=snapshot.trigger_price,
            initial_stop=snapshot.initial_stop,
            target_1=snapshot.target_1,
            rank_score=snapshot.rank_score,
        )
        created += 1
        active_instruments.add(snapshot.instrument_id)
    return PaperSeedResult(scanned=len(snapshots), created=created, skipped=skipped)


def update_paper_trades(
    repo: PaperTradingRepository,
    provider: MarketDataProvider,
    provider_mode: str | None = None,
    max_holding_days: int = 20,
    max_entry_wait_days: int = 10,
    as_of: datetime | None = None,
) -> PaperUpdateResult:
    trades = repo.list_trades(limit=1000, provider=provider_mode)
    repaired_invalid_dates = _repair_impossible_trade_dates(repo, trades)
    if repaired_invalid_dates:
        trades = repo.list_trades(limit=1000, provider=provider_mode)
    active = [trade for trade in trades if trade.status in OPEN_STATUSES]
    execution_time = _a_share_local_datetime(as_of)
    execution_session = _a_share_execution_session(execution_time)
    fills_deferred = 0
    minute_checked = 0
    minute_rows = 0
    daily_fallback_checked = 0
    daily_fallback_rows = 0
    for trade in active:
        minute_update, checked, rows = _try_evaluate_trade_with_minutes(
            repo,
            provider,
            trade,
            max_holding_days=max_holding_days,
            max_entry_wait_days=max_entry_wait_days,
            as_of=execution_time,
        )
        minute_checked += checked
        minute_rows += rows
        if minute_update is not None:
            repo.update_trade(trade.trade_id, **minute_update)
            continue
        daily_fallback_checked += 1
        daily_end = max(trade.signal_date, execution_time.date())
        bars = provider.get_daily_bars(
            [trade.instrument_id],
            start=trade.signal_date,
            end=daily_end,
        )
        daily_fallback_rows += len(bars)
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
    refreshed = repo.list_trades(limit=1000, provider=provider_mode)
    provider_errors = getattr(provider, "last_errors", [])
    data_health = {
        "provider": provider.name,
        "paper_provider_filter": provider_mode or "all",
        "trades": str(len(refreshed)),
        "active_checked": str(len(active)),
        **paper_execution_data_health(
            as_of=execution_time,
            fills_deferred=fills_deferred,
            session=execution_session,
        ),
        "paper_minute_checked": str(minute_checked),
        "paper_minute_rows": str(minute_rows),
        "paper_daily_fallback_checked": str(daily_fallback_checked),
        "paper_daily_fallback_rows": str(daily_fallback_rows),
        "paper_repaired_invalid_dates": str(repaired_invalid_dates),
    }
    if provider_errors:
        data_health["errors"] = " | ".join(provider_errors[:3])
    return PaperUpdateResult(
        summary=summarize_paper_trades(refreshed),
        trades=refreshed,
        data_health=data_health,
    )


def _repair_impossible_trade_dates(
    repo: PaperTradingRepository,
    trades: list[PaperTradeRecord],
) -> int:
    repaired = 0
    for trade in trades:
        if (
            trade.entry_date is None
            or trade.entry_price is None
            or trade.exit_date is None
            or trade.exit_date >= trade.entry_date
        ):
            continue
        repo.update_trade(
            trade.trade_id,
            status="open",
            exit_date=None,
            exit_price=None,
            realized_return_pct=None,
            latest_date=trade.entry_date,
            latest_price=trade.entry_price,
            unrealized_return_pct=Decimal("0"),
            holding_days=0,
            notes=_append_note(
                trade.notes,
                "修复异常日期：历史记录出现离场早于入场，已恢复为持仓重新评估。",
            ),
        )
        repaired += 1
    return repaired


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


def build_paper_daily_report(
    *,
    trades: list[PaperTradeRecord],
    ledger: PaperLedger,
    validation: PaperValidationResult,
    as_of: date | None = None,
    benchmark_items: list[Mapping[str, object]] | None = None,
    asset_type_by_instrument: Mapping[str, str] | None = None,
) -> PaperDailyReport:
    report_date = as_of or _validation_as_of(trades)
    ledger_by_id = {item.trade_id: item for item in ledger.items}
    validation_by_id = {item.trade_id: item for item in validation.items}
    new_opportunities = [
        _daily_report_item(trade, ledger_by_id, validation_by_id)
        for trade in trades
        if trade.signal_date == report_date
    ]
    triggered_today = [
        _daily_report_item(trade, ledger_by_id, validation_by_id)
        for trade in trades
        if trade.entry_date == report_date
    ]
    holdings = [
        _daily_report_item(trade, ledger_by_id, validation_by_id)
        for trade in trades
        if trade.status == "open"
    ]
    closed_today = [
        _daily_report_item(trade, ledger_by_id, validation_by_id)
        for trade in trades
        if trade.exit_date == report_date and trade.status in CLOSED_STATUSES
    ]
    benchmark = _paper_daily_benchmark(
        total_return_pct=ledger.summary.total_return_pct,
        benchmark_items=benchmark_items or [],
    )
    market_context = _paper_market_context(benchmark)
    trigger_quality = _paper_trigger_quality(trades)
    return PaperDailyReport(
        report_date=report_date,
        summary=PaperDailyReportSummary(
            total_trades=len(trades),
            new_opportunities=len(new_opportunities),
            triggered_today=len(triggered_today),
            open_positions=len(holdings),
            closed_today=len(closed_today),
            target_hits_today=sum(1 for trade in closed_today if trade.status == "target_1_hit"),
            stopped_today=sum(1 for trade in closed_today if trade.status == "stopped"),
            total_return_pct=ledger.summary.total_return_pct,
            max_drawdown_pct=ledger.summary.max_drawdown_pct,
            win_rate=ledger.summary.win_rate,
        ),
        benchmark=benchmark,
        risk_gate=build_paper_risk_gate_status(
            ledger,
            market_context=market_context,
            trigger_quality=trigger_quality,
        ),
        market_context=market_context,
        trigger_quality=trigger_quality,
        failure_attribution=_paper_failure_attribution(
            ledger.items,
            asset_type_by_instrument=asset_type_by_instrument or {},
            allocation_per_trade=ledger.summary.allocation_per_trade,
        ),
        event_timeline=_paper_event_timeline(
            trades=trades,
            ledger_by_id=ledger_by_id,
            validation_by_id=validation_by_id,
        ),
        new_opportunities=new_opportunities,
        triggered_today=triggered_today,
        holdings=holdings,
        closed_today=closed_today,
        asset_groups=_paper_asset_groups(
            ledger.items,
            asset_type_by_instrument=asset_type_by_instrument or {},
            allocation_per_trade=ledger.summary.allocation_per_trade,
        ),
        next_trade_day_focus=_next_trade_day_focus(
            new_opportunities=new_opportunities,
            holdings=holdings,
            closed_today=closed_today,
            validation=validation,
        ),
        data_health={
            **ledger.data_health,
            "paper_daily_report": "ready",
            "paper_daily_report_date": report_date.isoformat(),
            "paper_daily_report_trades": str(len(trades)),
            "paper_daily_report_benchmarks": str(len(benchmark.items)),
        },
    )


def build_paper_risk_gate_status(
    ledger: PaperLedger,
    *,
    market_context: PaperMarketContext | None = None,
    trigger_quality: PaperTriggerQualitySummary | None = None,
) -> PaperRiskGateStatus:
    return _paper_risk_gate_status(
        ledger,
        market_context=market_context,
        trigger_quality=trigger_quality,
    )


def _paper_asset_groups(
    items: list[PaperLedgerItem],
    *,
    asset_type_by_instrument: Mapping[str, str],
    allocation_per_trade: Decimal,
) -> list[PaperDailyAssetGroup]:
    grouped: dict[str, list[PaperLedgerItem]] = defaultdict(list)
    for item in items:
        grouped[_paper_asset_type(item.instrument_id, asset_type_by_instrument)].append(item)
    return [
        _paper_asset_group(asset_type, group_items, allocation_per_trade=allocation_per_trade)
        for asset_type, group_items in sorted(
            grouped.items(),
            key=lambda pair: (_paper_asset_rank(pair[0]), pair[0]),
        )
    ]


def _paper_asset_group(
    asset_type: str,
    items: list[PaperLedgerItem],
    *,
    allocation_per_trade: Decimal,
) -> PaperDailyAssetGroup:
    returns = [item.return_pct for item in items if item.return_pct is not None]
    closed = [item for item in items if item.status in CLOSED_STATUSES]
    positive = [value for value in returns if value > 0]
    negative = [value for value in returns if value < 0]
    total_pnl = _money(sum((item.total_pnl for item in items), Decimal("0")))
    effective_items = [item for item in items if item.status != "pending"]
    capital_base = allocation_per_trade * Decimal(len(effective_items))
    total_return_pct = (
        round(float(total_pnl / capital_base * Decimal("100")), 4)
        if capital_base > 0
        else None
    )
    return PaperDailyAssetGroup(
        asset_type=asset_type,
        label=_paper_asset_label(asset_type),
        total_trades=len(items),
        pending_trades=sum(1 for item in items if item.status == "pending"),
        open_trades=sum(1 for item in items if item.status == "open"),
        closed_trades=len(closed),
        positive_trades=len(positive),
        negative_trades=len(negative),
        win_rate=round(len(positive) / len(closed), 4) if closed else None,
        average_return_pct=round(sum(returns) / len(returns), 4) if returns else None,
        total_pnl=total_pnl,
        total_return_pct=total_return_pct,
        best_return_pct=round(max(returns), 4) if returns else None,
        worst_return_pct=round(min(returns), 4) if returns else None,
    )


def _paper_asset_type(
    instrument_id: str,
    asset_type_by_instrument: Mapping[str, str],
) -> str:
    raw = asset_type_by_instrument.get(instrument_id)
    if raw:
        normalized = raw.strip().lower()
        if normalized:
            return normalized
    symbol = instrument_id.split(":", 1)[-1]
    if symbol.startswith(("15", "16", "51", "56", "58")):
        return "etf"
    return "stock" if instrument_id.upper().startswith("CN:") else "other"


def _paper_asset_label(asset_type: str) -> str:
    labels = {
        "stock": "股票",
        "etf": "ETF",
        "index": "指数",
        "fund": "基金",
    }
    return labels.get(asset_type, asset_type.upper())


def _paper_asset_rank(asset_type: str) -> int:
    return {"stock": 0, "etf": 1, "index": 2, "fund": 3}.get(asset_type, 9)


def _paper_market_context(benchmark: PaperDailyBenchmark) -> PaperMarketContext:
    item = next((entry for entry in benchmark.items if entry.excess_return_pct is not None), None)
    if item is None:
        return PaperMarketContext(
            regime="no_benchmark",
            title="暂无指数归因",
            summary="暂无指数基准数据，先看模拟盘绝对收益和回撤。",
        )
    benchmark_return = item.return_pct
    excess = item.excess_return_pct
    market_drag = 0.0
    strategy_drag = 0.0
    if benchmark_return is not None and benchmark_return < 0:
        market_drag = min(abs(benchmark_return) / 8.0, 1.0)
    if excess is not None and excess < 0:
        strategy_drag = min(abs(excess) / 8.0, 1.0)

    if excess is not None and excess >= 0:
        return PaperMarketContext(
            regime="outperforming",
            title="跑赢指数",
            summary=f"模拟盘相对{item.name}超额 {excess:+.2f}%，说明当前收益不是单纯靠大盘。",
            benchmark_name=item.name,
            benchmark_return_pct=benchmark_return,
            excess_return_pct=excess,
            market_drag_score=market_drag,
            strategy_drag_score=strategy_drag,
        )
    if benchmark_return is not None and benchmark_return <= -1.0 and market_drag >= strategy_drag:
        return PaperMarketContext(
            regime="market_drag",
            title="市场拖累",
            summary=f"{item.name}同期 {benchmark_return:+.2f}%，模拟盘下行主要受市场环境影响，优先缩仓而不是直接否定策略。",
            benchmark_name=item.name,
            benchmark_return_pct=benchmark_return,
            excess_return_pct=excess,
            market_drag_score=market_drag,
            strategy_drag_score=strategy_drag,
        )
    return PaperMarketContext(
        regime="strategy_underperforming",
        title="策略/买点跑输",
        summary=f"{item.name}同期 {benchmark_return:+.2f}%，模拟盘跑输 {excess:+.2f}%，重点复核选股、触发价和止损规则。",
        benchmark_name=item.name,
        benchmark_return_pct=benchmark_return,
        excess_return_pct=excess,
        market_drag_score=market_drag,
        strategy_drag_score=strategy_drag,
    )


def _paper_trigger_quality(trades: list[PaperTradeRecord]) -> PaperTriggerQualitySummary:
    total = len(trades)
    pending = sum(1 for trade in trades if trade.status == "pending")
    triggered = sum(1 for trade in trades if trade.entry_date is not None)
    missed = sum(1 for trade in trades if trade.status == "missed_entry")
    no_chase_missed = sum(
        1
        for trade in trades
        if trade.status == "missed_entry" and ("追高" in trade.notes or "no-chase" in trade.notes.lower())
    )
    stopped = sum(1 for trade in trades if trade.status == "stopped")
    target_hit = sum(1 for trade in trades if trade.status == "target_1_hit")
    trigger_rate = round(triggered / total, 4) if total else None
    miss_rate = round(missed / total, 4) if total else None
    stop_after_trigger_rate = round(stopped / triggered, 4) if triggered else None

    if total == 0:
        verdict = "waiting"
        summary = "暂无模拟单，等待推荐进入验证。"
    elif no_chase_missed >= 1 and (miss_rate or 0) >= 0.15:
        verdict = "needs_tighter_entry"
        summary = f"{no_chase_missed} 笔因为不追高规则错过，说明触发价和追高上限需要更精细。"
    elif stopped >= 3 and (stop_after_trigger_rate or 0) >= 0.5:
        verdict = "stop_rules_weak"
        summary = f"触发后止损比例 {stop_after_trigger_rate:.0%}，优先复核入场确认和止损距离。"
    elif pending > triggered and pending / total >= 0.5:
        verdict = "waiting"
        summary = f"{pending} 笔仍在等待触发，当前更多是观察池，不是已经买入。"
    elif target_hit >= stopped and triggered:
        verdict = "healthy"
        summary = "触发后表现相对健康，可以继续按原规则跟踪。"
    else:
        verdict = "watch"
        summary = "触发质量仍需观察，先保持小样本跟踪。"

    return PaperTriggerQualitySummary(
        total_trades=total,
        pending_count=pending,
        triggered_count=triggered,
        missed_entry_count=missed,
        no_chase_missed_count=no_chase_missed,
        stopped_count=stopped,
        target_hit_count=target_hit,
        trigger_rate=trigger_rate,
        miss_rate=miss_rate,
        stop_after_trigger_rate=stop_after_trigger_rate,
        verdict=verdict,
        summary=summary,
    )


def _paper_risk_gate_status(
    ledger: PaperLedger,
    *,
    market_context: PaperMarketContext | None = None,
    trigger_quality: PaperTriggerQualitySummary | None = None,
) -> PaperRiskGateStatus:
    summary = ledger.summary
    reasons: list[str] = []
    if summary.total_trades >= 5 and summary.total_return_pct <= -2.0:
        reasons.append(f"总收益 {summary.total_return_pct:.2f}% 低于 -2.00%")
    if summary.total_trades >= 5 and summary.max_drawdown_pct <= -2.0:
        reasons.append(f"最大回撤 {summary.max_drawdown_pct:.2f}% 低于 -2.00%")
    if (
        summary.closed_trades >= 3
        and summary.win_rate is not None
        and summary.win_rate <= 0.25
    ):
        reasons.append(f"闭环胜率 {summary.win_rate:.0%} 低于 25%")
    if summary.stopped_count >= 3 and summary.target_hit_count == 0:
        reasons.append("止损次数较多且尚无止盈")

    if summary.total_trades == 0:
        return PaperRiskGateStatus(
            action="allow_new_entries",
            can_add_entries=True,
            title="允许新增模拟单",
            reason="还没有模拟历史，允许从新推荐开始跟踪。",
            reasons=["no_paper_history"],
            recovery_conditions=["至少积累 5 笔模拟记录后再判断门禁。"],
            recovery_state="normal",
            recovery_score=1.0,
            max_new_entries=max(summary.max_positions, 1),
            position_size_multiplier=1.0,
        )

    recovery_score = _paper_recovery_score(summary, market_context, trigger_quality)
    severe = _paper_risk_gate_is_severe(summary)
    if not reasons:
        return PaperRiskGateStatus(
            action="allow_new_entries",
            can_add_entries=True,
            title="允许新增模拟单",
            reason="当前回撤、胜率和触发质量仍在允许范围内。",
            reasons=["within_limits"],
            recovery_conditions=["继续按最大持仓上限执行，不追高。"],
            recovery_state="normal",
            recovery_score=recovery_score,
            max_new_entries=max(summary.max_positions - summary.open_trades - summary.pending_trades, 0),
            position_size_multiplier=1.0,
        )

    if severe and recovery_score < 0.45:
        return PaperRiskGateStatus(
            action="pause_new_entries",
            can_add_entries=False,
            title="暂停新增模拟单",
            reason="；".join(reasons),
            reasons=reasons,
            recovery_conditions=[
                "总收益回到 -2% 以上",
                "最大回撤收敛到 -2% 以内",
                "闭环胜率回到 25% 以上",
                "出现目标命中或连续止损减少",
            ],
            recovery_state="paused",
            recovery_score=recovery_score,
            max_new_entries=0,
            position_size_multiplier=0.0,
        )

    return PaperRiskGateStatus(
        action="resume_probe_entries",
        can_add_entries=True,
        title="恢复小仓位试单",
        reason="；".join(reasons) + "。风控尚未完全恢复，但允许 1 笔高质量新机会试单，避免错过强信号。",
        reasons=reasons,
        recovery_conditions=[
            "恢复期只允许 1 笔试单，且必须来自当前最高质量推荐",
            "试单后继续观察总收益、最大回撤和止损次数",
            "若再次触发止损或跑输指数扩大，会重新暂停新增",
            "若胜率和收益恢复，再切回正常新增",
        ],
        recovery_state="probing",
        recovery_score=recovery_score,
        max_new_entries=1,
        position_size_multiplier=0.35,
    )


def _paper_recovery_score(
    summary: PaperLedgerSummary,
    market_context: PaperMarketContext | None,
    trigger_quality: PaperTriggerQualitySummary | None,
) -> float:
    score = 0.4
    if summary.total_return_pct > -2.0:
        score += 0.18
    if summary.max_drawdown_pct > -2.0:
        score += 0.16
    if summary.win_rate is None or summary.win_rate > 0.25:
        score += 0.14
    if summary.target_hit_count > 0 or summary.stopped_count < 3:
        score += 0.12
    if market_context is not None:
        if market_context.regime == "market_drag":
            score += 0.08
        elif market_context.regime == "strategy_underperforming":
            score -= 0.08
    if trigger_quality is not None:
        if trigger_quality.verdict == "healthy":
            score += 0.08
        elif trigger_quality.verdict in {"needs_tighter_entry", "stop_rules_weak"}:
            score -= 0.08
    return round(max(0.0, min(1.0, score)), 4)


def _paper_risk_gate_is_severe(summary: PaperLedgerSummary) -> bool:
    if summary.total_return_pct <= -6.0 or summary.max_drawdown_pct <= -6.0:
        return True
    if (
        summary.stopped_count >= 6
        and summary.win_rate is not None
        and summary.win_rate <= 0.15
        and summary.target_hit_count == 0
    ):
        return True
    return False


def _paper_failure_attribution(
    items: list[PaperLedgerItem],
    *,
    asset_type_by_instrument: Mapping[str, str],
    allocation_per_trade: Decimal,
) -> list[PaperFailureAttributionItem]:
    grouped: dict[tuple[str, str, str], list[PaperLedgerItem]] = defaultdict(list)
    for item in items:
        asset_type = _paper_asset_type(item.instrument_id, asset_type_by_instrument)
        grouped[("asset", asset_type, _paper_asset_label(asset_type))].append(item)
        grouped[("strategy", item.strategy_id or "unknown", item.strategy_id or "未分类策略")].append(item)
        grouped[("status", item.status, _paper_status_label(item.status))].append(item)

    attribution = [
        _paper_failure_group(
            dimension=dimension,
            key=key,
            label=label,
            items=group_items,
            allocation_per_trade=allocation_per_trade,
        )
        for (dimension, key, label), group_items in grouped.items()
    ]
    return sorted(
        attribution,
        key=lambda item: (item.total_pnl, -item.evaluated_trades, item.dimension, item.key),
    )[:12]


def _paper_failure_group(
    *,
    dimension: str,
    key: str,
    label: str,
    items: list[PaperLedgerItem],
    allocation_per_trade: Decimal,
) -> PaperFailureAttributionItem:
    evaluated = [item for item in items if item.status != "pending" and item.return_pct is not None]
    closed = [item for item in items if item.status in CLOSED_STATUSES]
    returns = [item.return_pct for item in evaluated if item.return_pct is not None]
    total_pnl = _money(sum((item.total_pnl for item in items), Decimal("0")))
    capital_base = allocation_per_trade * Decimal(str(len(evaluated)))
    total_return_pct = _pct(total_pnl, capital_base) if capital_base > 0 else None
    win_rate = (
        round(sum(1 for value in returns if value > 0) / len(returns), 4)
        if returns
        else None
    )
    average_return_pct = round(sum(returns) / len(returns), 4) if returns else None
    worst_return_pct = round(min(returns), 4) if returns else None
    if total_pnl < 0:
        verdict = "drag"
        note = f"{label} 当前拖累模拟盘，优先复核买点、止损和是否追高。"
    elif total_pnl > 0:
        verdict = "contributor"
        note = f"{label} 当前贡献正收益，可继续观察是否稳定。"
    else:
        verdict = "neutral"
        note = f"{label} 暂无明确收益贡献，继续等待样本。"
    return PaperFailureAttributionItem(
        dimension=dimension,
        key=key,
        label=label,
        total_trades=len(items),
        evaluated_trades=len(evaluated),
        closed_trades=len(closed),
        stopped_trades=sum(1 for item in items if item.status == "stopped"),
        target_hit_trades=sum(1 for item in items if item.status == "target_1_hit"),
        win_rate=win_rate,
        average_return_pct=average_return_pct,
        total_pnl=total_pnl,
        total_return_pct=total_return_pct,
        worst_return_pct=worst_return_pct,
        verdict=verdict,
        note=note,
    )


def _paper_status_label(status: str) -> str:
    return {
        "pending": "等待触发",
        "open": "持仓中",
        "target_1_hit": "目标命中",
        "stopped": "止损",
        "time_exit": "时间退出",
        "missed_entry": "错过买点",
    }.get(status, status)


def _paper_event_timeline(
    *,
    trades: list[PaperTradeRecord],
    ledger_by_id: dict[str, PaperLedgerItem],
    validation_by_id: dict[str, PaperValidationItem],
) -> list[PaperEventTimelineItem]:
    events: list[PaperEventTimelineItem] = []
    for trade in trades:
        ledger_item = ledger_by_id.get(trade.trade_id)
        validation_item = validation_by_id.get(trade.trade_id)
        events.append(
            PaperEventTimelineItem(
                event_id=f"{trade.trade_id}:signal",
                trade_id=trade.trade_id,
                instrument_id=trade.instrument_id,
                strategy_id=trade.strategy_id,
                event_date=trade.signal_date,
                event_type="signal",
                title="生成推荐",
                description="推荐进入模拟观察，等待触发价确认。",
                status=trade.status,
                price=trade.trigger_price,
                pnl=ledger_item.total_pnl if ledger_item is not None else Decimal("0"),
                return_pct=ledger_item.return_pct if ledger_item is not None else None,
            )
        )
        if trade.entry_date is not None and trade.entry_price is not None:
            events.append(
                PaperEventTimelineItem(
                    event_id=f"{trade.trade_id}:entry",
                    trade_id=trade.trade_id,
                    instrument_id=trade.instrument_id,
                    strategy_id=trade.strategy_id,
                    event_date=trade.entry_date,
                    event_type="entry",
                    title="触发买点",
                    description="价格触发计划买点，按模拟盘仓位和成本规则入账。",
                    status=trade.status,
                    price=trade.entry_price,
                    pnl=ledger_item.total_pnl if ledger_item is not None else Decimal("0"),
                    return_pct=ledger_item.return_pct if ledger_item is not None else None,
                )
            )
        if trade.exit_date is not None and trade.exit_price is not None:
            events.append(
                PaperEventTimelineItem(
                    event_id=f"{trade.trade_id}:exit",
                    trade_id=trade.trade_id,
                    instrument_id=trade.instrument_id,
                    strategy_id=trade.strategy_id,
                    event_date=trade.exit_date,
                    event_type="exit",
                    title=_paper_exit_title(trade.status),
                    description=validation_item.next_action if validation_item is not None else _paper_next_action(trade),
                    status=trade.status,
                    price=trade.exit_price,
                    pnl=ledger_item.total_pnl if ledger_item is not None else Decimal("0"),
                    return_pct=ledger_item.return_pct if ledger_item is not None else None,
                )
            )
        elif trade.latest_date is not None:
            events.append(
                PaperEventTimelineItem(
                    event_id=f"{trade.trade_id}:mark",
                    trade_id=trade.trade_id,
                    instrument_id=trade.instrument_id,
                    strategy_id=trade.strategy_id,
                    event_date=trade.latest_date,
                    event_type="mark",
                    title="更新估值" if trade.status == "open" else "继续等待",
                    description=validation_item.next_action if validation_item is not None else _paper_next_action(trade),
                    status=trade.status,
                    price=trade.latest_price,
                    pnl=ledger_item.total_pnl if ledger_item is not None else Decimal("0"),
                    return_pct=ledger_item.return_pct if ledger_item is not None else None,
                )
            )
    order = {"exit": 4, "mark": 3, "entry": 2, "signal": 1}
    return sorted(
        events,
        key=lambda item: (item.event_date, order.get(item.event_type, 0), item.trade_id),
        reverse=True,
    )[:60]


def _paper_exit_title(status: str) -> str:
    return {
        "target_1_hit": "目标命中",
        "stopped": "触发止损",
        "time_exit": "时间退出",
        "missed_entry": "错过买点",
    }.get(status, "结束跟踪")


def _daily_report_item(
    trade: PaperTradeRecord,
    ledger_by_id: dict[str, PaperLedgerItem],
    validation_by_id: dict[str, PaperValidationItem],
) -> PaperDailyReportItem:
    ledger_item = ledger_by_id.get(trade.trade_id)
    validation_item = validation_by_id.get(trade.trade_id)
    return PaperDailyReportItem(
        trade_id=trade.trade_id,
        instrument_id=trade.instrument_id,
        strategy_id=trade.strategy_id,
        status=trade.status,
        signal_date=trade.signal_date,
        entry_date=trade.entry_date,
        exit_date=trade.exit_date,
        return_pct=(
            ledger_item.return_pct
            if ledger_item is not None
            else trade.realized_return_pct
            if trade.realized_return_pct is not None
            else trade.unrealized_return_pct
        ),
        pnl=ledger_item.total_pnl if ledger_item is not None else Decimal("0"),
        next_action=validation_item.next_action if validation_item is not None else _paper_next_action(trade),
        notes=trade.notes,
    )


def _paper_daily_benchmark(
    *,
    total_return_pct: float,
    benchmark_items: list[Mapping[str, object]],
) -> PaperDailyBenchmark:
    items: list[PaperDailyBenchmarkItem] = []
    for raw in benchmark_items:
        name = str(raw.get("name") or raw.get("benchmark_id") or "基准")
        return_pct = _float_mapping_value(raw, "return_pct")
        excess_return_pct = _float_mapping_value(raw, "excess_return_pct")
        items.append(
            PaperDailyBenchmarkItem(
                benchmark_id=str(raw.get("benchmark_id")) if raw.get("benchmark_id") is not None else None,
                name=name,
                return_pct=return_pct,
                excess_return_pct=excess_return_pct,
                summary=_paper_benchmark_item_summary(name, return_pct, excess_return_pct),
            )
        )
    best = next((item for item in items if item.excess_return_pct is not None), None)
    if best is None:
        summary = "暂无指数基准数据，先看模拟盘绝对收益和回撤。"
    elif best.excess_return_pct is not None and best.excess_return_pct >= 0:
        summary = f"模拟盘相对{best.name}超额 {best.excess_return_pct:+.2f}%。"
    else:
        summary = f"模拟盘相对{best.name}落后 {best.excess_return_pct:+.2f}%。"
    return PaperDailyBenchmark(
        total_return_pct=total_return_pct,
        items=items,
        summary=summary,
    )


def _paper_benchmark_item_summary(
    name: str,
    return_pct: float | None,
    excess_return_pct: float | None,
) -> str:
    if return_pct is None or excess_return_pct is None:
        return f"{name} 数据不足。"
    return f"{name} {return_pct:+.2f}%，模拟盘超额 {excess_return_pct:+.2f}%。"


def _next_trade_day_focus(
    *,
    new_opportunities: list[PaperDailyReportItem],
    holdings: list[PaperDailyReportItem],
    closed_today: list[PaperDailyReportItem],
    validation: PaperValidationResult,
) -> list[str]:
    focus: list[str] = []
    if new_opportunities:
        focus.append(f"{len(new_opportunities)} 个新增机会等待触发，未到买点不追高。")
    if holdings:
        focus.append(f"{len(holdings)} 个持仓继续检查止损、目标价和推荐变弱信号。")
    if closed_today:
        focus.append(f"{len(closed_today)} 笔今日闭环，已计入胜率和收益曲线。")
    if validation.sample_age.days_to_next_10d is not None:
        focus.append(f"最近 10 日验证样本还差 {validation.sample_age.days_to_next_10d} 天成熟。")
    if not focus:
        focus.append("暂无需要动作的模拟记录，等待下一次扫描产生新机会。")
    return focus[:4]


def _paper_next_action(trade: PaperTradeRecord) -> str:
    if trade.status == "pending":
        return "等待触发价。"
    if trade.status == "open":
        return "跟踪止损和目标价。"
    if trade.status in CLOSED_STATUSES:
        return "已闭环，纳入统计。"
    return "继续观察。"


def _float_mapping_value(raw: Mapping[str, object], key: str) -> float | None:
    value = raw.get(key)
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


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
    if status == "pending" and trade_date <= trade.signal_date:
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
                        "A股交易规则：信号日不回填买入，等待下个交易日确认。",
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
            if trade_date < entry_date:
                continue
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


def _try_evaluate_trade_with_minutes(
    repo: PaperTradingRepository,
    provider: MarketDataProvider,
    trade: PaperTradeRecord,
    *,
    max_holding_days: int,
    max_entry_wait_days: int,
    as_of: datetime,
) -> tuple[dict[str, object] | None, int, int]:
    getter = getattr(provider, "get_minute_bars", None)
    if getter is None or not _is_a_share_trade(trade):
        return None, 0, 0
    source_context = repo.get_trade_source_context(trade.source_snapshot_id)
    signal_datetime = _trade_signal_datetime(trade, source_context)
    if signal_datetime is None:
        return None, 0, 0
    start = signal_datetime
    end = _a_share_local_datetime(as_of).replace(tzinfo=None)
    try:
        minute_bars = getter([trade.instrument_id], start, end)
    except Exception:
        return None, 1, 0
    if minute_bars.empty:
        return None, 1, 0
    update = _evaluate_trade_with_minutes(
        trade,
        minute_bars,
        max_holding_days=max_holding_days,
        max_entry_wait_days=max_entry_wait_days,
        signal_datetime=signal_datetime,
        no_chase_above=_trade_no_chase_above(trade, source_context),
    )
    return update, 1, len(minute_bars)


def _evaluate_trade_with_minutes(
    trade: PaperTradeRecord,
    minute_bars: pd.DataFrame,
    *,
    max_holding_days: int,
    max_entry_wait_days: int,
    signal_datetime: datetime,
    no_chase_above: Decimal | None,
) -> dict[str, object]:
    ordered = minute_bars.sort_values("timestamp").reset_index(drop=True)
    ordered["timestamp"] = pd.to_datetime(ordered["timestamp"], errors="coerce")
    ordered = ordered.dropna(subset=["timestamp"])
    ordered = ordered[ordered["timestamp"] > signal_datetime]
    if ordered.empty:
        return {
            "status": trade.status,
            "holding_days": trade.holding_days,
            "notes": _append_note(trade.notes, "分钟数据尚未覆盖推荐后的交易时间。"),
        }
    entry_date = trade.entry_date
    entry_price = trade.entry_price
    status = trade.status
    notes = trade.notes

    for _, row in ordered.iterrows():
        timestamp = row["timestamp"].to_pydatetime()
        trade_date = timestamp.date()
        open_price = Decimal(str(row["open"]))
        high = Decimal(str(row["high"]))
        low = Decimal(str(row["low"]))
        close = Decimal(str(row["close"]))

        if status == "pending":
            wait_days = max((trade_date - trade.signal_date).days, 0)
            if high >= trade.trigger_price:
                missed = _minute_entry_missed(
                    trigger_price=trade.trigger_price,
                    no_chase_above=no_chase_above,
                    open_price=open_price,
                    low=low,
                )
                if missed:
                    return {
                        "status": "missed_entry",
                        "latest_date": trade_date,
                        "latest_price": close,
                        "exit_date": trade_date,
                        "exit_price": close,
                        "realized_return_pct": Decimal("0"),
                        "holding_days": 0,
                        "notes": _append_note(
                            notes,
                            "分钟线显示价格已超过追高上限，放弃本次模拟买入。",
                        ),
                    }
                status = "open"
                entry_date = trade_date
                entry_price = _minute_entry_price(
                    trigger_price=trade.trigger_price,
                    open_price=open_price,
                    low=low,
                )
                notes = _append_note(notes, "分钟线确认触发价，模拟开仓。")
            elif wait_days > max_entry_wait_days:
                return {
                    "status": "time_exit",
                    "latest_date": trade_date,
                    "latest_price": close,
                    "exit_date": trade_date,
                    "exit_price": close,
                    "realized_return_pct": Decimal("0"),
                    "holding_days": 0,
                    "notes": "买点等待超时，未开仓退出跟踪。",
                }
            else:
                continue

        if status == "open" and entry_date is not None and entry_price is not None:
            if trade_date < entry_date:
                continue
            holding_days = max((trade_date - entry_date).days, 0)
            if trade_date == entry_date:
                notes = _append_note(notes, "A股 T+1：买入当日不模拟卖出。")
                continue
            if trade.initial_stop is not None and low <= trade.initial_stop:
                return _closed_update(
                    status="stopped",
                    entry_date=entry_date,
                    entry_price=entry_price,
                    exit_date=trade_date,
                    exit_price=trade.initial_stop,
                    latest_price=close,
                    holding_days=holding_days,
                    notes="分钟线触及初始止损，模拟离场。",
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
                    notes="分钟线触及第一目标价，模拟止盈。",
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
                    notes="达到最长持有窗口，按分钟收盘价模拟退出。",
                )

    latest = ordered.iloc[-1]
    latest_date = latest["timestamp"].date()
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
        "notes": _append_note(notes, "分钟线未到触发价，继续等待。"),
    }


def _trade_signal_datetime(
    trade: PaperTradeRecord,
    source_context: PaperTradeSourceContext | None,
) -> datetime | None:
    if source_context is None:
        return None
    value = source_context.created_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(A_SHARE_TZ).replace(tzinfo=None)


def _trade_no_chase_above(
    trade: PaperTradeRecord,
    source_context: PaperTradeSourceContext | None,
) -> Decimal | None:
    value = None
    if source_context is not None:
        entry_plan = source_context.card.get("entry_plan")
        if isinstance(entry_plan, dict):
            value = entry_plan.get("no_chase_above")
    if value is None:
        return (trade.trigger_price * Decimal("1.03")).quantize(Decimal("0.0001"))
    try:
        return Decimal(str(value))
    except Exception:
        return (trade.trigger_price * Decimal("1.03")).quantize(Decimal("0.0001"))


def _minute_entry_missed(
    *,
    trigger_price: Decimal,
    no_chase_above: Decimal | None,
    open_price: Decimal,
    low: Decimal,
) -> bool:
    if no_chase_above is None:
        return False
    return low > no_chase_above or (open_price > no_chase_above and low > trigger_price)


def _minute_entry_price(
    *,
    trigger_price: Decimal,
    open_price: Decimal,
    low: Decimal,
) -> Decimal:
    return trigger_price if low <= trigger_price else open_price


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
