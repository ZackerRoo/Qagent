from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Literal, Mapping
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import BaseModel, Field

from qagent.execution.models import (
    AShareExecutionRules,
    MarketEvent,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from qagent.execution.rules import (
    apply_slippage as execution_apply_slippage,
    fee_breakdown as execution_fee_breakdown,
    is_one_price_limit_blocked,
    is_tick_aligned,
    match_base_price,
    money as execution_money,
    participation_capacity,
    round_lot,
)
from qagent.market.calendars import trading_sessions_elapsed
from qagent.paper_trading.admission import evaluate_paper_snapshot_admission
from qagent.providers.base import MarketDataProvider
from qagent.storage.paper import (
    PaperAccountSettings,
    PaperExecutionFacts,
    PaperExecutionLegFacts,
    PaperTradeAdmissionProof,
    PaperTradeEventMetadata,
    PaperTradeRecord,
    PaperTradeSourceContext,
    PaperTradingRepository,
)
from qagent.storage.repository import OpportunitySnapshotRecord


OPEN_STATUSES = {"pending", "open"}
CLOSED_STATUSES = {
    "target_1_hit",
    "stopped",
    "time_exit",
    "missed_entry",
    "replaced",
    "invalidated",
}
EXECUTED_CLOSED_STATUSES = {"target_1_hit", "stopped", "time_exit"}
A_SHARE_TZ = ZoneInfo("Asia/Shanghai")
A_SHARE_MORNING_START = time(9, 30)
A_SHARE_MORNING_END = time(11, 30)
A_SHARE_AFTERNOON_START = time(13, 0)
A_SHARE_AFTERNOON_END = time(15, 0)
PAPER_RISK_PROBE_NOTE = "风控恢复探针"
_ENTRY_FILL_UPDATE_KEY = "__paper_entry_fill"
_EXIT_FILL_UPDATE_KEY = "__paper_exit_fill"
_DEFERRED_FILL_UPDATE_KEY = "__paper_deferred_fills"
_TERMINAL_REASON_UPDATE_KEY = "__paper_terminal_reason"
_LEGACY_MARKET_REDUCED_NOTE = "防守行情研究仓位"
_RESEARCH_SHADOW_ADMISSION_SOURCES = frozenset({"ranking_v4_shadow"})


@dataclass(frozen=True, slots=True)
class _PaperExecutionContext:
    rules: AShareExecutionRules
    allocation: Decimal
    source_context: PaperTradeSourceContext | None


@dataclass(frozen=True, slots=True)
class _PaperMatchedFill:
    market_event_id: str
    side: OrderSide
    trade_date: date
    occurred_at: datetime
    base_price: Decimal
    price: Decimal
    quantity: int
    rules: AShareExecutionRules
    source: str = "unified_execution"


@dataclass(frozen=True, slots=True)
class _PaperMatchResult:
    triggered: bool
    fill: _PaperMatchedFill | None = None
    reason: str | None = None


class PaperSeedResult(BaseModel):
    scanned: int
    created: int
    skipped: int
    skipped_unaffordable: int = 0


class PaperTradingSummary(BaseModel):
    total: int
    pending: int
    open: int
    closed: int
    missed_entry_count: int
    replaced_count: int
    invalidated_count: int
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
    missed_entry_count: int
    replaced_count: int
    invalidated_count: int
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
    missed_entry_count: int
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
    missed_entry_count: int
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
    missed_entry_count: int
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
    replaced_count: int
    invalidated_count: int
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


class PaperTradeDiagnostic(BaseModel):
    trade_id: str
    instrument_id: str
    instrument_label: str
    strategy_id: str | None
    status: str
    return_pct: float | None
    root_cause: str
    root_cause_label: str
    severity: str
    factor_signals: list[str]
    source_industry: str = "unknown"
    source_themes: list[str] = Field(default_factory=list)
    source_market_regime: str = "unknown"
    source_context_status: str = "unknown"
    execution_evidence_status: str = "legacy_unverified"
    execution_evidence_label: str = "旧成交记录"
    strategy_attribution_eligible: bool = False
    evidence: list[str]
    action: str


class PaperExecutionEvidenceSummary(BaseModel):
    closed_trades: int = 0
    audited_closed_trades: int = 0
    partial_closed_trades: int = 0
    legacy_closed_trades: int = 0
    comparable_closed_trades: int = 0
    audited_open_entries: int = 0
    verdict: str = "building_sample"
    summary: str = "暂无完整闭环成交，继续积累统一执行样本。"


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
            replaced_count=0,
            invalidated_count=0,
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
    execution_evidence: PaperExecutionEvidenceSummary = Field(
        default_factory=PaperExecutionEvidenceSummary
    )
    trade_diagnostics: list[PaperTradeDiagnostic] = Field(default_factory=list)
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
    notes: str = "",
    allocation_multiplier: Decimal = Decimal("1.0"),
    admission_repo: object | None = None,
    admission_mode: str = "automatic",
) -> PaperSeedResult:
    if allocation_multiplier <= 0 or allocation_multiplier > 1:
        raise ValueError("allocation_multiplier must be between 0 and 1")
    created = 0
    skipped = 0
    skipped_unaffordable = 0
    existing_trades = repo.list_trades(limit=1000, provider=provider)
    existing = {trade.source_snapshot_id for trade in existing_trades}
    active_instruments = {
        trade.instrument_id for trade in existing_trades if trade.status in OPEN_STATUSES
    }
    active_count = sum(1 for trade in existing_trades if trade.status in OPEN_STATUSES)
    account_settings = repo.get_account_settings()
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
        if not paper_snapshot_price_basis_is_consistent(snapshot):
            skipped += 1
            continue
        if (
            max_signal_age_days is not None
            and (current_date - signal_date).days > max_signal_age_days
        ):
            skipped += 1
            continue
        admission = evaluate_paper_snapshot_admission(
            admission_repo or repo,
            snapshot,
            provider=provider,
            mode=admission_mode,
            allocation_multiplier=allocation_multiplier,
        )
        if not admission.eligible:
            skipped += 1
            continue
        if not paper_snapshot_round_lot_is_affordable(
            snapshot,
            account_settings,
            allocation_multiplier,
        ):
            skipped += 1
            skipped_unaffordable += 1
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
            notes=notes,
            allocation_multiplier=allocation_multiplier,
            admission_proof=PaperTradeAdmissionProof(
                admission_source=admission.admission_source,
                production_identity_digest=admission.production_identity_digest,
                production_batch_fact_digest=admission.production_batch_fact_digest,
                production_selection_item_digest=(admission.production_selection_item_digest),
                release_proof_digest=admission.release_proof_digest,
            ),
        )
        created += 1
        active_instruments.add(snapshot.instrument_id)
    return PaperSeedResult(
        scanned=len(snapshots),
        created=created,
        skipped=skipped,
        skipped_unaffordable=skipped_unaffordable,
    )


def paper_snapshot_price_basis_is_consistent(
    snapshot: OpportunitySnapshotRecord,
    *,
    max_gap_ratio: Decimal | None = None,
) -> bool:
    trigger = getattr(snapshot, "trigger_price", None)
    latest = _snapshot_reference_price(snapshot)
    instrument_id = str(getattr(snapshot, "instrument_id", ""))
    if trigger is None or trigger <= 0:
        return False
    if latest is None or latest <= 0:
        return not instrument_id.startswith("CN:")
    gap_limit = max_gap_ratio or paper_price_basis_gap_limit(instrument_id)
    return abs(trigger - latest) / trigger <= gap_limit


def paper_price_basis_gap_limit(instrument_id: str) -> Decimal:
    if not instrument_id.startswith("CN:"):
        return Decimal("0.45")
    code = instrument_id.split(":", 1)[1].split(".", 1)[0]
    if code.startswith(("4", "8", "92")):
        return Decimal("0.32")
    if code.startswith(("300", "301", "688", "689")):
        return Decimal("0.22")
    return Decimal("0.12")


def update_paper_trades(
    repo: PaperTradingRepository,
    provider: MarketDataProvider,
    provider_mode: str | None = None,
    max_holding_days: int = 20,
    max_entry_wait_days: int = 10,
    as_of: datetime | None = None,
) -> PaperUpdateResult:
    trades = repo.list_trades(limit=1000, provider=provider_mode)
    account_settings = repo.get_account_settings()
    invalidated_before = sum(1 for trade in trades if trade.status == "invalidated")
    repaired_replaced_statuses = _repair_replaced_trade_statuses(repo, trades)
    if repaired_replaced_statuses:
        trades = repo.list_trades(limit=1000, provider=provider_mode)
    repaired_invalid_dates = _repair_impossible_trade_dates(repo, trades)
    if repaired_invalid_dates:
        trades = repo.list_trades(limit=1000, provider=provider_mode)
    restored_standard_allocations = _restore_pending_market_reduced_allocations(repo, trades)
    if restored_standard_allocations:
        trades = repo.list_trades(limit=1000, provider=provider_mode)
    active = [trade for trade in trades if trade.status in OPEN_STATUSES]
    execution_time = _a_share_local_datetime(as_of)
    execution_session = _a_share_execution_session(execution_time)
    fills_deferred = 0
    minute_checked = 0
    minute_rows = 0
    daily_fallback_checked = 0
    daily_fallback_rows = 0
    unaffordable_missed = 0
    for trade in active:
        source_context = repo.get_trade_source_context(trade.source_snapshot_id)
        execution_context = _paper_execution_context(
            trade,
            account_settings,
            source_context,
        )
        if (
            trade.status == "pending"
            and execution_context is not None
            and _paper_entry_quantity(trade, execution_context) <= 0
        ):
            _persist_paper_trade_update(
                repo,
                trade,
                _unaffordable_round_lot_update(
                    trade,
                    execution_context,
                    invalidated_on=execution_time.date(),
                ),
                execution_context,
            )
            unaffordable_missed += 1
            continue
        minute_update, checked, rows, minute_deferred = _try_evaluate_trade_with_minutes(
            repo,
            provider,
            trade,
            max_holding_days=max_holding_days,
            max_entry_wait_days=max_entry_wait_days,
            as_of=execution_time,
            source_context=source_context,
            execution_context=execution_context,
        )
        minute_checked += checked
        minute_rows += rows
        fills_deferred += minute_deferred
        if minute_update is not None:
            _persist_paper_trade_update(
                repo,
                trade,
                minute_update,
                execution_context,
            )
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
            source_latest_close=_source_context_latest_close(source_context),
            execution_context=execution_context,
        )
        fills_deferred += deferred
        _persist_paper_trade_update(repo, trade, updated, execution_context)
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
        "paper_unaffordable_pending_missed": str(unaffordable_missed),
        "paper_repaired_invalid_dates": str(repaired_invalid_dates),
        "paper_repaired_replaced_statuses": str(repaired_replaced_statuses),
        "paper_restored_standard_allocations": str(restored_standard_allocations),
        "paper_price_basis_invalidated": str(
            max(
                sum(1 for trade in refreshed if trade.status == "invalidated") - invalidated_before,
                0,
            )
        ),
    }
    if provider_errors:
        data_health["errors"] = " | ".join(provider_errors[:3])
    return PaperUpdateResult(
        summary=summarize_paper_trades(refreshed, reporting_scope="all"),
        trades=refreshed,
        data_health=data_health,
    )


