from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
from pydantic import BaseModel

from qagent.jobs.daily_scan import run_daily_scan
from qagent.monitoring.outcomes import (
    OpportunityOutcome,
    StrategyPerformance,
    compute_opportunity_outcome,
    summarize_strategy_performance,
)
from qagent.providers.base import MarketDataProvider
from qagent.storage.repository import OpportunitySnapshotRecord
from qagent.strategy_data.providers import StrategyDataProvider


class BacktestSignal(BaseModel):
    snapshot_id: str
    instrument_id: str
    signal_date: date
    primary_strategy_id: str | None
    status: str
    rank_score: Decimal
    trigger_price: Decimal | None
    initial_stop: Decimal | None
    target_1: Decimal | None
    outcome_status: str
    return_5d: float | None = None
    return_10d: float | None = None
    return_20d: float | None = None
    return_60d: float | None = None
    max_drawdown_pct: float | None = None
    max_runup_pct: float | None = None


class BacktestSummary(BaseModel):
    provider: str
    symbols: list[str]
    start: date
    end: date
    scan_count: int
    evaluated_signals: int
    completed_signals: int
    target_hit_rate: float | None
    positive_rate_10d: float | None
    avg_return_5d: float | None
    avg_return_10d: float | None
    avg_return_20d: float | None
    max_drawdown_pct: float | None
    max_runup_pct: float | None


class BacktestBenchmarkComparison(BaseModel):
    label: str
    benchmark_return_10d: float | None
    strategy_return_10d: float | None
    excess_return_10d: float | None
    verdict: str
    summary: str


class BacktestEnvironmentBreakdown(BaseModel):
    regime: str
    sample_count: int
    completed_count: int
    benchmark_return_10d: float | None
    strategy_return_10d: float | None
    excess_return_10d: float | None
    win_rate_10d: float | None
    max_drawdown_pct: float | None


class BacktestResult(BaseModel):
    summary: BacktestSummary
    performance: list[StrategyPerformance]
    signals: list[BacktestSignal]
    benchmark: BacktestBenchmarkComparison
    environment_breakdown: list[BacktestEnvironmentBreakdown]
    data_health: dict[str, str]


def build_backtest_scan_dates(
    bars: pd.DataFrame,
    start: date,
    end: date,
    step_days: int,
) -> list[date]:
    if step_days <= 0:
        raise ValueError("step_days must be positive")
    if start > end:
        raise ValueError("start must be on or before end")
    if bars.empty:
        return []

    frame = bars.copy()
    if pd.api.types.is_datetime64_any_dtype(frame["trade_date"]):
        frame["trade_date"] = frame["trade_date"].dt.date
    dates = sorted(
        {
            trade_date
            for trade_date in frame["trade_date"].tolist()
            if start <= trade_date <= end
        }
    )
    return dates[::step_days]


def run_historical_backtest(
    instrument_ids: list[str],
    provider: MarketDataProvider,
    start: date,
    end: date,
    step_days: int = 5,
    max_signals: int = 100,
    lookback_days: int = 260,
    outcome_horizons: tuple[int, ...] = (5, 10, 20, 60),
    strategy_data_provider: StrategyDataProvider | None = None,
) -> BacktestResult:
    if start > end:
        raise ValueError("start must be on or before end")
    if step_days <= 0:
        raise ValueError("step_days must be positive")
    if max_signals <= 0:
        raise ValueError("max_signals must be positive")

    scan_seed_bars = provider.get_daily_bars(instrument_ids, start=start, end=end)
    scan_dates = build_backtest_scan_dates(scan_seed_bars, start, end, step_days)
    all_outcome_bars = provider.get_daily_bars(
        instrument_ids,
        start=start - timedelta(days=lookback_days),
        end=end + timedelta(days=max(outcome_horizons) * 3),
    )
    outcomes: list[OpportunityOutcome] = []
    signals: list[BacktestSignal] = []
    scan_cards = 0

    for scan_date in scan_dates:
        scan_result = run_daily_scan(
            instrument_ids=instrument_ids,
            provider=provider,
            mode=provider.name,
            strategy_data_provider=strategy_data_provider,
            start=start - timedelta(days=lookback_days),
            end=scan_date,
        )
        scan_cards += len(scan_result.cards)
        item_by_instrument = {item.instrument_id: item for item in scan_result.items}
        for card in scan_result.cards:
            item = item_by_instrument.get(card.instrument_id)
            if item is None or item.latest_trade_date is None:
                continue
            snapshot = _snapshot_from_card(scan_date, card, item)
            bars = all_outcome_bars.loc[
                all_outcome_bars["instrument_id"] == card.instrument_id
            ].reset_index(drop=True)
            outcome = compute_opportunity_outcome(snapshot, bars, horizons=outcome_horizons)
            outcomes.append(outcome)
            signals.append(_signal_from_outcome(snapshot, outcome))

    performance = summarize_strategy_performance(outcomes)
    summary = _build_summary(provider.name, instrument_ids, start, end, scan_dates, outcomes)
    benchmark_rows = _benchmark_rows(outcomes, all_outcome_bars, instrument_ids)
    benchmark = _benchmark_comparison(summary, benchmark_rows)
    environment_breakdown = _environment_breakdown(benchmark_rows)
    data_health = {
        "provider": provider.name,
        "symbols": str(len(instrument_ids)),
        "scan_dates": str(len(scan_dates)),
        "scan_cards": str(scan_cards),
        "signals": str(len(signals)),
        "lookahead_guard": "bars_limited_to_scan_date",
        "benchmark": "equal_weight_scanned_universe",
        "environment_breakdown": str(len(environment_breakdown)),
    }
    provider_errors = getattr(provider, "last_errors", [])
    if provider_errors:
        data_health["errors"] = " | ".join(provider_errors[:3])

    return BacktestResult(
        summary=summary,
        performance=performance,
        signals=sorted(signals, key=lambda item: item.signal_date, reverse=True)[:max_signals],
        benchmark=benchmark,
        environment_breakdown=environment_breakdown,
        data_health=data_health,
    )


