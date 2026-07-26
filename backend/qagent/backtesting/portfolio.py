from dataclasses import dataclass
from datetime import date
from decimal import (
    Decimal,
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_UP,
)
from enum import Enum

import pandas as pd
from pydantic import BaseModel

from qagent.backtesting.engine import BacktestSignal, run_historical_backtest
from qagent.backtesting.execution import (
    HistoricalExecutionRule,
    VersionedAshareExecutionResolver,
    execute_daily_bar_order,
    execution_rules_from_historical,
    round_order_quantity,
)
from qagent.execution import (
    AShareExecutionRules,
    OrderSide,
    OrderType,
    fee_breakdown,
    participation_capacity,
    round_to_tick,
)
from qagent.domain.enums import Market
from qagent.market.calendars import trading_sessions_in_range
from qagent.market.indicators import wilder_atr
from qagent.providers.base import MarketDataProvider
from qagent.strategy_data.providers import StrategyDataProvider


LEGACY_EXECUTION_PROFILE = "intraday-touch-fixed-plan-v1"
ADAPTIVE_EXECUTION_PROFILE = "close-confirm-next-open-atr-risk-v1"


@dataclass(frozen=True)
class PortfolioExecutionProfile:
    key: str = LEGACY_EXECUTION_PROFILE
    close_confirmation: bool = False
    atr_stop_multiple: Decimal | None = None
    minimum_stop_pct: Decimal | None = None
    maximum_stop_pct: Decimal | None = None
    target_r_multiple: Decimal | None = None
    breakeven_r_multiple: Decimal | None = None


DEFAULT_EXECUTION_PROFILE = PortfolioExecutionProfile()
ADAPTIVE_CONFIRMATION_EXECUTION_PROFILE = PortfolioExecutionProfile(
    key=ADAPTIVE_EXECUTION_PROFILE,
    close_confirmation=True,
    atr_stop_multiple=Decimal("2"),
    minimum_stop_pct=Decimal("6"),
    maximum_stop_pct=Decimal("10"),
    target_r_multiple=Decimal("2"),
    breakeven_r_multiple=Decimal("1"),
)


class CandidateOutcomeStatus(str, Enum):
    RESOLVED = "resolved"
    NOT_TRIGGERED_OR_UNFILLABLE = "not_triggered_or_unfillable"
    EXIT_UNFILLABLE = "exit_unfillable"
    INVALID_PLAN = "invalid_plan"
    INSUFFICIENT_FUTURE_DATA = "insufficient_future_data"
    OUTSIDE_REQUESTED_RANGE = "outside_requested_range"


class CandidateSignalOutcome(BaseModel):
    snapshot_id: str
    instrument_id: str
    strategy_id: str | None
    signal_date: date
    status: CandidateOutcomeStatus
    status_detail: str
    nominal_amount: Decimal
    resolved_at: date | None = None
    entry_date: date | None = None
    exit_date: date | None = None
    exit_reason: str | None = None
    entry_price: Decimal | None = None
    exit_price: Decimal | None = None
    shares: Decimal | None = None
    entry_value: Decimal | None = None
    exit_value: Decimal | None = None
    gross_pnl: Decimal | None = None
    entry_costs: Decimal | None = None
    exit_costs: Decimal | None = None
    costs: Decimal | None = None
    net_pnl: Decimal | None = None
    return_pct: float | None = None
    holding_days: int | None = None


class CandidateOutcomeLedgerResult(BaseModel):
    provider: str
    start: date
    end: date
    nominal_amount: Decimal
    outcomes: list[CandidateSignalOutcome]
    status_counts: dict[str, int]
    data_health: dict[str, str]


class PortfolioBacktestTrade(BaseModel):
    instrument_id: str
    strategy_id: str | None
    signal_date: date
    entry_date: date
    exit_date: date
    exit_reason: str
    entry_price: Decimal
    exit_price: Decimal
    shares: Decimal
    gross_pnl: Decimal
    costs: Decimal
    net_pnl: Decimal
    return_pct: float
    holding_days: int


class PortfolioEquityPoint(BaseModel):
    date: date
    equity: Decimal
    cash: Decimal
    market_value: Decimal = Decimal("0")
    open_positions: int
    drawdown_pct: float


class PortfolioBacktestSummary(BaseModel):
    provider: str
    symbols: list[str]
    start: date
    end: date
    initial_capital: Decimal
    final_equity: Decimal
    total_return_pct: float
    max_drawdown_pct: float
    trade_count: int
    win_rate: float | None
    profit_factor: float | None
    avg_trade_return_pct: float | None
    exposure_pct: float | None


class PortfolioMonthlyReturn(BaseModel):
    month: str
    starting_equity: Decimal
    ending_equity: Decimal
    return_pct: float


class PortfolioBacktestResult(BaseModel):
    summary: PortfolioBacktestSummary
    trades: list[PortfolioBacktestTrade]
    equity_curve: list[PortfolioEquityPoint]
    monthly_returns: list[PortfolioMonthlyReturn]
    data_health: dict[str, str]


@dataclass
class _TradeCandidate:
    signal: BacktestSignal
    entry_date: date
    exit_date: date
    exit_reason: str
    entry_price: Decimal
    exit_price: Decimal
    stop_price: Decimal
    holding_days: int
    execution_rule: HistoricalExecutionRule | None = None
    exit_execution_rule: HistoricalExecutionRule | None = None
    max_executable_shares: Decimal | None = None
    exit_executable_shares: Decimal | None = None


@dataclass(frozen=True)
class _CandidateResolution:
    status: CandidateOutcomeStatus
    detail: str
    candidate: _TradeCandidate | None = None
    resolved_at: date | None = None


@dataclass
class _OpenPortfolioPosition:
    trade: PortfolioBacktestTrade
    entry_costs: Decimal
    exit_costs: Decimal


def run_portfolio_backtest(
    instrument_ids: list[str],
    provider: MarketDataProvider,
    start: date,
    end: date,
    step_days: int = 5,
    initial_capital: Decimal = Decimal("100000"),
    risk_per_trade_pct: Decimal = Decimal("1"),
    max_positions: int = 5,
    transaction_cost_bps: Decimal = Decimal("5"),
    slippage_bps: Decimal = Decimal("5"),
    fee_multiplier: Decimal = Decimal("1"),
    max_entry_wait_days: int = 5,
    max_holding_days: int = 20,
    strategy_data_provider: StrategyDataProvider | None = None,
    execution_rule_resolver: VersionedAshareExecutionResolver | None = None,
    execution_profile: PortfolioExecutionProfile = DEFAULT_EXECUTION_PROFILE,
) -> PortfolioBacktestResult:
    if start > end:
        raise ValueError("start must be on or before end")
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")
    if risk_per_trade_pct <= 0:
        raise ValueError("risk_per_trade_pct must be positive")
    if max_positions <= 0:
        raise ValueError("max_positions must be positive")

    signal_result = run_historical_backtest(
        instrument_ids=instrument_ids,
        provider=provider,
        start=start,
        end=end,
        step_days=step_days,
        max_signals=500,
        strategy_data_provider=strategy_data_provider,
    )
    result = run_signal_portfolio_backtest(
        signals=signal_result.signals,
        instrument_ids=instrument_ids,
        provider=provider,
        start=start,
        end=end,
        initial_capital=initial_capital,
        risk_per_trade_pct=risk_per_trade_pct,
        max_positions=max_positions,
        transaction_cost_bps=transaction_cost_bps,
        slippage_bps=slippage_bps,
        fee_multiplier=fee_multiplier,
        max_entry_wait_days=max_entry_wait_days,
        max_holding_days=max_holding_days,
        execution_rule_resolver=execution_rule_resolver,
        execution_profile=execution_profile,
    )
    result.data_health["source_backtest_scans"] = str(signal_result.summary.scan_count)
    return result