def _repair_replaced_trade_statuses(
    repo: PaperTradingRepository,
    trades: list[PaperTradeRecord],
) -> int:
    repaired = 0
    for trade in trades:
        if trade.status != "missed_entry" or "候补替换" not in trade.notes:
            continue
        repo.update_trade(
            trade.trade_id,
            status="replaced",
            realized_return_pct=None,
        )
        repaired += 1
    return repaired


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
        repair_note = _append_note(
            trade.notes,
            "修复异常日期：历史记录出现离场早于入场，已恢复为持仓重新评估。",
        )
        event_metadata = None
        if trade.execution_facts is not None and trade.execution_facts.exit is not None:
            event_metadata = PaperTradeEventMetadata(
                note=repair_note,
                reason_code="paper_trade.execution_facts.invalid_exit_cleared",
                source="paper_repair",
                execution_facts=trade.execution_facts.model_copy(update={"exit": None}),
            )
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
            notes=repair_note,
            event_metadata=event_metadata,
        )
        repaired += 1
    return repaired


def _restore_pending_market_reduced_allocations(
    repo: PaperTradingRepository,
    trades: list[PaperTradeRecord],
) -> int:
    restored = 0
    for trade in trades:
        if (
            trade.status != "pending"
            or trade.allocation_multiplier >= Decimal("1")
            or _LEGACY_MARKET_REDUCED_NOTE not in trade.notes
        ):
            continue
        note = _append_note(
            trade.notes,
            "市场状态改为仅供研究归因，未成交订单恢复标准仓位。",
        )
        repo.update_trade(
            trade.trade_id,
            allocation_multiplier=Decimal("1"),
            notes=note,
            event_metadata=PaperTradeEventMetadata(
                idempotency_key=f"paper-policy:{trade.trade_id}:standard-allocation",
                reason_code="paper_trade.market_risk_sizing_removed",
                note=note,
                source="paper_policy_migration",
            ),
        )
        restored += 1
    return restored


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
        "paper_execution_contract": "qagent.execution.a_share_v1",
    }


def summarize_paper_trades(
    trades: list[PaperTradeRecord],
    *,
    reporting_scope: Literal["official", "legacy", "all"] = "official",
    authenticated_trade_ids: set[str] | None = None,
) -> PaperTradingSummary:
    trades, _ = _paper_reporting_scope(
        trades,
        reporting_scope=reporting_scope,
        authenticated_trade_ids=authenticated_trade_ids,
    )
    closed = [trade for trade in trades if _is_executed_closed_trade(trade)]
    winning = [
        trade
        for trade in closed
        if trade.realized_return_pct is not None and trade.realized_return_pct > 0
    ]
    realized = [
        trade.realized_return_pct for trade in closed if trade.realized_return_pct is not None
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
        missed_entry_count=sum(1 for trade in trades if trade.status == "missed_entry"),
        replaced_count=sum(1 for trade in trades if trade.status == "replaced"),
        invalidated_count=sum(1 for trade in trades if trade.status == "invalidated"),
        target_hit_count=sum(1 for trade in trades if trade.status == "target_1_hit"),
        stopped_count=sum(1 for trade in trades if trade.status == "stopped"),
        time_exit_count=sum(1 for trade in trades if trade.status == "time_exit"),
        win_rate=round(len(winning) / len(closed), 4) if closed else None,
        average_realized_return_pct=round(sum(realized) / len(realized), 4) if realized else None,
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
    reporting_scope: Literal["official", "legacy", "all"] = "official",
    authenticated_trade_ids: set[str] | None = None,
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

    trades, reporting_health = _paper_reporting_scope(
        trades,
        reporting_scope=reporting_scope,
        authenticated_trade_ids=authenticated_trade_ids,
    )
    allocation_per_trade = _money(initial_capital * allocation_per_trade_pct / Decimal("100"))
    items: list[PaperLedgerItem] = []
    planned_capital = Decimal("0")

    for trade in trades:
        trade_allocation = _trade_allocation(trade, allocation_per_trade)
        item = _ledger_item(trade, trade_allocation)
        items.append(item)
        planned_capital += trade_allocation if trade.status == "pending" else Decimal("0")

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
        if _is_executed_closed_item(item) and item.return_pct is not None
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
            closed_trades=sum(1 for trade in trades if _is_executed_closed_trade(trade)),
            missed_entry_count=sum(1 for trade in trades if trade.status == "missed_entry"),
            replaced_count=sum(1 for trade in trades if trade.status == "replaced"),
            invalidated_count=sum(1 for trade in trades if trade.status == "invalidated"),
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
            **reporting_health,
            "ledger_method": "chronological_cash_ledger",
            "ledger_execution_facts": str(
                sum(1 for trade in trades if trade.execution_facts is not None)
            ),
            "ledger_execution_facts_precedence": "event_snapshot_then_legacy_fallback",
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
    reporting_trade_ids = {item.trade_id for item in ledger.items}
    all_trades = trades
    trades = [trade for trade in all_trades if trade.trade_id in reporting_trade_ids]
    as_of = as_of or _validation_as_of(trades)
    validation_trades = [
        trade for trade in trades if trade.status not in {"replaced", "invalidated"}
    ]
    ledger_items = {item.trade_id: item for item in ledger.items}
    items = [
        _validation_item(
            trade=trade,
            ledger_item=ledger_items.get(trade.trade_id),
            allocation_per_trade=ledger.summary.allocation_per_trade,
            as_of=as_of,
        )
        for trade in validation_trades
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
            pending_trades=sum(1 for trade in validation_trades if trade.status == "pending"),
            open_trades=sum(1 for trade in validation_trades if trade.status == "open"),
            closed_trades=sum(1 for trade in validation_trades if _is_executed_closed_trade(trade)),
            missed_entry_count=sum(
                1 for trade in validation_trades if trade.status == "missed_entry"
            ),
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
            "validation_executed_items": str(
                sum(1 for item in items if item.entry_date is not None)
            ),
            "validation_missed_entries": str(
                sum(1 for item in items if item.status == "missed_entry")
            ),
            "validation_replaced_excluded": str(
                sum(1 for trade in trades if trade.status == "replaced")
            ),
            "validation_invalidated_excluded": str(
                sum(1 for trade in trades if trade.status == "invalidated")
            ),
            "validation_non_official_excluded": str(len(all_trades) - len(trades)),
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
    source_context_by_trade: Mapping[str, PaperTradeSourceContext] | None = None,
) -> PaperDailyReport:
    all_trades = trades
    reporting_trade_ids = {item.trade_id for item in ledger.items}
    trades = [trade for trade in all_trades if trade.trade_id in reporting_trade_ids]
    report_date = as_of or _validation_as_of(trades)
    ledger_by_id = {item.trade_id: item for item in ledger.items}
    validation_by_id = {item.trade_id: item for item in validation.items}
    new_opportunities = [
        _daily_report_item(trade, ledger_by_id, validation_by_id)
        for trade in trades
        if trade.signal_date == report_date and trade.status not in {"replaced", "invalidated"}
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
        if trade.exit_date == report_date and _is_executed_closed_trade(trade)
    ]
    benchmark = _paper_daily_benchmark(
        total_return_pct=ledger.summary.total_return_pct,
        benchmark_items=benchmark_items or [],
    )
    market_context = _paper_market_context(benchmark)
    trigger_quality = _paper_trigger_quality(trades)
    source_contexts = source_context_by_trade or {}
    trade_by_id = {trade.trade_id: trade for trade in trades}
    trade_diagnostics = _paper_trade_diagnostics(
        ledger.items,
        source_context_by_trade=source_contexts,
        trade_by_id=trade_by_id,
    )
    return PaperDailyReport(
        report_date=report_date,
        summary=PaperDailyReportSummary(
            total_trades=sum(
                1 for trade in trades if trade.status not in {"replaced", "invalidated"}
            ),
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
            source_context_by_trade=source_contexts,
            trade_diagnostics=trade_diagnostics,
        ),
        execution_evidence=_paper_execution_evidence_summary(
            trades,
            source_context_by_trade=source_contexts,
        ),
        trade_diagnostics=trade_diagnostics,
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
            **_paper_execution_evidence_health(trades, source_contexts),
            "paper_daily_report_date": report_date.isoformat(),
            "paper_daily_report_trades": str(len(trades)),
            "paper_daily_report_non_official_excluded": str(len(all_trades) - len(trades)),
            "paper_daily_report_benchmarks": str(len(benchmark.items)),
            **_paper_source_context_health(ledger.items, source_contexts),
        },
    )


