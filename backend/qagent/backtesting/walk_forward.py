from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from pydantic import BaseModel, Field

from qagent.backtesting.engine import BacktestSignal
from qagent.backtesting.experiment import (
    WalkForwardExperimentManifest,
    build_walk_forward_experiment_manifest,
)
from qagent.backtesting.execution import VersionedAshareExecutionResolver
from qagent.backtesting.portfolio import (
    PortfolioBacktestResult,
    run_signal_portfolio_backtest,
)
from qagent.backtesting.replay_provider import (
    ReplayMarketDataProvider,
    ReplayStrategyDataProvider,
)
from qagent.backtesting.temporal_validation import (
    TemporalValidationResult,
    build_temporal_validation,
)
from qagent.jobs.daily_scan import run_daily_scan
from qagent.market.astock_enhanced import EmptyAShareEnhancedDataProvider
from qagent.market.calendars import trading_sessions_in_range
from qagent.historical_evidence.providers import REQUIRED_BENCHMARK_IDS
from qagent.storage.replay_evidence import ReplayEvidenceRepository


EXCLUDED_STATUSES = frozenset(
    {"risk_elevated", "invalidated", "closed", "postmortem_done"}
)
ELIGIBLE_UNIVERSE_BENCHMARK_ID = "CN:EQUAL_WEIGHT_ELIGIBLE"


class WalkForwardSelection(BaseModel):
    instrument_id: str
    status: str
    primary_strategy_id: str | None
    rank_score: Decimal
    trigger_price: Decimal | None
    initial_stop: Decimal | None
    target_1: Decimal | None


class WalkForwardSnapshot(BaseModel):
    decision_date: date
    historical_universe_size: int
    eligible_size: int
    suspended_count: int
    st_excluded_count: int
    missing_tradability_count: int
    top_5: list[WalkForwardSelection] = Field(default_factory=list)
    top_10: list[WalkForwardSelection] = Field(default_factory=list)


class WalkForwardSelectionResult(BaseModel):
    owner_run_id: str
    provider_mode: str
    dataset_revision: int
    start_date: date
    end_date: date
    rebalance_step_sessions: int
    snapshots: list[WalkForwardSnapshot]
    top_5_portfolio: PortfolioBacktestResult
    top_10_portfolio: PortfolioBacktestResult
    top_5_metrics: "WalkForwardPortfolioMetrics"
    top_10_metrics: "WalkForwardPortfolioMetrics"
    top_5_temporal_validation: TemporalValidationResult
    top_10_temporal_validation: TemporalValidationResult
    benchmarks: list["WalkForwardBenchmarkComparison"]
    cost_sensitivity: list["WalkForwardCostScenario"]
    experiment_manifest: WalkForwardExperimentManifest
    reproducibility_digest: str
    data_health: dict[str, str] = Field(default_factory=dict)


class WalkForwardPortfolioMetrics(BaseModel):
    trade_count: int
    total_return_pct: float
    annualized_return_pct: float | None
    max_drawdown_pct: float
    win_rate: float | None
    avg_trade_return_pct: float | None
    trade_return_sharpe: float | None
    turnover_pct: float
    max_consecutive_losses: int
    total_costs: Decimal


class WalkForwardBenchmarkComparison(BaseModel):
    benchmark_id: str
    status: str
    benchmark_return_pct: float | None
    top_5_excess_return_pct: float | None
    top_10_excess_return_pct: float | None


class WalkForwardCostScenario(BaseModel):
    key: str
    label: str
    slippage_bps: Decimal
    fee_multiplier: Decimal
    top_5_return_pct: float
    top_10_return_pct: float
    top_5_max_drawdown_pct: float
    top_10_max_drawdown_pct: float
    top_5_total_costs: Decimal
    top_10_total_costs: Decimal


class WalkForwardProgress(BaseModel):
    phase: str
    processed_snapshots: int
    total_snapshots: int
    current_date: date | None = None
    snapshot: WalkForwardSnapshot | None = None