def run_signal_portfolio_backtest(
    *,
    signals: list[BacktestSignal],
    instrument_ids: list[str],
    provider: MarketDataProvider,
    start: date,
    end: date,
    initial_capital: Decimal = Decimal("100000"),
    risk_per_trade_pct: Decimal = Decimal("1"),
    max_positions: int = 5,
    transaction_cost_bps: Decimal = Decimal("5"),
    slippage_bps: Decimal = Decimal("5"),
    fee_multiplier: Decimal = Decimal("1"),
    max_entry_wait_days: int = 5,
    max_holding_days: int = 20,
    execution_rule_resolver: VersionedAshareExecutionResolver | None = None,
    execution_profile: PortfolioExecutionProfile = DEFAULT_EXECUTION_PROFILE,
) -> PortfolioBacktestResult:
    if start > end:
        raise ValueError("start must be on or before end")
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")
    if risk_per_trade_pct <= 0:
        raise ValueError("risk_per_trade_pct must be positive")
    if max_positions <= 0:
        raise ValueError("max_positions must be positive")
    if fee_multiplier <= 0:
        raise ValueError("fee_multiplier must be positive")
    bars = provider.get_daily_bars(
        instrument_ids,
        start=start,
        end=end,
    )
    bars = _normalize_bars(bars)
    candidates = _build_candidates(
        signals,
        bars,
        slippage_bps=slippage_bps,
        max_entry_wait_days=max_entry_wait_days,
        max_holding_days=max_holding_days,
        execution_rule_resolver=execution_rule_resolver,
        execution_profile=execution_profile,
    )
    candidates = [candidate for candidate in candidates if candidate.exit_date <= end]
    trades, equity_curve = _simulate_portfolio(
        candidates,
        start=start,
        end=end,
        bars=bars,
        initial_capital=initial_capital,
        risk_per_trade_pct=risk_per_trade_pct,
        max_positions=max_positions,
        transaction_cost_bps=transaction_cost_bps,
        fee_multiplier=fee_multiplier,
    )
    summary = _build_summary(
        provider_name=provider.name,
        instrument_ids=instrument_ids,
        start=start,
        end=end,
        initial_capital=initial_capital,
        trades=trades,
        equity_curve=equity_curve,
    )
    data_health = {
        "provider": provider.name,
        "symbols": str(len(instrument_ids)),
        "source_signals": str(len(signals)),
        "trade_candidates": str(len(candidates)),
        "trades": str(len(trades)),
        "lookahead_guard": "signals_generated_before_exits",
        "history_cutoff": "hard_end_date_no_future_bars",
        "portfolio_model": "fixed_risk_stop_target_time_exit",
        "execution_profile": execution_profile.key,
        "entry_confirmation": (
            "close_then_next_open"
            if execution_profile.close_confirmation
            else "intraday_trigger_touch"
        ),
        "execution_rules": (
            "unified_execution_kernel:gap,suspension,zero_volume,"
            "one_price_limit,tick,round_lot,fees,no_chase,target_guard"
        ),
        "cn_execution_rules": (
            "versioned_replay_evidence"
            if execution_rule_resolver is not None
            else "legacy_symbol_fallback"
        ),
        "max_positions": str(max_positions),
        "risk_per_trade_pct": str(risk_per_trade_pct),
        "fee_multiplier": str(fee_multiplier),
        "slippage_bps": str(slippage_bps),
        "atr_stop_multiple": str(execution_profile.atr_stop_multiple or ""),
        "minimum_stop_pct": str(execution_profile.minimum_stop_pct or ""),
        "maximum_stop_pct": str(execution_profile.maximum_stop_pct or ""),
        "target_r_multiple": str(execution_profile.target_r_multiple or ""),
        "breakeven_r_multiple": str(execution_profile.breakeven_r_multiple or ""),
    }
    provider_errors = getattr(provider, "last_errors", [])
    if provider_errors:
        data_health["errors"] = " | ".join(provider_errors[:3])
    return PortfolioBacktestResult(
        summary=summary,
        trades=trades,
        equity_curve=equity_curve,
        monthly_returns=_build_monthly_returns(equity_curve),
        data_health=data_health,
    )