def _paper_reporting_scope(
    trades: list[PaperTradeRecord],
    *,
    reporting_scope: Literal["official", "legacy", "all"] = "official",
    authenticated_trade_ids: set[str] | None = None,
) -> tuple[list[PaperTradeRecord], dict[str, str]]:
    authenticated_ids = authenticated_trade_ids or set()
    official = [trade for trade in trades if trade.trade_id in authenticated_ids]
    invalid_official_claims = sum(
        trade.admission_source == "ranking_v3_production"
        and trade.trade_id not in authenticated_ids
        for trade in trades
    )
    legacy_manual = sum(trade.admission_source == "legacy_manual" for trade in trades)
    research_shadow = sum(
        trade.admission_source in _RESEARCH_SHADOW_ADMISSION_SOURCES for trade in trades
    )
    legacy_unknown = sum(
        trade.admission_source
        not in {
            "ranking_v3_production",
            "legacy_manual",
            *_RESEARCH_SHADOW_ADMISSION_SOURCES,
        }
        for trade in trades
    ) + invalid_official_claims
    legacy = [trade for trade in trades if trade.trade_id not in authenticated_ids]
    reporting_trades = (
        official
        if reporting_scope == "official"
        else legacy
        if reporting_scope == "legacy"
        else trades
    )
    return reporting_trades, {
        "paper_reporting_scope": {
            "official": "ranking_v3_production",
            "legacy": "legacy_only",
            "all": "operational_all",
        }[reporting_scope],
        "paper_reporting_fail_closed": "true",
        "paper_reporting_official": str(len(official)),
        "paper_reporting_research_shadow": str(research_shadow),
        "paper_reporting_legacy_manual": str(legacy_manual),
        "paper_reporting_legacy_unknown": str(legacy_unknown),
        "paper_reporting_invalid_official_claims": str(invalid_official_claims),
        "paper_reporting_excluded": str(len(trades) - len(reporting_trades)),
    }


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
        if item.status in {"replaced", "invalidated"}:
            continue
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
    returns = [
        item.return_pct
        for item in items
        if item.entry_date is not None and item.return_pct is not None
    ]
    closed = [item for item in items if _is_executed_closed_item(item)]
    positive = [value for value in returns if value > 0]
    negative = [value for value in returns if value < 0]
    total_pnl = _money(sum((item.total_pnl for item in items), Decimal("0")))
    effective_items = [item for item in items if item.entry_date is not None]
    capital_base = sum((item.capital_allocated for item in effective_items), Decimal("0"))
    total_return_pct = (
        round(float(total_pnl / capital_base * Decimal("100")), 4) if capital_base > 0 else None
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
    evaluated_trades = [
        trade for trade in trades if trade.status not in {"replaced", "invalidated"}
    ]
    total = len(evaluated_trades)
    replaced = sum(1 for trade in trades if trade.status == "replaced")
    invalidated = sum(1 for trade in trades if trade.status == "invalidated")
    pending = sum(1 for trade in evaluated_trades if trade.status == "pending")
    triggered = sum(1 for trade in evaluated_trades if trade.entry_date is not None)
    missed = sum(1 for trade in evaluated_trades if trade.status == "missed_entry")
    no_chase_missed = sum(
        1
        for trade in evaluated_trades
        if trade.status == "missed_entry"
        and ("追高" in trade.notes or "no-chase" in trade.notes.lower())
    )
    stopped = sum(1 for trade in evaluated_trades if trade.status == "stopped")
    target_hit = sum(1 for trade in evaluated_trades if trade.status == "target_1_hit")
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
        replaced_count=replaced,
        invalidated_count=invalidated,
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
    if summary.closed_trades >= 3 and summary.win_rate is not None and summary.win_rate <= 0.25:
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
            max_new_entries=max(
                summary.max_positions - summary.open_trades - summary.pending_trades, 0
            ),
            position_size_multiplier=1.0,
        )

    # Research-paper performance is diagnostic evidence, not an execution
    # throttle. Reducing entries after a short drawdown would confound stock
    # selection, sizing, and admission frequency in the forward sample.
    # Operational constraints such as no slot or unavailable market data still
    # block execution elsewhere in the matching and admission pipeline.
    available_slots = max(
        summary.max_positions - summary.open_trades - summary.pending_trades,
        0,
    )
    if available_slots <= 0:
        return PaperRiskGateStatus(
            action="capacity_full",
            can_add_entries=False,
            title="模拟盘已满",
            reason="；".join(reasons) + "；当前没有可用仓位，等待退出或候补替换。",
            reasons=reasons + ["达到最大持仓数"],
            recovery_conditions=[
                "等待持仓止盈、止损或时间退出",
                "若出现更高质量候选，比较后替换低质量等待单",
            ],
            recovery_state="capacity_full",
            recovery_score=recovery_score,
            max_new_entries=0,
            position_size_multiplier=0.0,
        )

    return PaperRiskGateStatus(
        action="allow_new_entries",
        can_add_entries=True,
        title="账户表现需观察，新增保持标准规则",
        reason="；".join(reasons) + "；仅作为研究归因，合格候选仍按标准仓位和可用空位进入。",
        reasons=reasons,
        recovery_conditions=[
            f"可按剩余仓位一次新增 {available_slots} 笔，优先当前最高质量机会",
            "新增仓位按账户标准额度执行",
            "已有持仓继续按止损、止盈和 T+1 规则更新",
            "持续观察收益、回撤和触发质量，但不以此改变研究样本准入",
        ],
        recovery_state="observed",
        recovery_score=recovery_score,
        max_new_entries=available_slots,
        position_size_multiplier=1.0,
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


def _paper_risk_probe_state(ledger: PaperLedger) -> tuple[str, date | None]:
    """Return whether a recovery probe is active or cooling down.

    The gate is evaluated before the paper update in an automation cycle. A
    probe therefore needs an explicit marker so the next cycle does not keep
    adding one new trade while the first probe is still being evaluated.
    """
    probes = [item for item in ledger.items if PAPER_RISK_PROBE_NOTE in item.notes]
    if not probes:
        return "none", None
    if any(item.status in OPEN_STATUSES for item in probes):
        return "active", None

    completed_dates = [
        item.exit_date or item.latest_date
        for item in probes
        if item.status in EXECUTED_CLOSED_STATUSES
        and (item.exit_date or item.latest_date) is not None
    ]
    if not completed_dates:
        return "none", None
    last_completed = max(completed_dates)
    reference_dates = [
        item.latest_date or item.exit_date or item.entry_date or item.signal_date
        for item in ledger.items
        if (item.latest_date or item.exit_date or item.entry_date or item.signal_date) is not None
    ]
    reference_date = max(reference_dates, default=last_completed)
    if reference_date <= last_completed:
        return "cooldown", last_completed
    return "ready", last_completed


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
    source_context_by_trade: Mapping[str, PaperTradeSourceContext],
    trade_diagnostics: list[PaperTradeDiagnostic],
) -> list[PaperFailureAttributionItem]:
    grouped: dict[tuple[str, str, str], list[PaperLedgerItem]] = defaultdict(list)
    for item in items:
        if item.status in {"replaced", "invalidated"}:
            continue
        asset_type = _paper_asset_type(item.instrument_id, asset_type_by_instrument)
        grouped[("asset", asset_type, _paper_asset_label(asset_type))].append(item)
        grouped[
            ("strategy", item.strategy_id or "unknown", item.strategy_id or "未分类策略")
        ].append(item)
        grouped[("status", item.status, _paper_status_label(item.status))].append(item)
        context = source_context_by_trade.get(item.trade_id)
        industry = context.industry if context is not None else "unknown"
        grouped[("industry", industry, _paper_context_label("industry", industry))].append(item)
        market_regime = context.market_regime if context is not None else "unknown"
        grouped[
            (
                "market_regime",
                market_regime,
                _paper_context_label("market_regime", market_regime),
            )
        ].append(item)
        factor_ids = context.factor_ids if context is not None else []
        for factor_id in factor_ids or ["unknown"]:
            grouped[("factor", factor_id, _paper_context_label("factor", factor_id))].append(item)
        for signal in _paper_source_signals(context.card if context else {}):
            grouped[("signal", signal, _paper_signal_label(signal))].append(item)

    item_by_id = {item.trade_id: item for item in items}
    for diagnostic in trade_diagnostics:
        item = item_by_id.get(diagnostic.trade_id)
        if item is None or item.status in {"replaced", "invalidated"}:
            continue
        grouped[
            (
                "cause",
                diagnostic.root_cause,
                diagnostic.root_cause_label,
            )
        ].append(item)

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
    )


def _paper_source_context_health(
    items: list[PaperLedgerItem],
    source_context_by_trade: Mapping[str, PaperTradeSourceContext],
) -> dict[str, str]:
    contexts = [source_context_by_trade.get(item.trade_id) for item in items]
    return {
        "paper_pit_context_total": str(len(items)),
        "paper_pit_context_frozen": str(
            sum(context is not None and context.source_status == "frozen" for context in contexts)
        ),
        "paper_pit_context_legacy": str(
            sum(
                context is not None and context.source_status == "legacy_snapshot"
                for context in contexts
            )
        ),
        "paper_pit_context_unknown": str(
            sum(
                context is None
                or context.source_status == "unknown"
                or (
                    context.industry == "unknown"
                    and context.market_regime == "unknown"
                    and not context.factor_ids
                )
                for context in contexts
            )
        ),
    }


def _paper_execution_evidence_status(trade: PaperTradeRecord) -> str:
    facts = trade.execution_facts
    if facts is None:
        return "legacy_unverified"
    entry_is_unified = facts.entry.source == "unified_execution"
    if trade.status in EXECUTED_CLOSED_STATUSES:
        if facts.exit is None:
            return "partial"
        if entry_is_unified and facts.exit.source == "unified_execution":
            return "complete"
        return "partial"
    return "entry_audited" if entry_is_unified else "partial"


def _paper_execution_evidence_label(status: str) -> str:
    return {
        "complete": "完整统一成交事实",
        "partial": "部分成交事实",
        "entry_audited": "买入成交已审计",
        "legacy_unverified": "旧成交记录",
    }.get(status, "成交证据未知")


def _paper_execution_evidence_note(status: str) -> str:
    return {
        "complete": "买入和卖出均由统一执行引擎保存不可变成交事实",
        "partial": "仅部分成交由统一执行引擎保存，另一端来自旧记录推断",
        "entry_audited": "买入已保存统一执行事实，等待卖出后形成完整闭环",
        "legacy_unverified": "缺少统一执行引擎的买卖成交事实",
    }.get(status, "成交证据状态未知")


def _paper_execution_evidence_summary(
    trades: list[PaperTradeRecord],
    *,
    source_context_by_trade: Mapping[str, PaperTradeSourceContext],
) -> PaperExecutionEvidenceSummary:
    closed = [trade for trade in trades if _is_executed_closed_trade(trade)]
    statuses = {trade.trade_id: _paper_execution_evidence_status(trade) for trade in closed}
    audited = sum(status == "complete" for status in statuses.values())
    partial = sum(status == "partial" for status in statuses.values())
    legacy = sum(status == "legacy_unverified" for status in statuses.values())
    comparable = sum(
        statuses[trade.trade_id] == "complete"
        and (context := source_context_by_trade.get(trade.trade_id)) is not None
        and context.source_status == "frozen"
        for trade in closed
    )
    audited_open = sum(
        trade.status == "open"
        and _paper_execution_evidence_status(trade) == "entry_audited"
        for trade in trades
    )
    if not closed:
        verdict = "building_sample"
        summary = "暂无完整闭环成交，继续积累统一执行样本。"
    elif comparable == len(closed):
        verdict = "fully_audited"
        summary = f"{comparable} 笔闭环均具备冻结信号与完整统一成交事实。"
    else:
        verdict = "mixed_evidence"
        summary = (
            f"{len(closed)} 笔闭环中，{audited} 笔成交事实完整、{partial} 笔部分完整、"
            f"{legacy} 笔为旧记录；只有 {comparable} 笔可用于当前执行合同的策略归因。"
        )
    return PaperExecutionEvidenceSummary(
        closed_trades=len(closed),
        audited_closed_trades=audited,
        partial_closed_trades=partial,
        legacy_closed_trades=legacy,
        comparable_closed_trades=comparable,
        audited_open_entries=audited_open,
        verdict=verdict,
        summary=summary,
    )


def _paper_execution_evidence_health(
    trades: list[PaperTradeRecord],
    source_context_by_trade: Mapping[str, PaperTradeSourceContext],
) -> dict[str, str]:
    summary = _paper_execution_evidence_summary(
        trades,
        source_context_by_trade=source_context_by_trade,
    )
    return {
        "paper_execution_evidence_verdict": summary.verdict,
        "paper_execution_evidence_closed": str(summary.closed_trades),
        "paper_execution_evidence_audited_closed": str(summary.audited_closed_trades),
        "paper_execution_evidence_partial_closed": str(summary.partial_closed_trades),
        "paper_execution_evidence_legacy_closed": str(summary.legacy_closed_trades),
        "paper_execution_evidence_comparable_closed": str(
            summary.comparable_closed_trades
        ),
        "paper_execution_evidence_audited_open": str(summary.audited_open_entries),
    }


def _paper_source_market_context(
    context: PaperTradeSourceContext | None,
) -> PaperMarketContext:
    if context is None or context.market_regime == "unknown":
        return PaperMarketContext(
            regime="unknown",
            title="信号时点市场环境未知",
            summary="该交易没有保存信号时点市场环境，不能用当前市场状态回填。",
        )
    return PaperMarketContext(
        regime=context.market_regime,
        title=f"信号时点市场环境：{context.market_regime}",
        summary=f"归因仅使用 {context.signal_date or context.created_at.date()} 保存的市场环境。",
    )


def _paper_context_label(dimension: str, key: str) -> str:
    if key == "unknown":
        return {
            "industry": "行业未知",
            "theme": "主题未知",
            "market_regime": "信号时点市场环境未知",
            "factor": "因子未知",
        }.get(dimension, "未知")
    if dimension == "factor":
        return _paper_signal_label(key)
    return key