def run_full_market_walk_forward_selection(
    repository: ReplayEvidenceRepository,
    *,
    owner_run_id: str,
    start: date,
    end: date,
    rebalance_step_sessions: int = 5,
    lookback_days: int = 400,
    experiment_manifest: WalkForwardExperimentManifest | None = None,
    resume_snapshots: list[WalkForwardSnapshot] | None = None,
    progress_callback: Callable[[WalkForwardProgress], None] | None = None,
) -> WalkForwardSelectionResult:
    if start > end:
        raise ValueError("start must be on or before end")
    if rebalance_step_sessions <= 0:
        raise ValueError("rebalance_step_sessions must be positive")
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    revision = repository.current_revision()
    if revision <= 0:
        raise ValueError("historical replay dataset is empty")
    experiment_manifest = experiment_manifest or build_walk_forward_experiment_manifest(
        provider_mode=repository.provider_mode,
        dataset_revision=revision,
        start_date=start,
        end_date=end,
        rebalance_step_sessions=rebalance_step_sessions,
        lookback_days=lookback_days,
    )
    if experiment_manifest.dataset_revision != revision:
        raise RuntimeError("experiment dataset revision no longer matches replay data")
    owner_repository = ReplayEvidenceRepository(
        repository.session_factory,
        repository.provider_mode,
        owner_run_id=owner_run_id,
    )
    lease = owner_repository.acquire_dataset_lease()
    if lease.revision != revision:
        owner_repository.release_dataset_lease()
        raise RuntimeError("dataset revision changed while acquiring replay lease")
    market_provider = ReplayMarketDataProvider(owner_repository, revision)
    strategy_provider = ReplayStrategyDataProvider(owner_repository, revision)
    snapshots: list[WalkForwardSnapshot] = []
    eligible_universes: list[tuple[date, list[str]]] = []
    scan_errors: list[str] = []
    try:
        sessions = trading_sessions_in_range(start, end)[::rebalance_step_sessions]
        resumed = {item.decision_date: item for item in (resume_snapshots or [])}
        unexpected_dates = set(resumed).difference(sessions)
        if unexpected_dates:
            raise ValueError("resume snapshots do not match the requested validation window")
        _report_progress(
            progress_callback,
            phase="historical_replay",
            processed_snapshots=0,
            total_snapshots=len(sessions),
        )
        for index, decision_date in enumerate(sessions, start=1):
            owner_repository.renew_dataset_lease()
            members = owner_repository.universe_members_on(decision_date, revision)
            if not members:
                members = owner_repository.materialize_universe(
                    decision_date,
                    revision,
                ).members
            instrument_ids = [item.instrument_id for item in members if item.active]
            tradability = owner_repository.tradability_on(
                instrument_ids,
                decision_date,
                revision,
            )
            eligible = []
            suspended_count = 0
            st_excluded_count = 0
            missing_tradability_count = 0
            for instrument_id in instrument_ids:
                point = tradability.get(instrument_id)
                if point is None:
                    missing_tradability_count += 1
                    continue
                if point.trading_status != "trading":
                    suspended_count += 1
                    continue
                if point.is_st is True:
                    st_excluded_count += 1
                    continue
                eligible.append(instrument_id)
            eligible_universes.append((decision_date, sorted(eligible)))
            snapshot = resumed.get(decision_date)
            if snapshot is None:
                scan = run_daily_scan(
                    eligible,
                    market_provider,
                    mode="historical_replay",
                    strategy_data_provider=strategy_provider,
                    a_share_enhanced_provider=EmptyAShareEnhancedDataProvider(),
                    start=decision_date - timedelta(days=lookback_days),
                    end=decision_date,
                )
                scan_errors.extend(
                    item.reason for item in scan.items if item.status == "error"
                )
                selections = [
                    _selection(card)
                    for card in scan.cards
                    if card.status.value not in EXCLUDED_STATUSES
                ]
                snapshot = WalkForwardSnapshot(
                    decision_date=decision_date,
                    historical_universe_size=len(instrument_ids),
                    eligible_size=len(eligible),
                    suspended_count=suspended_count,
                    st_excluded_count=st_excluded_count,
                    missing_tradability_count=missing_tradability_count,
                    top_5=selections[:5],
                    top_10=selections[:10],
                )
            snapshots.append(snapshot)
            _report_progress(
                progress_callback,
                phase="historical_replay",
                processed_snapshots=index,
                total_snapshots=len(sessions),
                current_date=decision_date,
                snapshot=snapshot,
            )
        _report_progress(
            progress_callback,
            phase="portfolio_simulation",
            processed_snapshots=len(snapshots),
            total_snapshots=len(sessions),
        )
        execution_resolver = VersionedAshareExecutionResolver(
            owner_repository,
            dataset_revision=revision,
        )
        top_5_signals = _signals(snapshots, size=5)
        top_10_signals = _signals(snapshots, size=10)
        top_5_portfolio = run_signal_portfolio_backtest(
            signals=top_5_signals,
            instrument_ids=sorted(
                {item.instrument_id for item in top_5_signals}
            ),
            provider=market_provider,
            start=start,
            end=end,
            max_positions=5,
            execution_rule_resolver=execution_resolver,
        )
        top_10_portfolio = run_signal_portfolio_backtest(
            signals=top_10_signals,
            instrument_ids=sorted(
                {item.instrument_id for item in top_10_signals}
            ),
            provider=market_provider,
            start=start,
            end=end,
            max_positions=10,
            execution_rule_resolver=execution_resolver,
        )
        top_5_metrics = _portfolio_metrics(top_5_portfolio, start, end)
        top_10_metrics = _portfolio_metrics(top_10_portfolio, start, end)
        top_5_temporal_validation = _trade_temporal_validation(
            top_5_portfolio.trades
        )
        top_10_temporal_validation = _trade_temporal_validation(
            top_10_portfolio.trades
        )
        _report_progress(
            progress_callback,
            phase="validation_and_benchmarks",
            processed_snapshots=len(snapshots),
            total_snapshots=len(sessions),
        )
        cost_sensitivity = _cost_sensitivity(
            top_5_signals=top_5_signals,
            top_10_signals=top_10_signals,
            top_5_portfolio=top_5_portfolio,
            top_10_portfolio=top_10_portfolio,
            market_provider=market_provider,
            execution_resolver=execution_resolver,
            start=start,
            end=end,
        )
        benchmarks = _benchmark_comparisons(
            market_provider,
            start=start,
            end=end,
            top_5_return=top_5_metrics.total_return_pct,
            top_10_return=top_10_metrics.total_return_pct,
            eligible_universes=eligible_universes,
        )
    finally:
        owner_repository.release_dataset_lease()
    digest = _selection_digest(
        snapshots,
        revision,
        top_5_portfolio,
        top_10_portfolio,
        benchmarks,
        cost_sensitivity,
    )
    result = WalkForwardSelectionResult(
        owner_run_id=owner_run_id,
        provider_mode=repository.provider_mode,
        dataset_revision=revision,
        start_date=start,
        end_date=end,
        rebalance_step_sessions=rebalance_step_sessions,
        snapshots=snapshots,
        top_5_portfolio=top_5_portfolio,
        top_10_portfolio=top_10_portfolio,
        top_5_metrics=top_5_metrics,
        top_10_metrics=top_10_metrics,
        top_5_temporal_validation=top_5_temporal_validation,
        top_10_temporal_validation=top_10_temporal_validation,
        benchmarks=benchmarks,
        cost_sensitivity=cost_sensitivity,
        experiment_manifest=experiment_manifest,
        reproducibility_digest=digest,
        data_health={
            "walk_forward_revision": str(revision),
            "walk_forward_snapshots": str(len(snapshots)),
            "walk_forward_lookback_days": str(lookback_days),
            "walk_forward_scan_errors": str(len(scan_errors)),
            "walk_forward_future_data_guard": "revision_lease_and_decision_date_cutoff",
            "walk_forward_universe": "historical_lifecycle_per_rebalance_date",
            "walk_forward_st_policy": "excluded",
            "walk_forward_top_5_trades": str(
                top_5_portfolio.summary.trade_count
            ),
            "walk_forward_top_10_trades": str(
                top_10_portfolio.summary.trade_count
            ),
            "walk_forward_oos_minimum_trades": "30",
            "walk_forward_top_5_oos_trades": str(
                _oos_sample_count(top_5_temporal_validation)
            ),
            "walk_forward_top_10_oos_trades": str(
                _oos_sample_count(top_10_temporal_validation)
            ),
            "walk_forward_top_5_oos_gate": _oos_gate(
                top_5_temporal_validation
            ),
            "walk_forward_top_10_oos_gate": _oos_gate(
                top_10_temporal_validation
            ),
            "walk_forward_benchmarks_ready": (
                f"{sum(item.status == 'ready' for item in benchmarks)}/"
                f"{len(benchmarks)}"
            ),
            "walk_forward_equal_weight_benchmark": next(
                item.status
                for item in benchmarks
                if item.benchmark_id == ELIGIBLE_UNIVERSE_BENCHMARK_ID
            ),
            "walk_forward_cost_scenarios": str(len(cost_sensitivity)),
            "walk_forward_stress_top_5_return_pct": str(
                cost_sensitivity[-1].top_5_return_pct
            ),
            "walk_forward_stress_top_10_return_pct": str(
                cost_sensitivity[-1].top_10_return_pct
            ),
            "walk_forward_digest": digest,
            "walk_forward_experiment_digest": experiment_manifest.experiment_digest,
            "walk_forward_code_revision": experiment_manifest.code_revision,
            "walk_forward_strategy_registry_digest": (
                experiment_manifest.strategy_registry_digest
            ),
            **(
                {"walk_forward_error_samples": " | ".join(scan_errors[:3])}
                if scan_errors
                else {}
            ),
        },
    )
    _report_progress(
        progress_callback,
        phase="completed",
        processed_snapshots=len(snapshots),
        total_snapshots=len(snapshots),
    )
    return result