def resolve_candidate_outcome_ledger(
    *,
    signals: list[BacktestSignal],
    provider: MarketDataProvider,
    start: date,
    end: date,
    nominal_amount: Decimal = Decimal("100000"),
    transaction_cost_bps: Decimal = Decimal("5"),
    slippage_bps: Decimal = Decimal("5"),
    fee_multiplier: Decimal = Decimal("1"),
    max_entry_wait_days: int = 5,
    max_holding_days: int = 20,
    execution_rule_resolver: VersionedAshareExecutionResolver | None = None,
    execution_profile: PortfolioExecutionProfile = DEFAULT_EXECUTION_PROFILE,
) -> CandidateOutcomeLedgerResult:
    """Resolve every signal independently, without portfolio capital or position limits."""

    if start > end:
        raise ValueError("start must be on or before end")
    if nominal_amount <= 0:
        raise ValueError("nominal_amount must be positive")
    if transaction_cost_bps < 0:
        raise ValueError("transaction_cost_bps must be non-negative")
    if slippage_bps < 0:
        raise ValueError("slippage_bps must be non-negative")
    if fee_multiplier <= 0:
        raise ValueError("fee_multiplier must be positive")
    if max_entry_wait_days <= 0:
        raise ValueError("max_entry_wait_days must be positive")
    if max_holding_days <= 0:
        raise ValueError("max_holding_days must be positive")

    ordered_signals = sorted(
        signals,
        key=lambda signal: (
            signal.signal_date,
            signal.instrument_id,
            signal.snapshot_id,
            Decimal(signal.rank_score),
        ),
    )
    instrument_ids = sorted({signal.instrument_id for signal in ordered_signals})
    if instrument_ids:
        bars = provider.get_daily_bars(
            instrument_ids,
            start=start,
            end=end,
        )
        bars = _normalize_bars(bars)
    else:
        bars = pd.DataFrame()
    bars_by_instrument = (
        {
            str(instrument_id): frame.reset_index(drop=True)
            for instrument_id, frame in bars.groupby("instrument_id", sort=False)
        }
        if not bars.empty
        else {}
    )

    outcomes: list[CandidateSignalOutcome] = []
    for signal in ordered_signals:
        if signal.signal_date < start or signal.signal_date > end:
            outcomes.append(
                _unresolved_candidate_outcome(
                    signal,
                    CandidateOutcomeStatus.OUTSIDE_REQUESTED_RANGE,
                    "signal_date_outside_requested_range",
                    nominal_amount,
                    resolved_at=signal.signal_date,
                )
            )
            continue
        resolution = _candidate_resolution_from_signal(
            signal,
            bars_by_instrument.get(signal.instrument_id, pd.DataFrame()),
            slippage_bps=slippage_bps,
            max_entry_wait_days=max_entry_wait_days,
            max_holding_days=max_holding_days,
            execution_rule_resolver=execution_rule_resolver,
            execution_profile=execution_profile,
        )
        if resolution.candidate is None:
            outcomes.append(
                _unresolved_candidate_outcome(
                    signal,
                    resolution.status,
                    resolution.detail,
                    nominal_amount,
                    resolved_at=resolution.resolved_at,
                )
            )
            continue
        outcomes.append(
            _resolved_candidate_outcome(
                resolution.candidate,
                nominal_amount=nominal_amount,
                transaction_cost_bps=transaction_cost_bps,
                fee_multiplier=fee_multiplier,
            )
        )

    status_counts = {
        status.value: sum(outcome.status == status for outcome in outcomes)
        for status in CandidateOutcomeStatus
    }
    data_health = {
        "provider": provider.name,
        "source_signals": str(len(signals)),
        "resolved_signals": str(status_counts[CandidateOutcomeStatus.RESOLVED.value]),
        "independent_resolution": "no_capital_or_position_capacity",
        "nominal_amount": str(nominal_amount),
        "transaction_cost_bps": str(transaction_cost_bps),
        "slippage_bps": str(slippage_bps),
        "fee_multiplier": str(fee_multiplier),
        "max_entry_wait_days": str(max_entry_wait_days),
        "max_holding_days": str(max_holding_days),
        "result_availability": "explicit_resolved_at",
        "history_cutoff": "hard_end_date_no_future_bars",
        "liquidity_sizing": "entry_date_only_exit_capacity_is_censoring_gate",
        "execution_profile": execution_profile.key,
        "cn_execution_rules": (
            "versioned_replay_evidence"
            if execution_rule_resolver is not None
            else "legacy_symbol_fallback"
        ),
    }
    provider_errors = getattr(provider, "last_errors", [])
    if provider_errors:
        data_health["errors"] = " | ".join(provider_errors[:3])
    return CandidateOutcomeLedgerResult(
        provider=provider.name,
        start=start,
        end=end,
        nominal_amount=nominal_amount,
        outcomes=outcomes,
        status_counts=status_counts,
        data_health=data_health,
    )


def _normalize_bars(bars: pd.DataFrame) -> pd.DataFrame:
    if bars.empty:
        return bars
    frame = bars.copy()
    if pd.api.types.is_datetime64_any_dtype(frame["trade_date"]):
        frame["trade_date"] = frame["trade_date"].dt.date
    return frame.sort_values(["instrument_id", "trade_date"]).reset_index(drop=True)