def _snapshot_from_card(scan_date, card, item) -> OpportunitySnapshotRecord:
    latest_close = Decimal(str(item.latest_close)) if item.latest_close is not None else None
    return OpportunitySnapshotRecord(
        snapshot_id=f"backtest-{scan_date:%Y%m%d}:{card.card_id}",
        run_id=f"backtest-{scan_date:%Y%m%d}",
        card_id=card.card_id,
        instrument_id=card.instrument_id,
        market=card.market.value,
        status=card.status.value,
        signal_date=item.latest_trade_date,
        latest_close=latest_close,
        primary_strategy_id=card.primary_strategy_id,
        score=Decimal(str(card.score)),
        strategy_score=Decimal(str(card.strategy_score)),
        rank_score=Decimal(str(card.rank_score)),
        trigger_price=card.entry_plan.trigger_price,
        initial_stop=card.exit_plan.initial_stop,
        target_1=card.exit_plan.target_1,
        card=card.model_dump(mode="json"),
    )


def _signal_from_outcome(
    snapshot: OpportunitySnapshotRecord,
    outcome: OpportunityOutcome,
) -> BacktestSignal:
    if snapshot.signal_date is None:
        raise ValueError("backtest snapshot signal_date cannot be None")
    return BacktestSignal(
        snapshot_id=snapshot.snapshot_id,
        instrument_id=snapshot.instrument_id,
        signal_date=snapshot.signal_date,
        primary_strategy_id=snapshot.primary_strategy_id,
        status=snapshot.status,
        rank_score=snapshot.rank_score,
        trigger_price=snapshot.trigger_price,
        initial_stop=snapshot.initial_stop,
        target_1=snapshot.target_1,
        outcome_status=outcome.outcome_status,
        return_5d=outcome.return_5d,
        return_10d=outcome.return_10d,
        return_20d=outcome.return_20d,
        return_60d=outcome.return_60d,
        max_drawdown_pct=outcome.max_drawdown_pct,
        max_runup_pct=outcome.max_runup_pct,
    )


def _build_summary(
    provider_name: str,
    instrument_ids: list[str],
    start: date,
    end: date,
    scan_dates: list[date],
    outcomes: list[OpportunityOutcome],
) -> BacktestSummary:
    completed = [outcome for outcome in outcomes if outcome.outcome_status != "pending"]
    return_5d = [outcome.return_5d for outcome in completed if outcome.return_5d is not None]
    return_10d = [outcome.return_10d for outcome in completed if outcome.return_10d is not None]
    return_20d = [outcome.return_20d for outcome in completed if outcome.return_20d is not None]
    drawdowns = [
        outcome.max_drawdown_pct
        for outcome in completed
        if outcome.max_drawdown_pct is not None
    ]
    runups = [
        outcome.max_runup_pct for outcome in completed if outcome.max_runup_pct is not None
    ]
    target_hits = sum(1 for outcome in completed if outcome.outcome_status == "target_1_hit")
    return BacktestSummary(
        provider=provider_name,
        symbols=instrument_ids,
        start=start,
        end=end,
        scan_count=len(scan_dates),
        evaluated_signals=len(outcomes),
        completed_signals=len(completed),
        target_hit_rate=_ratio(target_hits, len(completed)),
        positive_rate_10d=_ratio(sum(1 for value in return_10d if value > 0), len(return_10d)),
        avg_return_5d=_average(return_5d),
        avg_return_10d=_average(return_10d),
        avg_return_20d=_average(return_20d),
        max_drawdown_pct=min(drawdowns) if drawdowns else None,
        max_runup_pct=max(runups) if runups else None,
    )


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _benchmark_rows(
    outcomes: list[OpportunityOutcome],
    bars: pd.DataFrame,
    instrument_ids: list[str],
) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    completed = [
        outcome
        for outcome in outcomes
        if outcome.signal_date is not None and outcome.outcome_status != "pending"
    ]
    for outcome in completed:
        benchmark_return = _equal_weight_forward_return(
            bars,
            instrument_ids,
            outcome.signal_date,
            horizon=10,
        )
        if benchmark_return is None:
            benchmark_return = 0.0
        strategy_return = outcome.return_10d if outcome.return_10d is not None else 0.0
        rows.append(
            {
                "regime": _benchmark_regime(benchmark_return),
                "strategy_return_10d": strategy_return,
                "benchmark_return_10d": benchmark_return,
                "excess_return_10d": strategy_return - benchmark_return,
                "max_drawdown_pct": outcome.max_drawdown_pct
                if outcome.max_drawdown_pct is not None
                else 0.0,
            }
        )
    return rows