def _report_progress(
    callback: Callable[[WalkForwardProgress], None] | None,
    *,
    phase: str,
    processed_snapshots: int,
    total_snapshots: int,
    current_date: date | None = None,
    snapshot: WalkForwardSnapshot | None = None,
) -> None:
    if callback is None:
        return
    callback(
        WalkForwardProgress(
            phase=phase,
            processed_snapshots=processed_snapshots,
            total_snapshots=total_snapshots,
            current_date=current_date,
            snapshot=snapshot,
        )
    )


def _selection(card) -> WalkForwardSelection:
    return WalkForwardSelection(
        instrument_id=card.instrument_id,
        status=card.status.value,
        primary_strategy_id=card.primary_strategy_id,
        rank_score=Decimal(str(card.rank_score)),
        trigger_price=card.entry_plan.trigger_price,
        initial_stop=card.exit_plan.initial_stop,
        target_1=card.exit_plan.target_1,
    )


def _signals(
    snapshots: list[WalkForwardSnapshot], *, size: int
) -> list[BacktestSignal]:
    result = []
    for snapshot in snapshots:
        selections = snapshot.top_5 if size == 5 else snapshot.top_10
        result.extend(
            BacktestSignal(
                snapshot_id=(
                    f"walk-forward-{size}-{snapshot.decision_date:%Y%m%d}:"
                    f"{item.instrument_id}"
                ),
                instrument_id=item.instrument_id,
                signal_date=snapshot.decision_date,
                primary_strategy_id=item.primary_strategy_id,
                status=item.status,
                rank_score=item.rank_score,
                trigger_price=item.trigger_price,
                initial_stop=item.initial_stop,
                target_1=item.target_1,
                outcome_status="pending",
            )
            for item in selections
        )
    return result