def _build_candidates(
    signals: list[BacktestSignal],
    bars: pd.DataFrame,
    slippage_bps: Decimal,
    max_entry_wait_days: int,
    max_holding_days: int,
    execution_rule_resolver: VersionedAshareExecutionResolver | None = None,
    execution_profile: PortfolioExecutionProfile = DEFAULT_EXECUTION_PROFILE,
) -> list[_TradeCandidate]:
    candidates: list[_TradeCandidate] = []
    bars_by_instrument = {
        str(instrument_id): frame.reset_index(drop=True)
        for instrument_id, frame in bars.groupby("instrument_id", sort=False)
    }
    sorted_signals = sorted(
        signals,
        key=lambda signal: (signal.signal_date, Decimal(signal.rank_score)),
        reverse=False,
    )
    for signal in sorted_signals:
        candidate = _candidate_from_signal(
            signal,
            bars_by_instrument.get(signal.instrument_id, pd.DataFrame()),
            slippage_bps=slippage_bps,
            max_entry_wait_days=max_entry_wait_days,
            max_holding_days=max_holding_days,
            execution_rule_resolver=execution_rule_resolver,
            execution_profile=execution_profile,
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _candidate_from_signal(
    signal: BacktestSignal,
    bars: pd.DataFrame,
    slippage_bps: Decimal,
    max_entry_wait_days: int,
    max_holding_days: int,
    execution_rule_resolver: VersionedAshareExecutionResolver | None = None,
    execution_profile: PortfolioExecutionProfile = DEFAULT_EXECUTION_PROFILE,
) -> _TradeCandidate | None:
    return _candidate_resolution_from_signal(
        signal,
        bars,
        slippage_bps=slippage_bps,
        max_entry_wait_days=max_entry_wait_days,
        max_holding_days=max_holding_days,
        execution_rule_resolver=execution_rule_resolver,
        execution_profile=execution_profile,
    ).candidate


def _candidate_resolution_from_signal(
    signal: BacktestSignal,
    bars: pd.DataFrame,
    slippage_bps: Decimal,
    max_entry_wait_days: int,
    max_holding_days: int,
    execution_rule_resolver: VersionedAshareExecutionResolver | None = None,
    execution_profile: PortfolioExecutionProfile = DEFAULT_EXECUTION_PROFILE,
) -> _CandidateResolution:
    if signal.trigger_price is None:
        return _CandidateResolution(
            CandidateOutcomeStatus.INVALID_PLAN,
            "missing_trigger_price",
        )
    if bars.empty:
        return _CandidateResolution(
            CandidateOutcomeStatus.INSUFFICIENT_FUTURE_DATA,
            "no_daily_bars",
        )

    trigger = Decimal(signal.trigger_price)
    stop = (
        Decimal(signal.initial_stop)
        if signal.initial_stop is not None
        else trigger * Decimal("0.95")
    )
    target = Decimal(signal.target_1) if signal.target_1 is not None else None
    no_chase = Decimal(signal.no_chase_above) if signal.no_chase_above is not None else None
    if (
        trigger <= 0
        or stop <= 0
        or stop >= trigger
        or (target is not None and target <= trigger)
        or (no_chase is not None and no_chase < trigger)
    ):
        return _CandidateResolution(
            CandidateOutcomeStatus.INVALID_PLAN,
            "invalid_trigger_stop_target_or_no_chase",
        )

    ordered = bars.sort_values("trade_date").reset_index(drop=True)
    future = ordered.loc[ordered["trade_date"] > signal.signal_date]
    if future.empty:
        return _CandidateResolution(
            CandidateOutcomeStatus.INSUFFICIENT_FUTURE_DATA,
            "no_sessions_after_signal",
        )

    entry_index: int | None = None
    entry_rule: HistoricalExecutionRule | None = None
    entry_price: Decimal | None = None
    entry_capacity = 0
    entry_triggered = False
    close_confirmed = False
    for index, row in future.head(max_entry_wait_days).iterrows():
        previous = ordered.iloc[index - 1] if index > 0 else None
        row_rule = _resolve_execution_rule(
            execution_rule_resolver,
            signal.instrument_id,
            row,
        )
        rules = _execution_rules_for_row(
            signal.instrument_id,
            row["trade_date"],
            row_rule,
            side=OrderSide.BUY,
            slippage_bps=slippage_bps,
        )
        trigger_order_price = round_to_tick(
            trigger,
            rules.tick_size,
            rounding=ROUND_CEILING,
        )
        probe_quantity = _execution_probe_quantity(row_rule, rules)
        if execution_profile.close_confirmation and not close_confirmed:
            close = Decimal(str(row["close"]))
            if (
                _row_has_trades(row)
                and close >= trigger_order_price
                and (no_chase is None or close <= no_chase)
                and (target is None or close < target)
            ):
                close_confirmed = True
            continue
        fill = execute_daily_bar_order(
            instrument_id=signal.instrument_id,
            row=row,
            previous=previous,
            side=OrderSide.BUY,
            quantity=probe_quantity,
            order_type=(
                OrderType.MARKET
                if execution_profile.close_confirmation or entry_triggered
                else OrderType.STOP
            ),
            rules=rules,
            stop_price=(
                None
                if execution_profile.close_confirmation or entry_triggered
                else trigger_order_price
            ),
            intent_id=f"{signal.snapshot_id}:entry:{row['trade_date']}",
        )
        touched = _row_has_trades(row) and (
            Decimal(str(row["open"])) >= trigger_order_price
            or Decimal(str(row["high"])) >= trigger_order_price
        )
        if fill is not None and fill.quantity == probe_quantity:
            if (no_chase is not None and fill.price > no_chase) or (
                target is not None and fill.price >= target
            ):
                return _CandidateResolution(
                    CandidateOutcomeStatus.NOT_TRIGGERED_OR_UNFILLABLE,
                    "entry_fill_outside_plan",
                    resolved_at=row["trade_date"],
                )
            entry_index = index
            entry_rule = row_rule
            entry_price = fill.price
            entry_capacity = participation_capacity(_row_volume(row), rules)
            break
        if touched:
            entry_triggered = True
    if entry_index is None or entry_price is None:
        observed_entry_sessions = len(future.head(max_entry_wait_days))
        if observed_entry_sessions < max_entry_wait_days:
            return _CandidateResolution(
                CandidateOutcomeStatus.INSUFFICIENT_FUTURE_DATA,
                "entry_wait_window_incomplete",
            )
        return _CandidateResolution(
            CandidateOutcomeStatus.NOT_TRIGGERED_OR_UNFILLABLE,
            "entry_not_triggered_or_fill_blocked",
            resolved_at=future.head(max_entry_wait_days).iloc[-1]["trade_date"],
        )

    entry_date = ordered.iloc[entry_index]["trade_date"]
    stop, target = _execution_plan_prices(
        signal=signal,
        bars=ordered,
        entry_price=entry_price,
        trigger=trigger,
        original_stop=stop,
        original_target=target,
        execution_profile=execution_profile,
    )
    settlement_days = (
        entry_rule.settlement_days
        if entry_rule is not None
        else (1 if _is_cn(signal.instrument_id) else 0)
    )
    if max_holding_days <= settlement_days:
        return _CandidateResolution(
            CandidateOutcomeStatus.INVALID_PLAN,
            "holding_window_not_longer_than_settlement",
            resolved_at=entry_date,
        )

    exit_price: Decimal | None = None
    exit_date: date | None = None
    selected_exit_rule: HistoricalExecutionRule | None = None
    exit_reason: str | None = None
    exit_order_type: OrderType | None = None
    exit_order_price: Decimal | None = None
    exit_capacity = 0
    exit_rows = ordered.iloc[entry_index:]
    active_stop = stop
    initial_risk = entry_price - stop
    for session_offset, (row_index, row) in enumerate(exit_rows.iterrows()):
        trade_date = row["trade_date"]
        if session_offset < settlement_days:
            continue
        previous = ordered.iloc[row_index - 1] if row_index > 0 else None
        exit_rule = _resolve_execution_rule(
            execution_rule_resolver,
            signal.instrument_id,
            row,
        )
        rules = _execution_rules_for_row(
            signal.instrument_id,
            trade_date,
            exit_rule,
            side=OrderSide.SELL,
            slippage_bps=slippage_bps,
        )
        pending_before_session = exit_order_type is not None
        if (
            pending_before_session
            and exit_order_type == OrderType.LIMIT
            and session_offset >= max_holding_days - 1
        ):
            exit_reason = "time_exit"
            exit_order_type = OrderType.MARKET
            exit_order_price = None
        if exit_order_type is None:
            low = Decimal(str(row["low"]))
            high = Decimal(str(row["high"]))
            if _row_has_trades(row) and low <= active_stop:
                exit_reason = "stopped"
                exit_order_type = OrderType.STOP
                exit_order_price = round_to_tick(
                    active_stop,
                    rules.tick_size,
                    rounding=ROUND_FLOOR,
                )
            elif _row_has_trades(row) and target is not None and high >= target:
                exit_reason = "target_1_hit"
                exit_order_type = OrderType.LIMIT
                exit_order_price = round_to_tick(
                    target,
                    rules.tick_size,
                    rounding=ROUND_CEILING,
                )
            elif session_offset >= max_holding_days - 1:
                exit_reason = "time_exit"
                exit_order_type = OrderType.MARKET
            else:
                active_stop = _next_session_stop(
                    current_stop=active_stop,
                    entry_price=entry_price,
                    initial_risk=initial_risk,
                    row=row,
                    execution_profile=execution_profile,
                )
                continue

        probe_quantity = _execution_probe_quantity(exit_rule, rules)
        fill = execute_daily_bar_order(
            instrument_id=signal.instrument_id,
            row=row,
            previous=previous,
            side=OrderSide.SELL,
            quantity=probe_quantity,
            order_type=exit_order_type,
            rules=rules,
            limit_price=(exit_order_price if exit_order_type == OrderType.LIMIT else None),
            stop_price=(exit_order_price if exit_order_type == OrderType.STOP else None),
            intent_id=f"{signal.snapshot_id}:exit:{trade_date}",
        )
        if fill is not None and fill.quantity == probe_quantity:
            exit_date = trade_date
            exit_price = fill.price
            selected_exit_rule = exit_rule
            exit_capacity = participation_capacity(_row_volume(row), rules)
            break
        if exit_order_type == OrderType.STOP:
            # A triggered stop remains executable after a blocked daily bar.
            exit_order_type = OrderType.MARKET
            exit_order_price = None

    if exit_price is None or exit_date is None or exit_reason is None:
        if len(exit_rows) < max_holding_days:
            return _CandidateResolution(
                CandidateOutcomeStatus.INSUFFICIENT_FUTURE_DATA,
                "exit_window_incomplete",
            )
        return _CandidateResolution(
            CandidateOutcomeStatus.EXIT_UNFILLABLE,
            "exit_order_unfillable",
            resolved_at=exit_rows.iloc[-1]["trade_date"],
        )

    return _CandidateResolution(
        CandidateOutcomeStatus.RESOLVED,
        "resolved",
        _TradeCandidate(
            signal=signal,
            entry_date=entry_date,
            exit_date=exit_date,
            exit_reason=exit_reason,
            entry_price=entry_price,
            exit_price=exit_price,
            stop_price=stop,
            holding_days=max((exit_date - entry_date).days, 0),
            execution_rule=entry_rule,
            exit_execution_rule=selected_exit_rule,
            max_executable_shares=Decimal(entry_capacity),
            exit_executable_shares=Decimal(exit_capacity),
        ),
        resolved_at=exit_date,
    )


def _execution_plan_prices(
    *,
    signal: BacktestSignal,
    bars: pd.DataFrame,
    entry_price: Decimal,
    trigger: Decimal,
    original_stop: Decimal,
    original_target: Decimal | None,
    execution_profile: PortfolioExecutionProfile,
) -> tuple[Decimal, Decimal | None]:
    if execution_profile.atr_stop_multiple is None:
        return original_stop, original_target

    atr = _point_in_time_atr(bars, signal.signal_date, trigger)
    minimum_risk = (
        entry_price * (execution_profile.minimum_stop_pct or Decimal("0")) / Decimal("100")
    )
    maximum_risk = (
        entry_price * (execution_profile.maximum_stop_pct or Decimal("100")) / Decimal("100")
    )
    structure_risk = max(entry_price - original_stop, Decimal("0"))
    desired_risk = max(
        atr * execution_profile.atr_stop_multiple,
        minimum_risk,
        structure_risk,
    )
    risk = min(desired_risk, maximum_risk)
    if risk <= 0 or risk >= entry_price:
        return original_stop, original_target

    stop = (entry_price - risk).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    target = original_target
    if execution_profile.target_r_multiple is not None:
        target = (entry_price + risk * execution_profile.target_r_multiple).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
    return stop, target


def _point_in_time_atr(
    bars: pd.DataFrame,
    signal_date: date,
    fallback_price: Decimal,
) -> Decimal:
    history = bars.loc[bars["trade_date"] <= signal_date].sort_values("trade_date")
    if history.empty:
        return fallback_price * Decimal("0.04")
    series = wilder_atr(history, period=14)
    value = series.iloc[-1] if not series.empty else None
    if value is None or pd.isna(value) or float(value) <= 0:
        return fallback_price * Decimal("0.04")
    return Decimal(str(float(value)))


def _next_session_stop(
    *,
    current_stop: Decimal,
    entry_price: Decimal,
    initial_risk: Decimal,
    row: pd.Series,
    execution_profile: PortfolioExecutionProfile,
) -> Decimal:
    multiple = execution_profile.breakeven_r_multiple
    if multiple is None or initial_risk <= 0:
        return current_stop
    close = Decimal(str(row["close"]))
    if close >= entry_price + initial_risk * multiple:
        return max(current_stop, entry_price)
    return current_stop


def _simulate_portfolio(
    candidates: list[_TradeCandidate],
    start: date,
    initial_capital: Decimal,
    risk_per_trade_pct: Decimal,
    max_positions: int,
    transaction_cost_bps: Decimal,
    fee_multiplier: Decimal,
    bars: pd.DataFrame | None = None,
    end: date | None = None,
) -> tuple[list[PortfolioBacktestTrade], list[PortfolioEquityPoint]]:
    cash = _money(initial_capital)
    peak = cash
    open_positions: list[_OpenPortfolioPosition] = []
    closed_trades: list[PortfolioBacktestTrade] = []
    curve: list[PortfolioEquityPoint] = []
    latest_prices: dict[str, Decimal] = {}
    candidates_by_date: dict[date, list[_TradeCandidate]] = {}
    for candidate in sorted(
        candidates,
        key=lambda item: (
            item.entry_date,
            -Decimal(item.signal.rank_score),
            item.signal.instrument_id,
        ),
    ):
        candidates_by_date.setdefault(candidate.entry_date, []).append(candidate)

    final_date = max(
        [end or start, *(candidate.exit_date for candidate in candidates)],
    )
    trading_dates = {start, *candidates_by_date}
    trading_dates.update(candidate.exit_date for candidate in candidates)
    rows_by_date: dict[date, pd.DataFrame] = {}
    if bars is not None and not bars.empty:
        relevant = bars.loc[(bars["trade_date"] >= start) & (bars["trade_date"] <= final_date)]
        for trade_date, frame in relevant.groupby("trade_date", sort=True):
            rows_by_date[trade_date] = frame
            trading_dates.add(trade_date)
    symbols = {candidate.signal.instrument_id for candidate in candidates}
    if bars is not None and not bars.empty:
        symbols.update(str(value) for value in bars["instrument_id"].unique())
    if any(_is_cn(symbol) for symbol in symbols):
        trading_dates.update(trading_sessions_in_range(start, final_date, market=Market.CN))
    if any(not _is_cn(symbol) for symbol in symbols):
        trading_dates.update(trading_sessions_in_range(start, final_date, market=Market.US))

    for current_date in sorted(trading_dates):
        due = [position for position in open_positions if position.trade.exit_date <= current_date]
        for position in sorted(
            due,
            key=lambda item: (item.trade.exit_date, item.trade.instrument_id),
        ):
            trade = position.trade
            proceeds = trade.exit_price * trade.shares - position.exit_costs
            cash = _money(cash + proceeds)
            open_positions.remove(position)
            closed_trades.append(trade)

        sizing_equity = _money(cash + _open_market_value(open_positions, latest_prices))
        for candidate in candidates_by_date.get(current_date, []):
            if len(open_positions) >= max_positions:
                continue
            trade = _size_trade(
                candidate,
                equity=sizing_equity,
                cash=cash,
                risk_per_trade_pct=risk_per_trade_pct,
                max_positions=max_positions,
                transaction_cost_bps=transaction_cost_bps,
                fee_multiplier=fee_multiplier,
            )
            if trade is None:
                continue
            entry_costs, exit_costs = _trade_cost_breakdown(
                candidate,
                trade.shares,
                transaction_cost_bps,
                fee_multiplier,
            )
            entry_outlay = trade.entry_price * trade.shares + entry_costs
            if entry_outlay > cash:
                continue
            cash = _money(cash - entry_outlay)
            latest_prices[trade.instrument_id] = trade.entry_price
            position = _OpenPortfolioPosition(
                trade=trade,
                entry_costs=entry_costs,
                exit_costs=exit_costs,
            )
            if trade.exit_date <= current_date:
                cash = _money(cash + trade.exit_price * trade.shares - exit_costs)
                closed_trades.append(trade)
            else:
                open_positions.append(position)
            sizing_equity = _money(cash + _open_market_value(open_positions, latest_prices))

        for _, row in rows_by_date.get(current_date, pd.DataFrame()).iterrows():
            latest_prices[str(row["instrument_id"])] = Decimal(str(row["close"]))
        market_value = _money(_open_market_value(open_positions, latest_prices))
        equity = _money(cash + market_value)
        peak = max(peak, equity)
        point_cash = cash
        point_market_value = market_value
        point_equity = equity
        if (
            current_date == start
            and not open_positions
            and not closed_trades
            and cash == initial_capital
        ):
            point_cash = initial_capital
            point_market_value = Decimal("0")
            point_equity = initial_capital
        curve.append(
            PortfolioEquityPoint(
                date=current_date,
                equity=point_equity,
                cash=point_cash,
                market_value=point_market_value,
                open_positions=len(open_positions),
                drawdown_pct=_pct((equity - peak) / peak) if peak else 0.0,
            )
        )

    closed_trades.sort(key=lambda item: (item.exit_date, item.instrument_id))
    return closed_trades, curve


def _close_due_trades(
    current_date: date,
    open_trades: list[PortfolioBacktestTrade],
    closed_trades: list[PortfolioBacktestTrade],
    curve: list[PortfolioEquityPoint],
    equity: Decimal,
    peak: Decimal,
) -> tuple[Decimal, Decimal]:
    due = [trade for trade in open_trades if trade.exit_date <= current_date]
    for trade in sorted(due, key=lambda item: (item.exit_date, item.instrument_id)):
        equity = _money(equity + trade.net_pnl)
        peak = max(peak, equity)
        closed_trades.append(trade)
        open_trades.remove(trade)
        curve.append(
            PortfolioEquityPoint(
                date=trade.exit_date,
                equity=equity,
                cash=equity,
                open_positions=len(open_trades),
                drawdown_pct=_pct((equity - peak) / peak) if peak else 0.0,
            )
        )
    return equity, peak


def _size_trade(
    candidate: _TradeCandidate,
    equity: Decimal,
    risk_per_trade_pct: Decimal,
    max_positions: int,
    transaction_cost_bps: Decimal,
    fee_multiplier: Decimal = Decimal("1"),
    cash: Decimal | None = None,
) -> PortfolioBacktestTrade | None:
    per_share_risk = max(
        candidate.entry_price - candidate.stop_price,
        candidate.entry_price * Decimal("0.01"),
    )
    if per_share_risk <= 0:
        return None
    risk_budget = equity * (risk_per_trade_pct / Decimal("100"))
    capital_budget = equity / Decimal(max_positions)
    shares_by_risk = risk_budget / per_share_risk
    shares_by_capital = capital_budget / candidate.entry_price
    desired_shares = min(shares_by_risk, shares_by_capital)
    if candidate.max_executable_shares is not None:
        desired_shares = min(desired_shares, candidate.max_executable_shares)
    shares = _shares(
        desired_shares,
        candidate.signal.instrument_id,
        execution_rule=candidate.execution_rule,
    )
    shares = _round_for_exit_rule(shares, candidate.exit_execution_rule)
    if (
        candidate.exit_executable_shares is not None
        and shares > candidate.exit_executable_shares
    ):
        return None
    if cash is not None:
        shares = _fit_shares_to_cash(
            candidate,
            shares,
            cash,
            transaction_cost_bps,
            fee_multiplier,
        )
    if shares <= 0:
        return None

    gross_pnl = _money((candidate.exit_price - candidate.entry_price) * shares)
    entry_costs, exit_costs = _trade_cost_breakdown(
        candidate,
        shares,
        transaction_cost_bps,
        fee_multiplier,
    )
    costs = _money(entry_costs + exit_costs)
    net_pnl = _money(gross_pnl - costs)
    denominator = candidate.entry_price * shares
    return_pct = _pct(net_pnl / denominator) if denominator else 0.0
    return PortfolioBacktestTrade(
        instrument_id=candidate.signal.instrument_id,
        strategy_id=candidate.signal.primary_strategy_id,
        signal_date=candidate.signal.signal_date,
        entry_date=candidate.entry_date,
        exit_date=candidate.exit_date,
        exit_reason=candidate.exit_reason,
        entry_price=candidate.entry_price,
        exit_price=candidate.exit_price,
        shares=shares,
        gross_pnl=gross_pnl,
        costs=costs,
        net_pnl=net_pnl,
        return_pct=return_pct,
        holding_days=candidate.holding_days,
    )


def _trade_cost_breakdown(
    candidate: _TradeCandidate,
    shares: Decimal,
    transaction_cost_bps: Decimal,
    fee_multiplier: Decimal,
) -> tuple[Decimal, Decimal]:
    entry_value = candidate.entry_price * shares
    exit_value = candidate.exit_price * shares
    if candidate.execution_rule is None:
        rate = transaction_cost_bps / Decimal("10000")
        entry_base = entry_value * rate
        exit_base = exit_value * rate
    else:
        entry_rules = execution_rules_from_historical(
            candidate.execution_rule,
            side=OrderSide.BUY,
        )
        exit_rule = candidate.exit_execution_rule or candidate.execution_rule
        exit_rules = execution_rules_from_historical(
            exit_rule,
            side=OrderSide.SELL,
        )
        entry_base = fee_breakdown(
            OrderSide.BUY,
            entry_value,
            entry_rules,
        ).total
        exit_base = fee_breakdown(
            OrderSide.SELL,
            exit_value,
            exit_rules,
        ).total
    return _money(entry_base * fee_multiplier), _money(exit_base * fee_multiplier)


def _unresolved_candidate_outcome(
    signal: BacktestSignal,
    status: CandidateOutcomeStatus,
    detail: str,
    nominal_amount: Decimal,
    *,
    resolved_at: date | None = None,
) -> CandidateSignalOutcome:
    return CandidateSignalOutcome(
        snapshot_id=signal.snapshot_id,
        instrument_id=signal.instrument_id,
        strategy_id=signal.primary_strategy_id,
        signal_date=signal.signal_date,
        status=status,
        status_detail=detail,
        nominal_amount=nominal_amount,
        resolved_at=resolved_at,
    )


def _resolved_candidate_outcome(
    candidate: _TradeCandidate,
    *,
    nominal_amount: Decimal,
    transaction_cost_bps: Decimal,
    fee_multiplier: Decimal,
) -> CandidateSignalOutcome:
    desired_shares = nominal_amount / candidate.entry_price
    if candidate.max_executable_shares is not None:
        desired_shares = min(desired_shares, candidate.max_executable_shares)
    shares = _shares(
        desired_shares,
        candidate.signal.instrument_id,
        execution_rule=candidate.execution_rule,
    )
    shares = _round_for_exit_rule(shares, candidate.exit_execution_rule)
    if shares <= 0:
        return _unresolved_candidate_outcome(
            candidate.signal,
            CandidateOutcomeStatus.NOT_TRIGGERED_OR_UNFILLABLE,
            "nominal_amount_below_minimum_order",
            nominal_amount,
            resolved_at=candidate.entry_date,
        )
    if (
        candidate.exit_executable_shares is not None
        and shares > candidate.exit_executable_shares
    ):
        return _unresolved_candidate_outcome(
            candidate.signal,
            CandidateOutcomeStatus.EXIT_UNFILLABLE,
            "fixed_notional_exceeds_exit_liquidity",
            nominal_amount,
            resolved_at=candidate.exit_date,
        )

    entry_value = _money(candidate.entry_price * shares)
    exit_value = _money(candidate.exit_price * shares)
    gross_pnl = _money(exit_value - entry_value)
    entry_costs, exit_costs = _trade_cost_breakdown(
        candidate,
        shares,
        transaction_cost_bps,
        fee_multiplier,
    )
    costs = _money(entry_costs + exit_costs)
    net_pnl = _money(gross_pnl - costs)
    return CandidateSignalOutcome(
        snapshot_id=candidate.signal.snapshot_id,
        instrument_id=candidate.signal.instrument_id,
        strategy_id=candidate.signal.primary_strategy_id,
        signal_date=candidate.signal.signal_date,
        status=CandidateOutcomeStatus.RESOLVED,
        status_detail="resolved",
        nominal_amount=nominal_amount,
        resolved_at=candidate.exit_date,
        entry_date=candidate.entry_date,
        exit_date=candidate.exit_date,
        exit_reason=candidate.exit_reason,
        entry_price=candidate.entry_price,
        exit_price=candidate.exit_price,
        shares=shares,
        entry_value=entry_value,
        exit_value=exit_value,
        gross_pnl=gross_pnl,
        entry_costs=entry_costs,
        exit_costs=exit_costs,
        costs=costs,
        net_pnl=net_pnl,
        return_pct=_pct(net_pnl / entry_value) if entry_value else 0.0,
        holding_days=candidate.holding_days,
    )


def _fit_shares_to_cash(
    candidate: _TradeCandidate,
    shares: Decimal,
    cash: Decimal,
    transaction_cost_bps: Decimal,
    fee_multiplier: Decimal,
) -> Decimal:
    for _ in range(8):
        if shares <= 0:
            return Decimal("0")
        entry_costs, _ = _trade_cost_breakdown(
            candidate,
            shares,
            transaction_cost_bps,
            fee_multiplier,
        )
        if candidate.entry_price * shares + entry_costs <= cash:
            return shares
        affordable_value = max(cash - entry_costs, Decimal("0"))
        adjusted = _shares(
            affordable_value / candidate.entry_price,
            candidate.signal.instrument_id,
            execution_rule=candidate.execution_rule,
        )
        adjusted = _round_for_exit_rule(
            min(shares, adjusted),
            candidate.exit_execution_rule,
        )
        if adjusted >= shares:
            adjusted = _shares(
                shares - _share_step(candidate),
                candidate.signal.instrument_id,
                execution_rule=candidate.execution_rule,
            )
            adjusted = _round_for_exit_rule(
                adjusted,
                candidate.exit_execution_rule,
            )
        shares = adjusted
    return Decimal("0")


def _round_for_exit_rule(
    shares: Decimal,
    exit_rule: HistoricalExecutionRule | None,
) -> Decimal:
    if exit_rule is None:
        return shares
    return min(shares, round_order_quantity(shares, exit_rule))


def _share_step(candidate: _TradeCandidate) -> Decimal:
    if candidate.execution_rule is not None:
        return Decimal(candidate.execution_rule.quantity_step)
    if _is_cn(candidate.signal.instrument_id):
        return Decimal("100")
    return Decimal("0.0001")


def _open_market_value(
    positions: list[_OpenPortfolioPosition],
    latest_prices: dict[str, Decimal],
) -> Decimal:
    return sum(
        (
            position.trade.shares
            * latest_prices.get(
                position.trade.instrument_id,
                position.trade.entry_price,
            )
            for position in positions
        ),
        Decimal("0"),
    )


def _build_summary(
    provider_name: str,
    instrument_ids: list[str],
    start: date,
    end: date,
    initial_capital: Decimal,
    trades: list[PortfolioBacktestTrade],
    equity_curve: list[PortfolioEquityPoint],
) -> PortfolioBacktestSummary:
    final_equity = equity_curve[-1].equity if equity_curve else initial_capital
    wins = [trade.net_pnl for trade in trades if trade.net_pnl > 0]
    losses = [trade.net_pnl for trade in trades if trade.net_pnl < 0]
    returns = [trade.return_pct for trade in trades]
    period_days = max((end - start).days, 1)
    invested_days = sum(trade.holding_days for trade in trades)
    exposure_denominator = period_days * max(len(instrument_ids), 1)
    return PortfolioBacktestSummary(
        provider=provider_name,
        symbols=instrument_ids,
        start=start,
        end=end,
        initial_capital=initial_capital,
        final_equity=_money(final_equity),
        total_return_pct=_pct((final_equity - initial_capital) / initial_capital),
        max_drawdown_pct=min((point.drawdown_pct for point in equity_curve), default=0.0),
        trade_count=len(trades),
        win_rate=_ratio(len(wins), len(trades)),
        profit_factor=_profit_factor(wins, losses),
        avg_trade_return_pct=round(sum(returns) / len(returns), 4) if returns else None,
        exposure_pct=round((invested_days / exposure_denominator) * 100, 4)
        if exposure_denominator
        else None,
    )


def _build_monthly_returns(
    equity_curve: list[PortfolioEquityPoint],
) -> list[PortfolioMonthlyReturn]:
    if not equity_curve:
        return []
    ordered = sorted(equity_curve, key=lambda point: point.date)
    grouped: dict[str, list[PortfolioEquityPoint]] = {}
    for point in ordered:
        grouped.setdefault(point.date.strftime("%Y-%m"), []).append(point)

    monthly_returns: list[PortfolioMonthlyReturn] = []
    previous_equity = ordered[0].equity
    for month in sorted(grouped):
        points = grouped[month]
        starting_equity = previous_equity
        ending_equity = points[-1].equity
        return_pct = (
            _pct((ending_equity - starting_equity) / starting_equity) if starting_equity else 0.0
        )
        monthly_returns.append(
            PortfolioMonthlyReturn(
                month=month,
                starting_equity=_money(starting_equity),
                ending_equity=_money(ending_equity),
                return_pct=return_pct,
            )
        )
        previous_equity = ending_equity
    return monthly_returns


def _profit_factor(wins: list[Decimal], losses: list[Decimal]) -> float | None:
    if not wins and not losses:
        return None
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = abs(sum(losses, Decimal("0")))
    if gross_loss == 0:
        return None
    return round(float(gross_profit / gross_loss), 4)


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _pct(value: Decimal) -> float:
    return round(float(value * Decimal("100")), 4)


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _shares(
    value: Decimal,
    instrument_id: str | None = None,
    *,
    execution_rule: HistoricalExecutionRule | None = None,
) -> Decimal:
    if execution_rule is not None:
        return round_order_quantity(value, execution_rule)
    if instrument_id and _is_cn(instrument_id):
        lots = (value / Decimal("100")).to_integral_value(rounding=ROUND_DOWN)
        return lots * Decimal("100")
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _resolve_execution_rule(
    resolver: VersionedAshareExecutionResolver | None,
    instrument_id: str,
    row,
) -> HistoricalExecutionRule | None:
    if resolver is None or not _is_cn(instrument_id):
        return None
    return resolver.resolve(
        instrument_id,
        row["trade_date"],
        is_st=_row_is_st(row),
    )


def _execution_rules_for_row(
    instrument_id: str,
    trade_date: date,
    historical_rule: HistoricalExecutionRule | None,
    *,
    side: OrderSide,
    slippage_bps: Decimal,
) -> AShareExecutionRules:
    if historical_rule is not None:
        rules = execution_rules_from_historical(
            historical_rule,
            side=side,
            slippage_bps=slippage_bps,
        )
        effective_limit = _effective_limit_pct(historical_rule, trade_date)
        return rules.model_copy(
            update={
                "price_limit_rate": (
                    effective_limit / Decimal("100") if effective_limit is not None else None
                )
            }
        )

    is_cn = _is_cn(instrument_id)
    return AShareExecutionRules(
        rules_version=("portfolio-cn-legacy-v1" if is_cn else "portfolio-generic-v1"),
        fee_schedule_version="portfolio-cost-model-v1",
        tick_size=Decimal("0.01") if is_cn else Decimal("0.0001"),
        lot_size=100 if is_cn else 1,
        settlement_days=1 if is_cn else 0,
        price_limit_rate=(_limit_pct(instrument_id) / Decimal("100") if is_cn else None),
        volume_participation_rate=Decimal("1"),
        commission_bps=Decimal("0"),
        minimum_commission=Decimal("0"),
        stamp_duty_bps=Decimal("0"),
        transfer_fee_bps=Decimal("0"),
        slippage_bps=slippage_bps,
    )


def _execution_probe_quantity(
    historical_rule: HistoricalExecutionRule | None,
    rules: AShareExecutionRules,
) -> int:
    minimum = (
        historical_rule.minimum_order_quantity if historical_rule is not None else rules.lot_size
    )
    remainder = minimum % rules.lot_size
    return minimum if remainder == 0 else minimum + rules.lot_size - remainder


def _row_volume(row) -> int:
    value = row.get("volume", 0)
    if value is None or pd.isna(value):
        return 0
    return max(int(Decimal(str(value)).to_integral_value(rounding=ROUND_DOWN)), 0)


def _row_has_trades(row) -> bool:
    if _row_volume(row) <= 0:
        return False
    if _truthy(row.get("suspended", False)) or _truthy(row.get("is_suspended", False)):
        return False
    status = row.get("trading_status", "trading")
    if status is None or pd.isna(status):
        return True
    return str(status).strip().lower() in {"", "trading", "normal", "active"}


def _truthy(value) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes"}


def _previous_row(ordered: pd.DataFrame, row) -> object | None:
    matches = ordered.index[ordered["trade_date"] == row["trade_date"]].tolist()
    if not matches:
        return None
    index = matches[0]
    return ordered.iloc[index - 1] if index > 0 else None


def _is_cn(instrument_id: str) -> bool:
    return instrument_id.startswith("CN:")


def _is_limit_up_day(
    row,
    previous,
    instrument_id: str,
    *,
    limit_pct: Decimal | None = None,
) -> bool:
    limit_pct = limit_pct if limit_pct is not None else _limit_pct(instrument_id)
    return _is_limit_day(row, previous, limit_pct, up=True)


def _is_limit_down_day(
    row,
    previous,
    instrument_id: str,
    *,
    limit_pct: Decimal | None = None,
) -> bool:
    limit_pct = limit_pct if limit_pct is not None else _limit_pct(instrument_id)
    return _is_limit_day(row, previous, limit_pct, up=False)


def _is_limit_day(row, previous, limit_pct: Decimal, *, up: bool) -> bool:
    if previous is None:
        return False
    previous_close = Decimal(str(previous["close"]))
    if previous_close <= 0:
        return False
    close = Decimal(str(row["close"]))
    change_pct = (close / previous_close - Decimal("1")) * Decimal("100")
    if up:
        limit_price = previous_close * (Decimal("1") + limit_pct / Decimal("100"))
        return change_pct >= limit_pct - Decimal("0.2") or close >= limit_price * Decimal("0.995")
    limit_price = previous_close * (Decimal("1") - limit_pct / Decimal("100"))
    return change_pct <= -limit_pct + Decimal("0.2") or close <= limit_price * Decimal("1.005")


def _limit_pct(instrument_id: str) -> Decimal:
    symbol = instrument_id.split(":", 1)[1]
    if symbol.startswith(("688", "300", "301")):
        return Decimal("20")
    if symbol.startswith(("4", "8", "920")):
        return Decimal("30")
    return Decimal("10")


def _row_is_st(row) -> bool:
    value = row.get("is_st", False)
    return value is True or str(value).strip().lower() in {"1", "true", "yes"}


def _effective_limit_pct(rule: HistoricalExecutionRule, trade_date: date) -> Decimal | None:
    if rule.listing_date is None or rule.ipo_no_limit_sessions <= 0:
        return rule.limit_pct
    if trade_date < rule.listing_date:
        return rule.limit_pct
    if (trade_date - rule.listing_date).days > 30:
        return rule.limit_pct
    sessions = trading_sessions_in_range(rule.listing_date, trade_date)
    if 0 < len(sessions) <= rule.ipo_no_limit_sessions:
        return None
    return rule.limit_pct