def _paper_trade_diagnostics(
    items: list[PaperLedgerItem],
    *,
    source_context_by_trade: Mapping[str, PaperTradeSourceContext],
    trade_by_id: Mapping[str, PaperTradeRecord],
) -> list[PaperTradeDiagnostic]:
    diagnostics = []
    for item in items:
        if item.status == "replaced":
            continue
        context = source_context_by_trade.get(item.trade_id)
        source_card = context.card if context else {}
        signals = context.factor_ids if context is not None else []
        if not signals:
            signals = _paper_source_signals(source_card)
        source_market_context = _paper_source_market_context(context)
        cause, label, severity, evidence, action = _paper_trade_root_cause(
            item,
            signals=signals,
            market_context=source_market_context,
        )
        if cause == "waiting":
            continue
        trade = trade_by_id.get(item.trade_id)
        evidence_status = (
            _paper_execution_evidence_status(trade)
            if trade is not None
            else "legacy_unverified"
        )
        strategy_attribution_eligible = (
            evidence_status == "complete"
            and context is not None
            and context.source_status == "frozen"
        )
        evidence_note = _paper_execution_evidence_note(evidence_status)
        if (
            item.status in EXECUTED_CLOSED_STATUSES
            and not strategy_attribution_eligible
        ):
            cause = "legacy_execution_evidence"
            label = "旧成交证据不完整"
            severity = "warning"
            evidence = [
                evidence_note,
                "盈亏继续保留在保守风险统计，但不能代表当前统一执行规则",
            ]
            action = "不据此调整当前策略；等待冻结信号与完整成交事实的新闭环样本。"
        elif evidence_note not in evidence:
            evidence = [evidence_note, *evidence]
        diagnostics.append(
            PaperTradeDiagnostic(
                trade_id=item.trade_id,
                instrument_id=item.instrument_id,
                instrument_label=str(source_card.get("instrument_label") or item.instrument_id),
                strategy_id=item.strategy_id,
                status=item.status,
                return_pct=item.return_pct,
                root_cause=cause,
                root_cause_label=label,
                severity=severity,
                factor_signals=signals,
                source_industry=context.industry if context is not None else "unknown",
                source_themes=context.themes if context is not None else [],
                source_market_regime=source_market_context.regime,
                source_context_status=context.source_status if context is not None else "unknown",
                execution_evidence_status=evidence_status,
                execution_evidence_label=_paper_execution_evidence_label(evidence_status),
                strategy_attribution_eligible=strategy_attribution_eligible,
                evidence=evidence,
                action=action,
            )
        )
    severity_rank = {"critical": 0, "warning": 1, "watch": 2, "positive": 3}
    return sorted(
        diagnostics,
        key=lambda item: (
            severity_rank.get(item.severity, 9),
            item.return_pct if item.return_pct is not None else 0,
            item.trade_id,
        ),
    )[:20]


def _paper_trade_root_cause(
    item: PaperLedgerItem,
    *,
    signals: list[str],
    market_context: PaperMarketContext,
) -> tuple[str, str, str, list[str], str]:
    signal_labels = [_paper_signal_label(signal) for signal in signals]
    signal_evidence = [f"推荐时包含{label}" for label in signal_labels[:3]]
    if item.status == "invalidated":
        return (
            "data_quality",
            "数据口径异常",
            "critical",
            ["行情价格与推荐价格基准不连续"],
            "作废该样本，修复复权或代码映射后再验证。",
        )
    if item.status == "missed_entry":
        return (
            "entry_timing",
            "买点没有成交",
            "watch",
            ["价格未按触发与禁追规则形成可成交买点"],
            "保留选股样本，但重新校准触发价和等待期限。",
        )
    if item.status == "target_1_hit":
        return (
            "validated_success",
            "信号与执行有效",
            "positive",
            [f"按计划命中目标，收益 {item.return_pct:+.2f}%"]
            if item.return_pct is not None
            else ["按计划命中目标"],
            "保留该类信号权重，继续检查更大样本是否稳定。",
        )
    if item.status == "pending":
        return ("waiting", "等待买点", "watch", [], "继续等待触发或按禁追规则释放。")
    if item.status == "open" and (item.return_pct is None or item.return_pct > -2):
        return ("waiting", "等待结果", "watch", [], "继续按原计划跟踪。")

    stop_distance = None
    if item.entry_price and item.entry_price > 0 and item.risk_pct is not None:
        stop_distance = abs(item.risk_pct)
    if "overextended" in signals and "high_volatility" in signals:
        return (
            "risk_filter_failure",
            "过热且高波动",
            "critical" if item.status == "stopped" else "warning",
            signal_evidence + ["过热与高波动同时出现，止损容易被噪声触发"],
            "该组合信号进入门禁；等待回踩和波动收敛后再考虑。",
        )
    if "overextended" in signals:
        return (
            "chasing_entry",
            "入场位置偏高",
            "warning",
            signal_evidence + ["推荐时价格偏离趋势支撑"],
            "提高回踩确认要求，不在过热状态触发买入。",
        )
    if "high_volatility" in signals:
        return (
            "volatility_stop",
            "高波动触发止损",
            "warning",
            signal_evidence + ["近期波动较高，固定止损容易被日内噪声击穿"],
            "降低仓位，并使用波动率校准后的止损距离。",
        )
    if stop_distance is not None and stop_distance <= 2.5:
        return (
            "tight_stop",
            "止损距离过窄",
            "warning",
            [f"计划风险距离仅 {stop_distance:.2f}%"],
            "按 ATR/波动率重新设置止损，并同步降低仓位。",
        )
    if market_context.regime in {
        "market_drag",
        "risk_off",
        "risk-off",
        "bear",
        "bearish",
        "weak",
    }:
        return (
            "market_regime",
            "信号时点市场环境拖累",
            "watch",
            [market_context.summary],
            "弱市降低总仓位，只保留相对强度最高的机会。",
        )
    if item.status == "time_exit":
        return (
            "weak_followthrough",
            "信号缺少跟随",
            "warning" if (item.return_pct or 0) < 0 else "watch",
            ["持有窗口内没有命中目标或形成持续趋势"] + signal_evidence,
            "降低无跟随信号权重，并缩短失效确认周期。",
        )
    return (
        "selection_quality",
        "选股信号失效",
        "critical" if item.status == "stopped" else "warning",
        ["触发后走势未按推荐方向延续"] + signal_evidence,
        "保留为有效失败样本；达到固定检查点后再决定是否调整策略或因子权重。",
    )


def _paper_source_signals(card: Mapping[str, object]) -> list[str]:
    signals: list[str] = []
    raw_flags = card.get("factor_flags")
    if isinstance(raw_flags, list):
        signals.extend(str(value) for value in raw_flags if value)
    raw_exposures = card.get("factor_exposures")
    if isinstance(raw_exposures, list):
        for exposure in raw_exposures:
            if not isinstance(exposure, Mapping):
                continue
            factor_id = exposure.get("factor_id")
            score = _float_mapping_value(exposure, "score")
            if factor_id and score is not None and (score >= 0.75 or score <= 0.25):
                signals.append(str(factor_id))
    return sorted(set(signals))


def _paper_signal_label(signal: str) -> str:
    return {
        "high_volatility": "高波动",
        "overextended": "短线过热",
        "insufficient_history": "历史不足",
        "low_liquidity": "流动性偏弱",
        "momentum": "动量",
        "trend_quality": "趋势质量",
        "liquidity": "流动性",
        "low_risk": "低波动",
        "reversal": "反转/回踩",
        "valuation": "估值",
        "quality": "质量",
        "size": "市值",
    }.get(signal, signal)