def _selection_digest(
    snapshots: list[WalkForwardSnapshot],
    revision: int,
    top_5_portfolio: PortfolioBacktestResult,
    top_10_portfolio: PortfolioBacktestResult,
    benchmarks: list[WalkForwardBenchmarkComparison],
    cost_sensitivity: list[WalkForwardCostScenario],
) -> str:
    payload = {
        "dataset_revision": revision,
        "snapshots": [item.model_dump(mode="json") for item in snapshots],
        "top_5_portfolio": top_5_portfolio.model_dump(mode="json"),
        "top_10_portfolio": top_10_portfolio.model_dump(mode="json"),
        "top_5_temporal_validation": _trade_temporal_validation(
            top_5_portfolio.trades
        ).model_dump(mode="json"),
        "top_10_temporal_validation": _trade_temporal_validation(
            top_10_portfolio.trades
        ).model_dump(mode="json"),
        "benchmarks": [item.model_dump(mode="json") for item in benchmarks],
        "cost_sensitivity": [
            item.model_dump(mode="json") for item in cost_sensitivity
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _trade_temporal_validation(trades) -> TemporalValidationResult:
    rows = [
        SimpleNamespace(
            signal_date=trade.signal_date,
            return_20d=trade.return_pct,
        )
        for trade in trades
    ]
    return build_temporal_validation(
        rows,
        return_horizon_days=20,
        embargo_days=20,
        bootstrap_samples=1000,
        seed=42,
    )


def _cost_sensitivity(
    *,
    top_5_signals: list[BacktestSignal],
    top_10_signals: list[BacktestSignal],
    top_5_portfolio: PortfolioBacktestResult,
    top_10_portfolio: PortfolioBacktestResult,
    market_provider: ReplayMarketDataProvider,
    execution_resolver: VersionedAshareExecutionResolver,
    start: date,
    end: date,
) -> list[WalkForwardCostScenario]:
    scenarios = [
        ("base", "基准成本", Decimal("5"), Decimal("1")),
        ("elevated", "较高成本", Decimal("10"), Decimal("1.5")),
        ("stress", "压力成本", Decimal("20"), Decimal("2")),
    ]
    results: list[WalkForwardCostScenario] = []
    for key, label, slippage_bps, fee_multiplier in scenarios:
        if key == "base":
            top_5 = top_5_portfolio
            top_10 = top_10_portfolio
        else:
            top_5 = run_signal_portfolio_backtest(
                signals=top_5_signals,
                instrument_ids=sorted(
                    {item.instrument_id for item in top_5_signals}
                ),
                provider=market_provider,
                start=start,
                end=end,
                max_positions=5,
                slippage_bps=slippage_bps,
                fee_multiplier=fee_multiplier,
                execution_rule_resolver=execution_resolver,
            )
            top_10 = run_signal_portfolio_backtest(
                signals=top_10_signals,
                instrument_ids=sorted(
                    {item.instrument_id for item in top_10_signals}
                ),
                provider=market_provider,
                start=start,
                end=end,
                max_positions=10,
                slippage_bps=slippage_bps,
                fee_multiplier=fee_multiplier,
                execution_rule_resolver=execution_resolver,
            )
        results.append(
            WalkForwardCostScenario(
                key=key,
                label=label,
                slippage_bps=slippage_bps,
                fee_multiplier=fee_multiplier,
                top_5_return_pct=top_5.summary.total_return_pct,
                top_10_return_pct=top_10.summary.total_return_pct,
                top_5_max_drawdown_pct=top_5.summary.max_drawdown_pct,
                top_10_max_drawdown_pct=top_10.summary.max_drawdown_pct,
                top_5_total_costs=sum(
                    (item.costs for item in top_5.trades), Decimal("0")
                ),
                top_10_total_costs=sum(
                    (item.costs for item in top_10.trades), Decimal("0")
                ),
            )
        )
    return results


def _oos_sample_count(validation: TemporalValidationResult) -> int:
    return validation.out_of_sample.sample_count if validation.out_of_sample else 0


def _oos_gate(validation: TemporalValidationResult) -> str:
    return "ready" if _oos_sample_count(validation) >= 30 else "insufficient"


def _portfolio_metrics(
    portfolio: PortfolioBacktestResult,
    start: date,
    end: date,
) -> WalkForwardPortfolioMetrics:
    trades = portfolio.trades
    returns = [item.return_pct for item in trades]
    annualized = None
    elapsed_days = max((end - start).days, 0)
    if elapsed_days > 0 and portfolio.summary.initial_capital > 0:
        ratio = portfolio.summary.final_equity / portfolio.summary.initial_capital
        if ratio > 0:
            annualized = round((float(ratio) ** (365 / elapsed_days) - 1) * 100, 4)
    sharpe = None
    if len(returns) >= 2:
        deviation = statistics.stdev(returns)
        if deviation > 0:
            sharpe = round(statistics.mean(returns) / deviation * math.sqrt(len(returns)), 4)
    turnover = sum(
        float(item.entry_price * item.shares) for item in trades
    ) / float(portfolio.summary.initial_capital) * 100
    return WalkForwardPortfolioMetrics(
        trade_count=len(trades),
        total_return_pct=portfolio.summary.total_return_pct,
        annualized_return_pct=annualized,
        max_drawdown_pct=portfolio.summary.max_drawdown_pct,
        win_rate=portfolio.summary.win_rate,
        avg_trade_return_pct=portfolio.summary.avg_trade_return_pct,
        trade_return_sharpe=sharpe,
        turnover_pct=round(turnover, 4),
        max_consecutive_losses=_max_consecutive_losses(returns),
        total_costs=sum((item.costs for item in trades), Decimal("0")),
    )


def _max_consecutive_losses(returns: list[float]) -> int:
    maximum = 0
    current = 0
    for value in returns:
        current = current + 1 if value < 0 else 0
        maximum = max(maximum, current)
    return maximum


def _benchmark_comparisons(
    provider: ReplayMarketDataProvider,
    *,
    start: date,
    end: date,
    top_5_return: float,
    top_10_return: float,
    eligible_universes: list[tuple[date, list[str]]],
) -> list[WalkForwardBenchmarkComparison]:
    bars = provider.get_daily_bars(list(REQUIRED_BENCHMARK_IDS), start, end)
    comparisons = []
    for benchmark_id in REQUIRED_BENCHMARK_IDS:
        frame = bars.loc[bars["instrument_id"] == benchmark_id].sort_values(
            "trade_date"
        ) if not bars.empty else bars
        if frame.empty:
            comparisons.append(
                WalkForwardBenchmarkComparison(
                    benchmark_id=benchmark_id,
                    status="missing",
                    benchmark_return_pct=None,
                    top_5_excess_return_pct=None,
                    top_10_excess_return_pct=None,
                )
            )
            continue
        first = float(frame.iloc[0]["adjusted_close"])
        last = float(frame.iloc[-1]["adjusted_close"])
        benchmark_return = round((last / first - 1) * 100, 4) if first else None
        comparisons.append(
            WalkForwardBenchmarkComparison(
                benchmark_id=benchmark_id,
                status="ready" if benchmark_return is not None else "missing",
                benchmark_return_pct=benchmark_return,
                top_5_excess_return_pct=(
                    round(top_5_return - benchmark_return, 4)
                    if benchmark_return is not None
                    else None
                ),
                top_10_excess_return_pct=(
                    round(top_10_return - benchmark_return, 4)
                    if benchmark_return is not None
                    else None
                ),
            )
        )
    equal_weight_return = _equal_weight_eligible_return(
        provider,
        eligible_universes,
        end=end,
    )
    comparisons.append(
        WalkForwardBenchmarkComparison(
            benchmark_id=ELIGIBLE_UNIVERSE_BENCHMARK_ID,
            status="ready" if equal_weight_return is not None else "missing",
            benchmark_return_pct=equal_weight_return,
            top_5_excess_return_pct=(
                round(top_5_return - equal_weight_return, 4)
                if equal_weight_return is not None
                else None
            ),
            top_10_excess_return_pct=(
                round(top_10_return - equal_weight_return, 4)
                if equal_weight_return is not None
                else None
            ),
        )
    )
    return comparisons


def _equal_weight_eligible_return(
    provider: ReplayMarketDataProvider,
    eligible_universes: list[tuple[date, list[str]]],
    *,
    end: date,
) -> float | None:
    instrument_ids = sorted(
        {
            instrument_id
            for _, members in eligible_universes
            for instrument_id in members
        }
    )
    if not instrument_ids:
        return None
    first_date = eligible_universes[0][0]
    bars = provider.get_daily_bars(instrument_ids, first_date, end)
    if bars.empty:
        return None
    compounded = 1.0
    completed_periods = 0
    for index, (decision_date, members) in enumerate(eligible_universes):
        period_end = (
            eligible_universes[index + 1][0]
            if index + 1 < len(eligible_universes)
            else end
        )
        if period_end <= decision_date:
            continue
        returns = []
        for instrument_id in members:
            frame = bars.loc[
                bars["instrument_id"].eq(instrument_id)
                & bars["trade_date"].gt(decision_date)
                & bars["trade_date"].le(period_end)
            ].sort_values("trade_date")
            if len(frame) < 2:
                continue
            first = float(frame.iloc[0]["adjusted_close"])
            last = float(frame.iloc[-1]["adjusted_close"])
            if first > 0:
                returns.append(last / first - 1)
        if not returns:
            continue
        compounded *= 1 + statistics.mean(returns)
        completed_periods += 1
    return round((compounded - 1) * 100, 4) if completed_periods else None