def _benchmark_comparison(
    summary: BacktestSummary,
    rows: list[dict[str, float | str]],
) -> BacktestBenchmarkComparison:
    benchmark_return = _average(
        [float(row["benchmark_return_10d"]) for row in rows]
    )
    strategy_return = summary.avg_return_10d
    excess_return = (
        round(strategy_return - benchmark_return, 4)
        if strategy_return is not None and benchmark_return is not None
        else None
    )
    verdict = _benchmark_verdict(excess_return, len(rows))
    return BacktestBenchmarkComparison(
        label="Equal-weight scanned universe",
        benchmark_return_10d=benchmark_return,
        strategy_return_10d=strategy_return,
        excess_return_10d=excess_return,
        verdict=verdict,
        summary=_benchmark_summary(verdict, strategy_return, benchmark_return, excess_return),
    )


def _environment_breakdown(
    rows: list[dict[str, float | str]],
) -> list[BacktestEnvironmentBreakdown]:
    result: list[BacktestEnvironmentBreakdown] = []
    for regime in ("up", "range", "down"):
        regime_rows = [row for row in rows if row["regime"] == regime]
        if not regime_rows:
            continue
        strategy_returns = [float(row["strategy_return_10d"]) for row in regime_rows]
        benchmark_returns = [float(row["benchmark_return_10d"]) for row in regime_rows]
        excess_returns = [float(row["excess_return_10d"]) for row in regime_rows]
        drawdowns = [float(row["max_drawdown_pct"]) for row in regime_rows]
        result.append(
            BacktestEnvironmentBreakdown(
                regime=regime,
                sample_count=len(regime_rows),
                completed_count=len(regime_rows),
                benchmark_return_10d=_average(benchmark_returns),
                strategy_return_10d=_average(strategy_returns),
                excess_return_10d=_average(excess_returns),
                win_rate_10d=_ratio(sum(1 for value in strategy_returns if value > 0), len(strategy_returns)),
                max_drawdown_pct=min(drawdowns) if drawdowns else None,
            )
        )
    return result


def _equal_weight_forward_return(
    bars: pd.DataFrame,
    instrument_ids: list[str],
    signal_date: date,
    horizon: int,
) -> float | None:
    returns = [
        value
        for instrument_id in instrument_ids
        if (value := _instrument_forward_return(bars, instrument_id, signal_date, horizon)) is not None
    ]
    return _average(returns)


def _instrument_forward_return(
    bars: pd.DataFrame,
    instrument_id: str,
    signal_date: date,
    horizon: int,
) -> float | None:
    frame = bars[bars["instrument_id"] == instrument_id].copy()
    if frame.empty:
        return None
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
    frame = frame.sort_values("trade_date").reset_index(drop=True)
    eligible = frame.index[frame["trade_date"] >= signal_date].tolist()
    if not eligible:
        return None
    base_index = eligible[0]
    target_index = base_index + horizon
    if target_index >= len(frame):
        return None
    base_close = float(frame.loc[base_index, "close"])
    target_close = float(frame.loc[target_index, "close"])
    if base_close == 0:
        return None
    return round((target_close / base_close - 1) * 100, 4)


def _benchmark_regime(benchmark_return: float) -> str:
    if benchmark_return >= 1:
        return "up"
    if benchmark_return <= -1:
        return "down"
    return "range"


def _benchmark_verdict(excess_return: float | None, sample_count: int) -> str:
    if sample_count < 3 or excess_return is None:
        return "insufficient_sample"
    if excess_return >= 0.5:
        return "outperform"
    if excess_return <= -0.5:
        return "underperform"
    return "inline"


def _benchmark_summary(
    verdict: str,
    strategy_return: float | None,
    benchmark_return: float | None,
    excess_return: float | None,
) -> str:
    if verdict == "insufficient_sample":
        return "Benchmark comparison has too few completed samples."
    return (
        f"Strategy 10D avg {strategy_return:.2f}% vs equal-weight universe "
        f"{benchmark_return:.2f}%; excess {excess_return:+.2f}%."
    )