def _paper_failure_group(
    *,
    dimension: str,
    key: str,
    label: str,
    items: list[PaperLedgerItem],
    allocation_per_trade: Decimal,
) -> PaperFailureAttributionItem:
    evaluated = [
        item for item in items if item.entry_date is not None and item.return_pct is not None
    ]
    closed = [item for item in items if _is_executed_closed_item(item)]
    returns = [item.return_pct for item in evaluated if item.return_pct is not None]
    total_pnl = _money(sum((item.total_pnl for item in items), Decimal("0")))
    capital_base = sum((item.capital_allocated for item in evaluated), Decimal("0"))
    total_return_pct = _pct(total_pnl, capital_base) if capital_base > 0 else None
    win_rate = (
        round(sum(1 for value in returns if value > 0) / len(returns), 4) if returns else None
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
        "replaced": "候补换出",
        "invalidated": "数据作废",
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
        if trade.exit_date is not None and (
            trade.exit_price is not None or trade.status in {"replaced", "invalidated"}
        ):
            events.append(
                PaperEventTimelineItem(
                    event_id=f"{trade.trade_id}:exit",
                    trade_id=trade.trade_id,
                    instrument_id=trade.instrument_id,
                    strategy_id=trade.strategy_id,
                    event_date=trade.exit_date,
                    event_type="exit",
                    title=_paper_exit_title(trade.status),
                    description=validation_item.next_action
                    if validation_item is not None
                    else _paper_next_action(trade),
                    status=trade.status,
                    price=trade.exit_price or trade.latest_price,
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
                    description=validation_item.next_action
                    if validation_item is not None
                    else _paper_next_action(trade),
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
        "replaced": "候补换出",
        "invalidated": "数据作废",
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
        next_action=validation_item.next_action
        if validation_item is not None
        else _paper_next_action(trade),
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
                benchmark_id=str(raw.get("benchmark_id"))
                if raw.get("benchmark_id") is not None
                else None,
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
    if trade.status == "replaced":
        return "已由更高优先级候选替换，不计入成交胜率或错过率。"
    if trade.status == "invalidated":
        return "价格口径不一致，记录已作废并排除出绩效统计。"
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
    dates = {trade.signal_date for trade in trades if trade.signal_date is not None}
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
            if len(active_lots) >= max_positions and trade.execution_facts is None:
                continue
            buy = _buy_lot(
                trade=trade,
                cash=cash,
                allocation_per_trade=_trade_allocation(trade, allocation_per_trade),
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
            if (
                "execution_facts" in lot
                and lot["exit_date"] == current_date
                and lot["status"] in CLOSED_STATUSES
            ):
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
        "allocated_capital": _money(
            sum((position.cost_basis for position in positions), Decimal("0"))
        ),
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
    capital_allocated = (
        _trade_allocation(trade, allocation_per_trade)
        if trade.entry_date is not None
        else Decimal("0")
    )
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
        days_since_signal=max(trading_sessions_elapsed(trade.signal_date, as_of), 0),
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
        if item.entry_date is not None
        and item.days_since_signal >= window_days
        and item.return_pct is not None
    ]
    returns = [item.return_pct for item in evaluated if item.return_pct is not None]
    total_pnl = sum((item.pnl for item in evaluated), Decimal("0"))
    denominator = sum((item.capital_allocated for item in evaluated), Decimal("0"))
    return PaperValidationWindow(
        window_days=window_days,
        eligible_trades=len(items),
        evaluated_trades=len(evaluated),
        missed_entry_count=sum(1 for item in items if item.status == "missed_entry"),
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
        executed_items = [item for item in batch_items if item.entry_date is not None]
        returns = [
            item.return_pct
            for item in executed_items
            if item.return_pct is not None and item.days_since_signal >= windows[-1]
        ]
        total_pnl = sum((item.pnl for item in executed_items), Decimal("0"))
        denominator = sum((item.capital_allocated for item in executed_items), Decimal("0"))
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
                closed_trades=sum(1 for item in batch_items if _is_executed_closed_item(item)),
                missed_entry_count=sum(1 for item in batch_items if item.status == "missed_entry"),
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

    executed_count = sum(1 for item in items if item.entry_date is not None)
    missed_count = sum(1 for item in items if item.status == "missed_entry")
    closed_count = sum(1 for item in items if _is_executed_closed_item(item))
    sample_score = min(executed_count / 20, 1) * 0.25
    closed_score = min(closed_count / 10, 1) * 0.25
    maturity_score = min(sample_age.mature_10d / max(len(items), 1), 1) * 0.2
    drawdown_score = max(0.0, min(1.0, (12 + max_drawdown_pct) / 12)) * 0.15
    concentration_pct = _pnl_concentration(items)
    concentration_score = (1 - min((concentration_pct or 0) / 100, 1)) * 0.15
    score = round(
        sample_score + closed_score + maturity_score + drawdown_score + concentration_score, 4
    )
    warnings: list[str] = []
    if executed_count < 20:
        warnings.append("已成交样本少于 20 笔，先看方向，不宜过度相信胜率。")
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
            f"已成交 {executed_count} 笔",
            f"成交后闭环 {closed_count} 笔",
            f"错过买点 {missed_count} 笔",
            f"10日成熟样本 {sample_age.mature_10d} 笔",
            f"20日窗口可评价 {primary_window.evaluated_trades} 笔",
        ],
        concentration_pct=concentration_pct,
    )


def _mature_count(items: list[PaperValidationItem], window_days: int) -> int:
    return sum(
        1
        for item in items
        if item.entry_date is not None
        and item.return_pct is not None
        and item.days_since_signal >= window_days
    )


def _days_to_next_mature(
    items: list[PaperValidationItem],
    window_days: int,
) -> int | None:
    pending = [
        max(window_days - item.days_since_signal, 0)
        for item in items
        if item.entry_date is not None
        and item.return_pct is not None
        and item.days_since_signal < window_days
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
    if trade.status == "missed_entry":
        return "missed_entry"
    if trade.status == "replaced":
        return "replaced"
    if trade.status == "invalidated":
        return "invalidated"
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
    if state == "missed_entry":
        return "错过买点，仅计入触发率，不计入交易胜率。"
    if state == "replaced":
        return "候补轮换退出，保留审计记录，不计入成交胜率或错过率。"
    if state == "invalidated":
        return "价格口径不一致，样本作废，不计入交易或推荐绩效。"
    if state == "closed":
        return "已闭环，纳入胜率、收益和回撤统计。"
    return "继续观察。"


def _is_executed_closed_trade(trade: PaperTradeRecord) -> bool:
    return trade.entry_date is not None and trade.status in EXECUTED_CLOSED_STATUSES


def _is_executed_closed_item(item: PaperLedgerItem | PaperValidationItem) -> bool:
    return item.entry_date is not None and item.status in EXECUTED_CLOSED_STATUSES


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
    if trade.execution_facts is not None:
        entry = trade.execution_facts.entry
        shares = Decimal(entry.quantity)
        fee = entry.total_fees
        slippage = entry.slippage
        gross = entry.gross_amount
        cash_balance = _money(cash + entry.cash_flow)
        cost_basis = -entry.cash_flow
        lot = {
            "trade_id": trade.trade_id,
            "instrument_id": trade.instrument_id,
            "strategy_id": trade.strategy_id,
            "status": trade.status,
            "entry_date": entry.trade_date,
            "entry_price": entry.price,
            "exit_date": trade.exit_date,
            "exit_price": trade.exit_price,
            "latest_date": trade.latest_date,
            "latest_price": trade.latest_price,
            "shares": shares,
            "cost_basis": cost_basis,
            "execution_facts": trade.execution_facts,
        }
        transaction = PaperLedgerTransaction(
            transaction_id=f"{trade.trade_id}-buy",
            trade_id=trade.trade_id,
            instrument_id=trade.instrument_id,
            action="entry_buy",
            side="buy",
            trade_date=entry.trade_date,
            price=entry.price,
            shares=shares,
            gross_amount=gross,
            fee=fee,
            slippage=slippage,
            cash_flow=entry.cash_flow,
            cash_balance=cash_balance,
            notes="按不可变成交事实记入买入流水。",
        )
        return lot, transaction, fee, slippage, gross
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

    facts = lot.get("execution_facts")
    if isinstance(facts, PaperExecutionFacts) and facts.exit is not None:
        exit_fact = facts.exit
        shares = Decimal(exit_fact.quantity)
        fee = exit_fact.total_fees
        slippage = exit_fact.slippage
        cash_balance = _money(cash + exit_fact.cash_flow)
        pnl = exit_fact.cash_flow - cost_per_share * shares
        action = (
            "take_profit_exit"
            if status == "target_1_hit"
            else "stop_loss_exit"
            if status == "stopped"
            else "time_exit"
        )
        transaction = PaperLedgerTransaction(
            transaction_id=f"{lot['trade_id']}-{action}",
            trade_id=str(lot["trade_id"]),
            instrument_id=str(lot["instrument_id"]),
            action=action,
            side="sell",
            trade_date=exit_fact.trade_date,
            price=exit_fact.price,
            shares=shares,
            gross_amount=exit_fact.gross_amount,
            fee=fee,
            slippage=slippage,
            cash_flow=exit_fact.cash_flow,
            cash_balance=cash_balance,
            notes="按不可变成交事实记入卖出流水。",
        )
        return [(transaction, pnl, fee, slippage, exit_fact.gross_amount)]

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
    if (
        isinstance(latest_date, date)
        and latest_date <= current_date
        and isinstance(latest_price, Decimal)
    ):
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

    if trade.execution_facts is not None:
        entry = trade.execution_facts.entry
        shares = Decimal(entry.quantity)
        capital_allocated = -entry.cash_flow
        if trade.status in CLOSED_STATUSES and trade.execution_facts.exit is not None:
            exit_fact = trade.execution_facts.exit
            realized_pnl = exit_fact.cash_flow - capital_allocated
            return_pct = _pct(realized_pnl, capital_allocated)
        elif trade.status == "open":
            latest_price = trade.latest_price or entry.price
            market_value = shares * latest_price
            unrealized_pnl = market_value - capital_allocated
            return_pct = _pct(unrealized_pnl, capital_allocated)
    elif trade.entry_price and trade.entry_price > 0:
        shares = (allocation_per_trade / trade.entry_price).quantize(Decimal("0.0001"))
        capital_allocated = allocation_per_trade
        if trade.status in CLOSED_STATUSES and trade.exit_price is not None:
            exit_value = shares * trade.exit_price
            realized_pnl = exit_value - allocation_per_trade
            return_pct = _return_pct(trade.entry_price, trade.exit_price)
        elif trade.status == "open":
            latest_price = trade.latest_price or trade.entry_price
            market_value = shares * latest_price
            unrealized_pnl = market_value - allocation_per_trade
            return_pct = _return_pct(trade.entry_price, latest_price)

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


def _trade_allocation(
    trade: PaperTradeRecord,
    allocation_per_trade: Decimal,
) -> Decimal:
    if trade.execution_facts is not None:
        return trade.execution_facts.allocation
    multiplier = trade.allocation_multiplier or Decimal("1.0")
    return _money(allocation_per_trade * multiplier)


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
        drawdown_pct = _pct(equity - high_watermark, high_watermark) if high_watermark > 0 else 0.0
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


def _paper_execution_context(
    trade: PaperTradeRecord,
    account: PaperAccountSettings,
    source_context: PaperTradeSourceContext | None,
) -> _PaperExecutionContext | None:
    if not _is_a_share_trade(trade):
        return None
    if trade.execution_facts is not None:
        return _PaperExecutionContext(
            rules=trade.execution_facts.rules,
            allocation=trade.execution_facts.allocation,
            source_context=source_context,
        )

    card = source_context.card if source_context is not None else {}
    constraints = _mapping(card.get("trading_constraints"))
    overrides = _mapping(card.get("execution_rules") or card.get("execution"))
    code = trade.instrument_id.split(":", 1)[-1].split(".", 1)[0]
    default_tick = Decimal("0.001") if code.startswith(("1", "5")) else Decimal("0.01")
    tick_size = (
        _positive_decimal_or_none(overrides.get("tick_size") or constraints.get("tick_size"))
        or default_tick
    )
    lot_size = _positive_int(overrides.get("lot_size") or constraints.get("min_lot")) or 100

    settlement_value = overrides.get("settlement_days")
    if settlement_value in {0, 1, "0", "1"}:
        settlement_days = int(settlement_value)
    else:
        t_plus_one = constraints.get("t_plus_one", True)
        settlement_days = 1 if _truthy(t_plus_one) else 0

    price_limit_rate = _positive_decimal_or_none(overrides.get("price_limit_rate"))
    if price_limit_rate is None:
        price_limit_pct = _positive_decimal_or_none(constraints.get("price_limit_pct"))
        if price_limit_pct is not None:
            price_limit_rate = price_limit_pct / Decimal("100")
    if price_limit_rate is None:
        if code.startswith(("4", "8", "92")):
            price_limit_rate = Decimal("0.30")
        elif code.startswith(("300", "301", "688", "689")):
            price_limit_rate = Decimal("0.20")
        else:
            price_limit_rate = Decimal("0.10")

    rules = AShareExecutionRules(
        rules_version="paper-a-share-execution-v1",
        fee_schedule_version="paper-account-cost-v1",
        tick_size=tick_size,
        lot_size=lot_size,
        settlement_days=settlement_days,
        price_limit_rate=price_limit_rate,
        volume_participation_rate=Decimal("1"),
        commission_bps=account.transaction_cost_bps,
        minimum_commission=Decimal("0"),
        stamp_duty_bps=Decimal("0"),
        transfer_fee_bps=Decimal("0"),
        slippage_bps=account.slippage_bps,
    )
    allocation = execution_money(
        account.initial_capital
        * account.allocation_per_trade_pct
        / Decimal("100")
        * (trade.allocation_multiplier or Decimal("1"))
    )
    return _PaperExecutionContext(
        rules=rules,
        allocation=allocation,
        source_context=source_context,
    )


def _paper_entry_quantity(
    trade: PaperTradeRecord,
    context: _PaperExecutionContext,
) -> int:
    if trade.trigger_price <= 0:
        return 0
    return round_lot(
        int(context.allocation / trade.trigger_price),
        context.rules.lot_size,
    )


def paper_snapshot_round_lot_is_affordable(
    snapshot: OpportunitySnapshotRecord,
    account: PaperAccountSettings,
    allocation_multiplier: Decimal,
) -> bool:
    if not snapshot.instrument_id.upper().startswith("CN:"):
        return True
    if snapshot.trigger_price is None or snapshot.trigger_price <= 0:
        return False
    card = snapshot.card if isinstance(snapshot.card, dict) else {}
    constraints = _mapping(card.get("trading_constraints"))
    overrides = _mapping(card.get("execution_rules") or card.get("execution"))
    lot_size = _positive_int(overrides.get("lot_size") or constraints.get("min_lot")) or 100
    allocation = execution_money(
        account.initial_capital
        * account.allocation_per_trade_pct
        / Decimal("100")
        * allocation_multiplier
    )
    return round_lot(int(allocation / snapshot.trigger_price), lot_size) > 0


def _unaffordable_round_lot_update(
    trade: PaperTradeRecord,
    context: _PaperExecutionContext,
    *,
    invalidated_on: date,
) -> dict[str, object]:
    minimum_notional = execution_money(trade.trigger_price * context.rules.lot_size)
    return {
        "status": "missed_entry",
        "latest_date": trade.latest_date or invalidated_on,
        "latest_price": trade.latest_price or trade.trigger_price,
        "exit_date": invalidated_on,
        "exit_price": None,
        "realized_return_pct": None,
        "holding_days": 0,
        "notes": _append_note(
            trade.notes,
            (
                f"模拟盘分配资金 {context.allocation:.2f} 元不足以按触发价买入"
                f"最小一手（需 {minimum_notional:.2f} 元），记为未成交并释放名额。"
            ),
        ),
        _TERMINAL_REASON_UPDATE_KEY: "paper_trade.entry_allocation_below_round_lot",
    }


def _paper_position_quantity(
    trade: PaperTradeRecord,
    context: _PaperExecutionContext,
    entry_fill: _PaperMatchedFill | None,
    entry_price: Decimal | None,
) -> int:
    if entry_fill is not None:
        return entry_fill.quantity
    if trade.execution_facts is not None:
        return trade.execution_facts.entry.quantity
    if entry_price is None or entry_price <= 0:
        return 0
    return round_lot(
        int(context.allocation / entry_price),
        context.rules.lot_size,
    )


def _paper_match_row(
    *,
    trade: PaperTradeRecord,
    row: pd.Series,
    trade_date: date,
    context: _PaperExecutionContext,
    side: OrderSide,
    order_type: OrderType,
    quantity: int,
    event_suffix: str,
    limit_price: Decimal | None = None,
    stop_price: Decimal | None = None,
    previous_close: Decimal | None = None,
    max_notional: Decimal | None = None,
    allow_partial: bool = True,
) -> _PaperMatchResult:
    price_to_validate = (
        limit_price if order_type in {OrderType.LIMIT, OrderType.STOP_LIMIT} else stop_price
    )
    if price_to_validate is not None and not is_tick_aligned(
        price_to_validate,
        context.rules.tick_size,
    ):
        return _PaperMatchResult(triggered=True, reason="price_not_on_tick")

    occurred_at = _paper_row_datetime(row, trade_date)
    try:
        market = _paper_market_event(
            trade=trade,
            row=row,
            occurred_at=occurred_at,
            event_suffix=event_suffix,
            context=context,
            previous_close=previous_close,
            default_volume=max(quantity, context.rules.lot_size),
        )
    except ValueError:
        return _PaperMatchResult(triggered=False, reason="invalid_market_bar")
    if market is None:
        return _PaperMatchResult(triggered=False, reason="invalid_market_bar")

    order_quantity = max(quantity, context.rules.lot_size)
    order = Order(
        order_id=f"paper-order:{trade.trade_id}:{event_suffix}",
        intent_id=f"paper-intent:{trade.trade_id}:{event_suffix}",
        account_id="paper",
        instrument_id=trade.instrument_id,
        side=side,
        quantity=order_quantity,
        submitted_at=occurred_at,
        order_type=order_type,
        limit_price=limit_price,
        stop_price=stop_price,
        estimated_price=limit_price or stop_price or market.open,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.ACTIVE,
        updated_at=occurred_at,
        rules=context.rules,
    )
    base_price = match_base_price(order, market)
    if base_price is None:
        return _PaperMatchResult(triggered=False)
    if market.suspended:
        return _PaperMatchResult(triggered=True, reason="suspended")
    if market.volume == 0:
        return _PaperMatchResult(triggered=True, reason="zero_volume")
    if is_one_price_limit_blocked(side, market, context.rules):
        return _PaperMatchResult(triggered=True, reason="one_price_limit")
    if quantity <= 0:
        return _PaperMatchResult(triggered=True, reason="quantity_below_round_lot")

    capacity = participation_capacity(market.volume, context.rules)
    if not allow_partial and capacity < quantity:
        return _PaperMatchResult(triggered=True, reason="insufficient_round_lot_volume")
    fill_quantity = round_lot(min(quantity, capacity), context.rules.lot_size)
    if fill_quantity <= 0:
        return _PaperMatchResult(triggered=True, reason="insufficient_round_lot_volume")
    price = execution_apply_slippage(order, market, base_price)
    if side == OrderSide.BUY and max_notional is not None:
        affordable = round_lot(
            int(max_notional / price),
            context.rules.lot_size,
        )
        fill_quantity = min(fill_quantity, affordable)
        if fill_quantity <= 0:
            return _PaperMatchResult(triggered=True, reason="quantity_below_round_lot")
    return _PaperMatchResult(
        triggered=True,
        fill=_PaperMatchedFill(
            market_event_id=market.event_id,
            side=side,
            trade_date=trade_date,
            occurred_at=occurred_at,
            base_price=base_price,
            price=price,
            quantity=fill_quantity,
            rules=context.rules,
        ),
    )


def _paper_market_event(
    *,
    trade: PaperTradeRecord,
    row: pd.Series,
    occurred_at: datetime,
    event_suffix: str,
    context: _PaperExecutionContext,
    previous_close: Decimal | None,
    default_volume: int,
) -> MarketEvent | None:
    prices = {field: _row_decimal(row, field) for field in ("open", "high", "low", "close")}
    if any(value is None or value <= 0 for value in prices.values()):
        return None
    volume = _row_non_negative_int(row, "volume")
    if volume is None:
        volume = default_volume
    trading_status = _source_trading_status(context.source_context)
    resolved_previous_close = (
        _row_decimal(row, "previous_close")
        or _row_decimal(row, "pre_close")
        or previous_close
        or _positive_decimal_or_none(trading_status.get("previous_close"))
    )
    return MarketEvent(
        event_id=f"paper-market:{trade.trade_id}:{event_suffix}",
        instrument_id=trade.instrument_id,
        occurred_at=occurred_at,
        trading_date=occurred_at.date(),
        open=prices["open"],
        high=prices["high"],
        low=prices["low"],
        close=prices["close"],
        volume=volume,
        previous_close=resolved_previous_close,
        suspended=_paper_row_suspended(row),
        limit_up_price=_row_decimal(row, "limit_up_price"),
        limit_down_price=_row_decimal(row, "limit_down_price"),
    )


def _paper_previous_close(
    ordered: pd.DataFrame,
    row_index: int,
    trade_date: date,
    source_latest_close: Decimal | None,
) -> Decimal | None:
    row = ordered.iloc[row_index]
    explicit = _row_decimal(row, "previous_close") or _row_decimal(row, "pre_close")
    if explicit is not None:
        return explicit
    if row_index > 0:
        previous = ordered.iloc[row_index - 1]
        previous_date = previous.get("trade_date")
        if isinstance(previous_date, (datetime, pd.Timestamp)):
            previous_date = previous_date.date()
        if previous_date != trade_date:
            return _row_decimal(previous, "close") or source_latest_close
    return source_latest_close


def _paper_minute_previous_close(
    ordered: pd.DataFrame,
    row_index: int,
    trade_date: date,
    source_latest_close: Decimal | None,
) -> Decimal | None:
    row = ordered.iloc[row_index]
    explicit = _row_decimal(row, "previous_close") or _row_decimal(row, "pre_close")
    if explicit is not None:
        return explicit
    if row_index > 0:
        previous = ordered.iloc[row_index - 1]
        previous_timestamp = previous.get("timestamp")
        if previous_timestamp is not None and not pd.isna(previous_timestamp):
            if pd.Timestamp(previous_timestamp).date() != trade_date:
                return _row_decimal(previous, "close") or source_latest_close
    return source_latest_close


def _paper_row_datetime(row: pd.Series, trade_date: date) -> datetime:
    raw = row.get("timestamp")
    if raw is not None and not pd.isna(raw):
        value = pd.Timestamp(raw).to_pydatetime()
        return value if value.tzinfo is not None else value.replace(tzinfo=A_SHARE_TZ)
    return datetime.combine(trade_date, A_SHARE_AFTERNOON_END, tzinfo=A_SHARE_TZ)


def _paper_row_suspended(row: pd.Series) -> bool:
    for key in ("suspended", "is_suspended"):
        raw = row.get(key)
        if raw is not None and not pd.isna(raw):
            return _truthy(raw)
    status = str(row.get("trading_status") or row.get("status") or "").lower()
    return status in {"suspended", "halted", "停牌"}


def _source_trading_status(
    source_context: PaperTradeSourceContext | None,
) -> Mapping[str, object]:
    if source_context is None:
        return {}
    return _mapping(source_context.card.get("trading_status"))


def _row_decimal(row: pd.Series, key: str) -> Decimal | None:
    raw = row.get(key)
    if raw is None or pd.isna(raw):
        return None
    return _positive_decimal_or_none(raw)


def _row_non_negative_int(row: pd.Series, key: str) -> int | None:
    raw = row.get(key)
    if raw is None or pd.isna(raw):
        return None
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return None


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _paper_execution_deferred_note(reason: str | None) -> str:
    return {
        "suspended": "统一成交规则：停牌期间不成交，继续等待。",
        "zero_volume": "统一成交规则：零成交量行情不成交，继续等待。",
        "one_price_limit": "统一成交规则：一字涨跌停方向受限，继续等待。",
        "price_not_on_tick": "统一成交规则：委托价格不符合最小报价单位，继续等待。",
        "quantity_below_round_lot": "统一成交规则：可买数量不足一手，继续等待。",
        "insufficient_round_lot_volume": "统一成交规则：可成交量不足一手，继续等待。",
    }.get(reason, "统一成交规则：当前行情不可成交，继续等待。")


def _a_share_can_fill_bar(
    trade: PaperTradeRecord,
    trade_date: date,
    as_of: datetime | None,
    *,
    status: str,
    entry_date: date | None,
    settlement_days: int | None = None,
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
            return entry_date is not None and (
                entry_date < trade_date or (settlement_days == 0 and entry_date == trade_date)
            )
    return False


def _append_note(existing: str, note: str) -> str:
    if not existing:
        return note
    if note in existing:
        return existing
    return f"{existing} {note}"


def _persist_paper_trade_update(
    repo: PaperTradingRepository,
    trade: PaperTradeRecord,
    update: dict[str, object],
    context: _PaperExecutionContext | None,
) -> None:
    changes = dict(update)
    entry_fill = changes.pop(_ENTRY_FILL_UPDATE_KEY, None)
    exit_fill = changes.pop(_EXIT_FILL_UPDATE_KEY, None)
    changes.pop(_DEFERRED_FILL_UPDATE_KEY, None)
    terminal_reason = changes.pop(_TERMINAL_REASON_UPDATE_KEY, None)
    if entry_fill is not None and not isinstance(entry_fill, _PaperMatchedFill):
        raise TypeError("paper entry fill evidence has an invalid type")
    if exit_fill is not None and not isinstance(exit_fill, _PaperMatchedFill):
        raise TypeError("paper exit fill evidence has an invalid type")

    metadata = None
    if terminal_reason is not None:
        metadata = PaperTradeEventMetadata(
            idempotency_key=f"paper-terminal:{trade.trade_id}:{terminal_reason}",
            reason_code=str(terminal_reason),
            note=str(changes.get("notes", trade.notes)),
            source="unified_execution",
        )
    if context is not None and (entry_fill is not None or exit_fill is not None):
        facts = _paper_execution_facts(
            trade,
            context,
            entry_fill=entry_fill,
            exit_fill=exit_fill,
        )
        latest_fill = exit_fill or entry_fill
        assert latest_fill is not None
        phase = "exit" if exit_fill is not None else "entry"
        metadata = PaperTradeEventMetadata(
            idempotency_key=(
                f"paper-execution:{trade.trade_id}:{phase}:{latest_fill.market_event_id}"
            ),
            occurred_at=latest_fill.occurred_at,
            trade_date=latest_fill.trade_date,
            price=latest_fill.price,
            reason_code=f"paper_trade.unified_execution.{phase}",
            note=str(changes.get("notes", trade.notes)),
            source="unified_execution",
            execution_facts=facts,
        )
    repo.update_trade(trade.trade_id, event_metadata=metadata, **changes)


def _paper_execution_facts(
    trade: PaperTradeRecord,
    context: _PaperExecutionContext,
    *,
    entry_fill: _PaperMatchedFill | None,
    exit_fill: _PaperMatchedFill | None,
) -> PaperExecutionFacts:
    existing = trade.execution_facts
    if existing is not None:
        entry = existing.entry
    else:
        if entry_fill is None:
            entry_fill = _inferred_entry_fill(trade, context)
        entry = _paper_execution_leg(entry_fill)
    exit_leg = existing.exit if existing is not None else None
    if exit_fill is not None:
        exit_leg = _paper_execution_leg(exit_fill)
    return PaperExecutionFacts(
        allocation=existing.allocation if existing is not None else context.allocation,
        rules=existing.rules if existing is not None else context.rules,
        entry=entry,
        exit=exit_leg,
    )


def _inferred_entry_fill(
    trade: PaperTradeRecord,
    context: _PaperExecutionContext,
) -> _PaperMatchedFill:
    if trade.entry_date is None or trade.entry_price is None:
        raise ValueError("cannot infer execution facts without an entry")
    quantity = _paper_position_quantity(
        trade,
        context,
        entry_fill=None,
        entry_price=trade.entry_price,
    )
    if quantity <= 0:
        raise ValueError("cannot infer execution facts below one round lot")
    occurred_at = datetime.combine(
        trade.entry_date,
        A_SHARE_AFTERNOON_END,
        tzinfo=A_SHARE_TZ,
    )
    return _PaperMatchedFill(
        market_event_id=f"paper-market:{trade.trade_id}:legacy-entry",
        side=OrderSide.BUY,
        trade_date=trade.entry_date,
        occurred_at=occurred_at,
        base_price=trade.entry_price,
        price=trade.entry_price,
        quantity=quantity,
        rules=context.rules,
        source="legacy_inferred",
    )


def _paper_execution_leg(fill: _PaperMatchedFill) -> PaperExecutionLegFacts:
    gross = fill.price * fill.quantity
    fees = execution_fee_breakdown(fill.side, gross, fill.rules)
    slippage = execution_money(abs(fill.price - fill.base_price) * fill.quantity)
    cash_flow = -(gross + fees.total) if fill.side == OrderSide.BUY else gross - fees.total
    return PaperExecutionLegFacts(
        market_event_id=fill.market_event_id,
        side=fill.side,
        trade_date=fill.trade_date,
        base_price=fill.base_price,
        price=fill.price,
        quantity=fill.quantity,
        gross_amount=gross,
        commission=fees.commission,
        stamp_duty=fees.stamp_duty,
        transfer_fee=fees.transfer_fee,
        slippage=slippage,
        cash_flow=execution_money(cash_flow),
        source=fill.source,
    )


def _attach_execution_fills(
    update: dict[str, object],
    *,
    entry_fill: _PaperMatchedFill | None = None,
    exit_fill: _PaperMatchedFill | None = None,
    deferred_fills: int | None = None,
) -> dict[str, object]:
    if entry_fill is not None:
        update[_ENTRY_FILL_UPDATE_KEY] = entry_fill
    if exit_fill is not None:
        update[_EXIT_FILL_UPDATE_KEY] = exit_fill
    if deferred_fills is not None:
        update[_DEFERRED_FILL_UPDATE_KEY] = deferred_fills
    return update


def _evaluate_trade(
    trade: PaperTradeRecord,
    bars: pd.DataFrame,
    max_holding_days: int,
    max_entry_wait_days: int,
    as_of: datetime | None = None,
    source_latest_close: Decimal | None = None,
    execution_context: _PaperExecutionContext | None = None,
) -> tuple[dict[str, object], int]:
    ordered = bars.sort_values("trade_date").reset_index(drop=True)
    if pd.api.types.is_datetime64_any_dtype(ordered["trade_date"]):
        ordered["trade_date"] = ordered["trade_date"].dt.date
    entry_date = trade.entry_date
    entry_price = trade.entry_price
    status = trade.status
    notes = trade.notes
    deferred_fills = 0
    entry_fill: _PaperMatchedFill | None = None

    if status == "pending":
        post_signal = ordered[ordered["trade_date"] > trade.signal_date]
        if not post_signal.empty:
            first = post_signal.iloc[0]
            observed = Decimal(str(first["open"]))
            if _paper_price_basis_discontinuous(
                trade.instrument_id,
                source_latest_close or trade.trigger_price,
                observed,
            ):
                latest = ordered.iloc[-1]
                latest_date = latest["trade_date"]
                latest_price = Decimal(str(latest["close"]))
                return (
                    _invalidated_price_basis_update(
                        trade,
                        latest_date=latest_date,
                        latest_price=latest_price,
                    ),
                    deferred_fills,
                )

    for row_index, row in ordered.iterrows():
        trade_date = row["trade_date"]
        if isinstance(trade_date, pd.Timestamp):
            trade_date = trade_date.date()
        elif isinstance(trade_date, datetime):
            trade_date = trade_date.date()
        high = Decimal(str(row["high"]))
        low = Decimal(str(row["low"]))
        close = Decimal(str(row["close"]))
        previous_close = _paper_previous_close(
            ordered,
            row_index,
            trade_date,
            source_latest_close,
        )
        if status == "pending":
            wait_days = max((trade_date - trade.signal_date).days, 0)
            match = None
            if execution_context is not None:
                match = _paper_match_row(
                    trade=trade,
                    row=row,
                    trade_date=trade_date,
                    context=execution_context,
                    side=OrderSide.BUY,
                    order_type=OrderType.STOP,
                    stop_price=trade.trigger_price,
                    quantity=_paper_entry_quantity(trade, execution_context),
                    event_suffix=f"daily:{trade_date.isoformat()}:entry",
                    previous_close=previous_close,
                    max_notional=execution_context.allocation,
                )
            trigger_reached = match.triggered if match is not None else high >= trade.trigger_price
            if trigger_reached:
                if match is not None and match.fill is None:
                    deferred_fills += 1
                    notes = _append_note(notes, _paper_execution_deferred_note(match.reason))
                    continue
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
                if match is not None:
                    assert match.fill is not None
                    entry_fill = match.fill
                    entry_price = entry_fill.price
                else:
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
                settlement_days=(
                    execution_context.rules.settlement_days
                    if execution_context is not None
                    else 1
                    if _is_a_share_trade(trade)
                    else 0
                ),
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
            settlement_days = (
                execution_context.rules.settlement_days
                if execution_context is not None
                else 1
                if _is_a_share_trade(trade)
                else 0
            )
            if settlement_days > 0 and trade_date == entry_date:
                notes = _append_note(notes, "A股 T+1：买入当日不模拟卖出。")
                continue
            if execution_context is not None:
                quantity = _paper_position_quantity(
                    trade,
                    execution_context,
                    entry_fill,
                    entry_price,
                )
                if trade.initial_stop is not None:
                    stop_match = _paper_match_row(
                        trade=trade,
                        row=row,
                        trade_date=trade_date,
                        context=execution_context,
                        side=OrderSide.SELL,
                        order_type=OrderType.STOP,
                        stop_price=trade.initial_stop,
                        quantity=quantity,
                        event_suffix=f"daily:{trade_date.isoformat()}:stop",
                        previous_close=previous_close,
                        allow_partial=False,
                    )
                    if stop_match.triggered:
                        if stop_match.fill is None:
                            deferred_fills += 1
                            notes = _append_note(
                                notes,
                                _paper_execution_deferred_note(stop_match.reason),
                            )
                            continue
                        closed = _closed_update(
                            status="stopped",
                            entry_date=entry_date,
                            entry_price=entry_price,
                            exit_date=trade_date,
                            exit_price=stop_match.fill.price,
                            latest_price=close,
                            holding_days=holding_days,
                            notes=_append_note(notes, "触及初始止损，模拟离场。"),
                        )
                        return (
                            _attach_execution_fills(
                                closed,
                                entry_fill=entry_fill,
                                exit_fill=stop_match.fill,
                            ),
                            deferred_fills,
                        )
                if trade.target_1 is not None:
                    target_match = _paper_match_row(
                        trade=trade,
                        row=row,
                        trade_date=trade_date,
                        context=execution_context,
                        side=OrderSide.SELL,
                        order_type=OrderType.LIMIT,
                        limit_price=trade.target_1,
                        quantity=quantity,
                        event_suffix=f"daily:{trade_date.isoformat()}:target",
                        previous_close=previous_close,
                        allow_partial=False,
                    )
                    if target_match.triggered:
                        if target_match.fill is None:
                            deferred_fills += 1
                            notes = _append_note(
                                notes,
                                _paper_execution_deferred_note(target_match.reason),
                            )
                            continue
                        closed = _closed_update(
                            status="target_1_hit",
                            entry_date=entry_date,
                            entry_price=entry_price,
                            exit_date=trade_date,
                            exit_price=target_match.fill.price,
                            latest_price=close,
                            holding_days=holding_days,
                            notes=_append_note(notes, "触及第一目标价，模拟止盈。"),
                        )
                        return (
                            _attach_execution_fills(
                                closed,
                                entry_fill=entry_fill,
                                exit_fill=target_match.fill,
                            ),
                            deferred_fills,
                        )
                if holding_days >= max_holding_days:
                    time_match = _paper_match_row(
                        trade=trade,
                        row=row,
                        trade_date=trade_date,
                        context=execution_context,
                        side=OrderSide.SELL,
                        order_type=OrderType.MARKET,
                        quantity=quantity,
                        event_suffix=f"daily:{trade_date.isoformat()}:time",
                        previous_close=previous_close,
                        allow_partial=False,
                    )
                    if time_match.fill is None:
                        deferred_fills += 1
                        notes = _append_note(
                            notes,
                            _paper_execution_deferred_note(time_match.reason),
                        )
                        continue
                    closed = _closed_update(
                        status="time_exit",
                        entry_date=entry_date,
                        entry_price=entry_price,
                        exit_date=trade_date,
                        exit_price=time_match.fill.price,
                        latest_price=close,
                        holding_days=holding_days,
                        notes=_append_note(
                            notes,
                            "达到最长持有窗口，按开盘可成交价模拟退出。",
                        ),
                    )
                    return (
                        _attach_execution_fills(
                            closed,
                            entry_fill=entry_fill,
                            exit_fill=time_match.fill,
                        ),
                        deferred_fills,
                    )
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
                        notes=_append_note(notes, "触及初始止损，模拟离场。"),
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
                        notes=_append_note(notes, "触及第一目标价，模拟止盈。"),
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
                        notes=_append_note(notes, "达到最长持有窗口，按收盘价模拟退出。"),
                    ),
                    deferred_fills,
                )

    latest = ordered.iloc[-1]
    latest_date = latest["trade_date"]
    latest_price = Decimal(str(latest["close"]))
    if status == "open" and entry_date is not None and entry_price is not None:
        update = {
            "status": "open",
            "entry_date": entry_date,
            "entry_price": entry_price,
            "latest_date": latest_date,
            "latest_price": latest_price,
            "unrealized_return_pct": Decimal(str(_return_pct(entry_price, latest_price))),
            "holding_days": max((latest_date - entry_date).days, 0),
            "notes": notes,
        }
        return (
            _attach_execution_fills(update, entry_fill=entry_fill),
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
    source_context: PaperTradeSourceContext | None = None,
    execution_context: _PaperExecutionContext | None = None,
) -> tuple[dict[str, object] | None, int, int, int]:
    getter = getattr(provider, "get_minute_bars", None)
    if getter is None or not _is_a_share_trade(trade):
        return None, 0, 0, 0
    if source_context is None:
        source_context = repo.get_trade_source_context(trade.source_snapshot_id)
    signal_datetime = _trade_signal_datetime(trade, source_context)
    if signal_datetime is None:
        return None, 0, 0, 0
    start = signal_datetime
    end = _a_share_local_datetime(as_of).replace(tzinfo=None)
    try:
        minute_bars = getter([trade.instrument_id], start, end)
    except Exception:
        return None, 1, 0, 0
    if minute_bars.empty:
        return None, 1, 0, 0
    update = _evaluate_trade_with_minutes(
        trade,
        minute_bars,
        max_holding_days=max_holding_days,
        max_entry_wait_days=max_entry_wait_days,
        signal_datetime=signal_datetime,
        no_chase_above=_trade_no_chase_above(trade, source_context),
        source_latest_close=_source_context_latest_close(source_context),
        execution_context=execution_context,
    )
    deferred = int(update.pop(_DEFERRED_FILL_UPDATE_KEY, 0))
    return update, 1, len(minute_bars), deferred


def _evaluate_trade_with_minutes(
    trade: PaperTradeRecord,
    minute_bars: pd.DataFrame,
    *,
    max_holding_days: int,
    max_entry_wait_days: int,
    signal_datetime: datetime,
    no_chase_above: Decimal | None,
    source_latest_close: Decimal | None = None,
    execution_context: _PaperExecutionContext | None = None,
) -> dict[str, object]:
    ordered = minute_bars.sort_values("timestamp").reset_index(drop=True)
    ordered["timestamp"] = pd.to_datetime(ordered["timestamp"], errors="coerce")
    ordered = ordered.dropna(subset=["timestamp"])
    ordered = ordered[ordered["timestamp"] > signal_datetime].reset_index(drop=True)
    if ordered.empty:
        return {
            "status": trade.status,
            "holding_days": trade.holding_days,
            "notes": _append_note(trade.notes, "分钟数据尚未覆盖推荐后的交易时间。"),
        }
    first = ordered.iloc[0]
    first_open = Decimal(str(first["open"]))
    if trade.status == "pending" and _paper_price_basis_discontinuous(
        trade.instrument_id,
        source_latest_close or trade.trigger_price,
        first_open,
    ):
        latest = ordered.iloc[-1]
        return _invalidated_price_basis_update(
            trade,
            latest_date=latest["timestamp"].date(),
            latest_price=Decimal(str(latest["close"])),
        )
    entry_date = trade.entry_date
    entry_price = trade.entry_price
    status = trade.status
    notes = trade.notes
    entry_fill: _PaperMatchedFill | None = None
    deferred_fills = 0

    for row_index, row in ordered.iterrows():
        timestamp = row["timestamp"].to_pydatetime()
        trade_date = timestamp.date()
        open_price = Decimal(str(row["open"]))
        high = Decimal(str(row["high"]))
        low = Decimal(str(row["low"]))
        close = Decimal(str(row["close"]))
        previous_close = _paper_minute_previous_close(
            ordered,
            row_index,
            trade_date,
            source_latest_close,
        )

        if status == "pending":
            wait_days = max((trade_date - trade.signal_date).days, 0)
            match = None
            if execution_context is not None:
                match = _paper_match_row(
                    trade=trade,
                    row=row,
                    trade_date=trade_date,
                    context=execution_context,
                    side=OrderSide.BUY,
                    order_type=OrderType.STOP,
                    stop_price=trade.trigger_price,
                    quantity=_paper_entry_quantity(trade, execution_context),
                    event_suffix=f"minute:{timestamp.isoformat()}:entry",
                    previous_close=previous_close,
                    max_notional=execution_context.allocation,
                )
            trigger_reached = match.triggered if match is not None else high >= trade.trigger_price
            if trigger_reached:
                if match is not None and match.fill is None:
                    deferred_fills += 1
                    notes = _append_note(notes, _paper_execution_deferred_note(match.reason))
                    continue
                missed = _minute_entry_missed(
                    trigger_price=trade.trigger_price,
                    no_chase_above=no_chase_above,
                    open_price=open_price,
                    low=low,
                )
                if missed:
                    return _attach_execution_fills(
                        {
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
                        },
                        deferred_fills=deferred_fills,
                    )
                status = "open"
                entry_date = trade_date
                if match is not None:
                    assert match.fill is not None
                    entry_fill = match.fill
                    entry_price = entry_fill.price
                else:
                    entry_price = _minute_entry_price(
                        trigger_price=trade.trigger_price,
                        open_price=open_price,
                        low=low,
                    )
                notes = _append_note(notes, "分钟线确认触发价，模拟开仓。")
            elif wait_days > max_entry_wait_days:
                return _attach_execution_fills(
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
                    deferred_fills=deferred_fills,
                )
            else:
                continue

        if status == "open" and entry_date is not None and entry_price is not None:
            if trade_date < entry_date:
                continue
            holding_days = max((trade_date - entry_date).days, 0)
            settlement_days = (
                execution_context.rules.settlement_days if execution_context is not None else 1
            )
            if settlement_days > 0 and trade_date == entry_date:
                notes = _append_note(notes, "A股 T+1：买入当日不模拟卖出。")
                continue
            if execution_context is not None:
                quantity = _paper_position_quantity(
                    trade,
                    execution_context,
                    entry_fill,
                    entry_price,
                )
                if trade.initial_stop is not None:
                    stop_match = _paper_match_row(
                        trade=trade,
                        row=row,
                        trade_date=trade_date,
                        context=execution_context,
                        side=OrderSide.SELL,
                        order_type=OrderType.STOP,
                        stop_price=trade.initial_stop,
                        quantity=quantity,
                        event_suffix=f"minute:{timestamp.isoformat()}:stop",
                        previous_close=previous_close,
                        allow_partial=False,
                    )
                    if stop_match.triggered:
                        if stop_match.fill is None:
                            deferred_fills += 1
                            notes = _append_note(
                                notes,
                                _paper_execution_deferred_note(stop_match.reason),
                            )
                            continue
                        closed = _closed_update(
                            status="stopped",
                            entry_date=entry_date,
                            entry_price=entry_price,
                            exit_date=trade_date,
                            exit_price=stop_match.fill.price,
                            latest_price=close,
                            holding_days=holding_days,
                            notes=_append_note(
                                notes,
                                "分钟线触及初始止损，模拟离场。",
                            ),
                        )
                        return _attach_execution_fills(
                            closed,
                            entry_fill=entry_fill,
                            exit_fill=stop_match.fill,
                            deferred_fills=deferred_fills,
                        )
                if trade.target_1 is not None:
                    target_match = _paper_match_row(
                        trade=trade,
                        row=row,
                        trade_date=trade_date,
                        context=execution_context,
                        side=OrderSide.SELL,
                        order_type=OrderType.LIMIT,
                        limit_price=trade.target_1,
                        quantity=quantity,
                        event_suffix=f"minute:{timestamp.isoformat()}:target",
                        previous_close=previous_close,
                        allow_partial=False,
                    )
                    if target_match.triggered:
                        if target_match.fill is None:
                            deferred_fills += 1
                            notes = _append_note(
                                notes,
                                _paper_execution_deferred_note(target_match.reason),
                            )
                            continue
                        closed = _closed_update(
                            status="target_1_hit",
                            entry_date=entry_date,
                            entry_price=entry_price,
                            exit_date=trade_date,
                            exit_price=target_match.fill.price,
                            latest_price=close,
                            holding_days=holding_days,
                            notes=_append_note(
                                notes,
                                "分钟线触及第一目标价，模拟止盈。",
                            ),
                        )
                        return _attach_execution_fills(
                            closed,
                            entry_fill=entry_fill,
                            exit_fill=target_match.fill,
                            deferred_fills=deferred_fills,
                        )
                if holding_days >= max_holding_days:
                    time_match = _paper_match_row(
                        trade=trade,
                        row=row,
                        trade_date=trade_date,
                        context=execution_context,
                        side=OrderSide.SELL,
                        order_type=OrderType.MARKET,
                        quantity=quantity,
                        event_suffix=f"minute:{timestamp.isoformat()}:time",
                        previous_close=previous_close,
                        allow_partial=False,
                    )
                    if time_match.fill is None:
                        deferred_fills += 1
                        notes = _append_note(
                            notes,
                            _paper_execution_deferred_note(time_match.reason),
                        )
                        continue
                    closed = _closed_update(
                        status="time_exit",
                        entry_date=entry_date,
                        entry_price=entry_price,
                        exit_date=trade_date,
                        exit_price=time_match.fill.price,
                        latest_price=close,
                        holding_days=holding_days,
                        notes=_append_note(
                            notes,
                            "达到最长持有窗口，按分钟可成交价模拟退出。",
                        ),
                    )
                    return _attach_execution_fills(
                        closed,
                        entry_fill=entry_fill,
                        exit_fill=time_match.fill,
                        deferred_fills=deferred_fills,
                    )
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
                    notes=_append_note(notes, "分钟线触及初始止损，模拟离场。"),
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
                    notes=_append_note(notes, "分钟线触及第一目标价，模拟止盈。"),
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
                    notes=_append_note(notes, "达到最长持有窗口，按分钟收盘价模拟退出。"),
                )

    latest = ordered.iloc[-1]
    latest_date = latest["timestamp"].date()
    latest_price = Decimal(str(latest["close"]))
    if status == "open" and entry_date is not None and entry_price is not None:
        return _attach_execution_fills(
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
            entry_fill=entry_fill,
            deferred_fills=deferred_fills,
        )
    return _attach_execution_fills(
        {
            "status": "pending",
            "latest_date": latest_date,
            "latest_price": latest_price,
            "holding_days": 0,
            "notes": _append_note(notes, "分钟线未到触发价，继续等待。"),
        },
        deferred_fills=deferred_fills,
    )


def _paper_price_basis_discontinuous(
    instrument_id: str,
    reference_price: Decimal | None,
    observed_price: Decimal | None,
    *,
    max_gap_ratio: Decimal | None = None,
) -> bool:
    if (
        reference_price is None
        or observed_price is None
        or reference_price <= 0
        or observed_price <= 0
    ):
        return False
    gap_limit = max_gap_ratio or paper_price_basis_gap_limit(instrument_id)
    return abs(observed_price - reference_price) / reference_price > gap_limit


def _invalidated_price_basis_update(
    trade: PaperTradeRecord,
    *,
    latest_date: date,
    latest_price: Decimal,
) -> dict[str, object]:
    return {
        "status": "invalidated",
        "latest_date": latest_date,
        "latest_price": latest_price,
        "exit_date": latest_date,
        "exit_price": None,
        "realized_return_pct": None,
        "holding_days": 0,
        "notes": _append_note(
            trade.notes,
            (
                "推荐快照与首个盘中价格口径跳变超过"
                f"{int(paper_price_basis_gap_limit(trade.instrument_id) * 100)}%，"
                "样本作废并释放名额。"
            ),
        ),
    }


def _snapshot_reference_price(
    snapshot: OpportunitySnapshotRecord,
) -> Decimal | None:
    latest = _positive_decimal_or_none(getattr(snapshot, "latest_close", None))
    if latest is not None:
        return latest
    raw_card = getattr(snapshot, "card", {})
    card = raw_card if isinstance(raw_card, dict) else {}
    trading_status = card.get("trading_status")
    if not isinstance(trading_status, dict):
        return None
    return _positive_decimal_or_none(trading_status.get("latest_close"))


def _source_context_latest_close(
    source_context: PaperTradeSourceContext | None,
) -> Decimal | None:
    if source_context is None:
        return None
    latest = _positive_decimal_or_none(source_context.latest_close)
    if latest is not None:
        return latest
    trading_status = source_context.card.get("trading_status")
    if not isinstance(trading_status, dict):
        return None
    return _positive_decimal_or_none(trading_status.get("latest_close"))


def _positive_decimal_or_none(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


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
