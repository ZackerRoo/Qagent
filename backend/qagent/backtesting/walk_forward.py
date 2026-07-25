from __future__ import annotations

import gc
import hashlib
import json
import math
import os
import statistics
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from decimal import Decimal
from multiprocessing import get_context
from threading import Event, Lock, Thread
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
from qagent.backtesting.reranking import (
    DYNAMIC_RERANKER_VERSION,
    MIN_RERANK_TRAINING_SAMPLES,
    RerankCandidate,
    RerankCandidateScore,
    ResolvedRerankObservation,
    rerank_candidates,
)
from qagent.backtesting.statistical_validation import (
    benjamini_hochberg,
    clustered_return_inference,
)
from qagent.backtesting.temporal_validation import (
    TemporalValidationResult,
    build_temporal_validation,
)
from qagent.factors.engine import build_factor_rankings
from qagent.factors.models import FactorRanking
from qagent.jobs.daily_scan import run_daily_scan
from qagent.market.astock_enhanced import EmptyAShareEnhancedDataProvider
from qagent.market.calendars import trading_sessions_in_range
from qagent.historical_evidence.providers import REQUIRED_BENCHMARK_IDS
from qagent.market.benchmark_trend import (
    BenchmarkTrendState,
    build_benchmark_trend_snapshot,
)
from qagent.recommendations.selection import select_strategy_diversified
from qagent.storage.replay_evidence import (
    LEASE_DURATION,
    DatasetLeaseBusy,
    ReplayEvidenceRepository,
    ReplayEvidenceUnavailable,
    StaleCheckpointRevision,
)


EXCLUDED_STATUSES = frozenset({"risk_elevated", "invalidated", "closed", "postmortem_done"})
ELIGIBLE_UNIVERSE_BENCHMARK_ID = "CN:EQUAL_WEIGHT_ELIGIBLE"
MIN_FULL_MARKET_COVERAGE_RATIO = 0.90
MIN_FUNDAMENTAL_COVERAGE_RATIO = 0.80
PREFILTER_LOOKBACK_DAYS = 220
PREFILTER_CANDIDATE_LIMIT = 300
PREFILTER_NON_STOCK_RESERVE_RATIO = 0.20


class _DatasetLeaseHeartbeat:
    def __init__(
        self,
        repository: ReplayEvidenceRepository,
        *,
        expected_revision: int,
        interval_seconds: float | None = None,
        retry_interval_seconds: float = 1.0,
        max_attempts: int = 3,
        maintenance_callback: Callable[[int, int, datetime], None] | None = None,
        initial_maintenance_count: int = 0,
        initial_recovery_count: int = 0,
        initial_heartbeat_at: datetime | None = None,
    ):
        self.repository = repository
        self.expected_revision = expected_revision
        self.interval_seconds = interval_seconds or max(
            1.0,
            LEASE_DURATION.total_seconds() / 3,
        )
        self.retry_interval_seconds = max(0.01, retry_interval_seconds)
        self.max_attempts = max(1, max_attempts)
        self.maintenance_callback = maintenance_callback
        self._stop = Event()
        self._maintenance_lock = Lock()
        self._failure: Exception | None = None
        self.maintenance_count = max(0, initial_maintenance_count)
        self.recovery_count = max(0, initial_recovery_count)
        self.last_heartbeat_at = initial_heartbeat_at
        self._thread = Thread(
            target=self._run,
            name=f"dataset-lease-{repository.provider_mode}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self.interval_seconds + 1.0))

    def raise_if_failed(self) -> None:
        if self._failure is not None:
            raise RuntimeError(
                f"dataset lease heartbeat failed: {self._failure}"
            ) from self._failure

    def maintain_now(self) -> None:
        telemetry: tuple[int, int, datetime] | None = None
        with self._maintenance_lock:
            self.raise_if_failed()
            for attempt in range(1, self.max_attempts + 1):
                try:
                    maintained = self.repository.maintain_dataset_lease(
                        expected_revision=self.expected_revision,
                    )
                except (DatasetLeaseBusy, StaleCheckpointRevision) as exc:
                    self._failure = exc
                    self.raise_if_failed()
                except Exception as exc:
                    if attempt >= self.max_attempts:
                        self._failure = exc
                        self.raise_if_failed()
                    if self._stop.wait(self.retry_interval_seconds):
                        return
                else:
                    self.maintenance_count += 1
                    if maintained.action != "renewed":
                        self.recovery_count += 1
                    self.last_heartbeat_at = maintained.lease.heartbeat_at
                    telemetry = (
                        self.maintenance_count,
                        self.recovery_count,
                        maintained.lease.heartbeat_at,
                    )
                    break
        if telemetry is not None and self.maintenance_callback is not None:
            try:
                self.maintenance_callback(*telemetry)
            except Exception:
                # Lease ownership is the correctness boundary. UI telemetry is
                # best-effort and must not terminate an otherwise healthy run.
                pass

    def telemetry(self) -> tuple[int, int, datetime | None]:
        with self._maintenance_lock:
            return (
                self.maintenance_count,
                self.recovery_count,
                self.last_heartbeat_at,
            )

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.maintain_now()
            except RuntimeError:
                return


class WalkForwardSelection(BaseModel):
    instrument_id: str
    status: str
    primary_strategy_id: str | None
    rank_score: Decimal
    trigger_price: Decimal | None
    initial_stop: Decimal | None
    target_1: Decimal | None
    no_chase_above: Decimal | None = None
    factor_signals: list[str] = Field(default_factory=list)
    asset_type: str = "unknown"
    industry: str | None = None
    index_memberships: list[str] = Field(default_factory=list)
    rerank_score: float | None = None
    rerank_baseline_position: int | None = None
    rerank_position: int | None = None
    rerank_training_samples: int = 0
    rerank_expected_return_pct: float | None = None
    rerank_win_probability: float | None = None
    rerank_reason: str = ""


class WalkForwardSnapshot(BaseModel):
    decision_date: date
    historical_universe_size: int
    eligible_size: int
    evaluated_size: int = 0
    prefilter_ranked_size: int = 0
    recommendation_card_count: int = 0
    paper_eligible_card_count: int = 0
    paper_blocked_card_count: int = 0
    suspended_count: int
    st_excluded_count: int
    missing_tradability_count: int
    fundamental_universe_size: int = 0
    fundamental_covered_count: int = 0
    benchmark_trend_state: str = BenchmarkTrendState.UNKNOWN.value
    benchmark_trend_valid_count: int = 0
    benchmark_trend_above_count: int = 0
    market_entry_allowed: bool = True
    strategy_diversification_limit: int = 2
    strategy_diversified_count: int = 0
    top_5: list[WalkForwardSelection] = Field(default_factory=list)
    top_10: list[WalkForwardSelection] = Field(default_factory=list)
    dynamic_top_5: list[WalkForwardSelection] = Field(default_factory=list)
    rerank_training_cutoff_date: date | None = None
    rerank_training_sample_count: int = 0
    rerank_model_ready: bool = False
    rerank_constraint_blocked_count: int = 0
    rerank_incomplete_index_snapshot_count: int = 0


class WalkForwardGateCriterion(BaseModel):
    key: str
    label: str
    status: str
    value: str
    requirement: str


class WalkForwardEvidenceMetric(BaseModel):
    dimension: str
    key: str
    label: str
    trade_count: int
    out_of_sample_count: int
    win_rate: float | None
    average_return_pct: float | None
    worst_return_pct: float | None
    profit_factor: float | None
    max_consecutive_losses: int
    out_of_sample_verdict: str
    statistical_method: str = "signal_date_cluster_bootstrap_sign_flip"
    statistical_sample_count: int = 0
    statistical_cluster_count: int = 0
    confidence_low_pct: float | None = None
    confidence_high_pct: float | None = None
    positive_edge_p_value: float | None = None
    negative_edge_p_value: float | None = None
    false_discovery_rate: float | None = None
    statistical_verdict: str = "insufficient"
    action: str
    suggested_weight_delta: float
    reason: str


class WalkForwardValidationCenter(BaseModel):
    status: str
    headline: str
    criteria: list[WalkForwardGateCriterion] = Field(default_factory=list)
    strategies: list[WalkForwardEvidenceMetric] = Field(default_factory=list)
    factors: list[WalkForwardEvidenceMetric] = Field(default_factory=list)


class WalkForwardRerankEvaluation(BaseModel):
    model_version: str
    status: str
    headline: str
    leakage_guard: str
    evaluated_snapshot_count: int
    changed_snapshot_count: int
    promoted_selection_count: int
    constraint_blocked_selection_count: int
    incomplete_index_snapshot_count: int
    maximum_training_sample_count: int
    baseline_return_delta_pct: float
    baseline_max_drawdown_delta_pct: float
    portfolio: PortfolioBacktestResult
    metrics: "WalkForwardPortfolioMetrics"
    temporal_validation: TemporalValidationResult
    criteria: list[WalkForwardGateCriterion] = Field(default_factory=list)


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
    strategy_validation: WalkForwardValidationCenter
    dynamic_rerank: WalkForwardRerankEvaluation
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
    dynamic_top_5_excess_return_pct: float | None = None


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
    dynamic_top_5_return_pct: float | None = None
    dynamic_top_5_max_drawdown_pct: float | None = None
    dynamic_top_5_total_costs: Decimal | None = None


class WalkForwardProgress(BaseModel):
    phase: str
    processed_snapshots: int
    total_snapshots: int
    current_date: date | None = None
    snapshot: WalkForwardSnapshot | None = None
    lease_maintenance_count: int = 0
    lease_recovery_count: int = 0
    last_lease_heartbeat_at: datetime | None = None


class _WalkForwardSnapshotInput(BaseModel):
    decision_date: date
    historical_universe_size: int
    eligible: list[str]
    eligible_stocks: list[str]
    eligible_non_stocks: list[str] = Field(default_factory=list)
    suspended_count: int
    st_excluded_count: int
    missing_tradability_count: int


class _WalkForwardWorkerStats(BaseModel):
    market_queries: int = 0
    full_window_queries: int = 0
    incremental_queries: int = 0
    rows_loaded: int = 0
    fundamental_prefetches: int = 0
    fundamental_fallback_queries: int = 0


class _WalkForwardWorkerResult(BaseModel):
    worker_pid: int
    snapshot: WalkForwardSnapshot
    scan_error_count: int
    scan_error_samples: list[str] = Field(default_factory=list)
    stats: _WalkForwardWorkerStats


_snapshot_worker_repository: ReplayEvidenceRepository | None = None
_snapshot_worker_market_provider: ReplayMarketDataProvider | None = None
_snapshot_worker_strategy_provider: ReplayStrategyDataProvider | None = None


def _repository_database_url(repository: ReplayEvidenceRepository) -> str:
    bind = repository.session_factory.kw.get("bind")
    if bind is None:
        raise RuntimeError("walk-forward repository has no database engine")
    return bind.url.render_as_string(hide_password=False)


def _initialize_snapshot_worker(
    database_url: str,
    provider_mode: str,
    owner_run_id: str,
    revision: int,
) -> None:
    from qagent.db import create_session_factory

    global _snapshot_worker_repository
    global _snapshot_worker_market_provider
    global _snapshot_worker_strategy_provider
    _snapshot_worker_repository = ReplayEvidenceRepository(
        create_session_factory(database_url),
        provider_mode,
        owner_run_id=owner_run_id,
    )
    _snapshot_worker_market_provider = ReplayMarketDataProvider(
        _snapshot_worker_repository,
        revision,
    )
    _snapshot_worker_strategy_provider = ReplayStrategyDataProvider(
        _snapshot_worker_repository,
        revision,
    )


def _compute_snapshot_in_worker(
    snapshot_input: _WalkForwardSnapshotInput,
    lookback_days: int,
) -> _WalkForwardWorkerResult:
    if (
        _snapshot_worker_repository is None
        or _snapshot_worker_market_provider is None
        or _snapshot_worker_strategy_provider is None
    ):
        raise RuntimeError("walk-forward snapshot worker is not initialized")
    return _compute_walk_forward_snapshot(
        snapshot_input,
        lookback_days=lookback_days,
        repository=_snapshot_worker_repository,
        market_provider=_snapshot_worker_market_provider,
        strategy_provider=_snapshot_worker_strategy_provider,
    )


def _compute_walk_forward_snapshot(
    snapshot_input: _WalkForwardSnapshotInput,
    *,
    lookback_days: int,
    repository: ReplayEvidenceRepository,
    market_provider: ReplayMarketDataProvider,
    strategy_provider: ReplayStrategyDataProvider,
) -> _WalkForwardWorkerResult:
    gc_was_enabled = gc.isenabled()
    if gc_was_enabled:
        gc.disable()
    try:
        return _compute_walk_forward_snapshot_without_gc(
            snapshot_input,
            lookback_days=lookback_days,
            repository=repository,
            market_provider=market_provider,
            strategy_provider=strategy_provider,
        )
    finally:
        if gc_was_enabled:
            gc.enable()
            gc.collect()


def _compute_walk_forward_snapshot_without_gc(
    snapshot_input: _WalkForwardSnapshotInput,
    *,
    lookback_days: int,
    repository: ReplayEvidenceRepository,
    market_provider: ReplayMarketDataProvider,
    strategy_provider: ReplayStrategyDataProvider,
) -> _WalkForwardWorkerResult:
    decision_date = snapshot_input.decision_date
    fundamental_evidence = repository.fundamentals_as_of(
        snapshot_input.eligible_stocks,
        decision_date,
        market_provider.revision,
    )
    fundamental_covered_count = sum(
        _has_usable_fundamental(item) for item in fundamental_evidence.values()
    )
    prefilter_start = decision_date - timedelta(
        days=min(lookback_days, PREFILTER_LOOKBACK_DAYS)
    )
    market_provider.prefetch_daily_bars(
        snapshot_input.eligible,
        prefilter_start,
        decision_date,
    )
    prefilter_bars = market_provider.get_daily_bars(
        snapshot_input.eligible,
        prefilter_start,
        decision_date,
    )
    factor_rankings = build_factor_rankings(
        _adjusted_prefilter_bars(prefilter_bars),
        fundamentals=fundamental_evidence,
    )
    candidates = _walk_forward_candidates(
        eligible=snapshot_input.eligible,
        eligible_non_stocks=snapshot_input.eligible_non_stocks,
        rankings=factor_rankings,
        limit=PREFILTER_CANDIDATE_LIMIT,
    )
    window_start = decision_date - timedelta(days=lookback_days)
    market_provider.prefetch_daily_bars(
        candidates,
        window_start,
        decision_date,
    )
    strategy_provider.prefetch_fundamentals(
        candidates,
        decision_date,
        snapshots={
            instrument_id: fundamental_evidence[instrument_id]
            for instrument_id in candidates
            if instrument_id in fundamental_evidence
        },
    )
    scan = run_daily_scan(
        candidates,
        market_provider,
        mode="historical_replay",
        strategy_data_provider=strategy_provider,
        a_share_enhanced_provider=EmptyAShareEnhancedDataProvider(),
        factor_rankings_override=factor_rankings,
        start=window_start,
        end=decision_date,
    )
    errors = [item.reason for item in scan.items if item.status == "error"]
    recommendation_cards = [
        card for card in scan.cards if card.status.value not in EXCLUDED_STATUSES
    ]
    paper_eligible_ids = _paper_eligible_card_ids(scan.strategy_governance)
    eligible_cards = (
        [
            card
            for card in recommendation_cards
            if card.card_id in paper_eligible_ids
        ]
        if paper_eligible_ids is not None
        else recommendation_cards
    )
    selections = [_selection(card) for card in eligible_cards]
    benchmark_bars = market_provider.get_daily_bars(
        list(REQUIRED_BENCHMARK_IDS),
        decision_date - timedelta(days=200),
        decision_date,
    )
    benchmark_trend = build_benchmark_trend_snapshot(
        benchmark_bars,
        as_of=decision_date,
    )
    diversified = (
        select_strategy_diversified(
            selections,
            limit=10,
            max_per_strategy=2,
        )
        if benchmark_trend.entry_allowed
        else []
    )
    return _WalkForwardWorkerResult(
        worker_pid=os.getpid(),
        snapshot=WalkForwardSnapshot(
            decision_date=decision_date,
            historical_universe_size=snapshot_input.historical_universe_size,
            eligible_size=len(snapshot_input.eligible),
            evaluated_size=len(candidates),
            prefilter_ranked_size=len(factor_rankings),
            recommendation_card_count=len(recommendation_cards),
            paper_eligible_card_count=len(eligible_cards),
            paper_blocked_card_count=(
                len(recommendation_cards) - len(eligible_cards)
            ),
            suspended_count=snapshot_input.suspended_count,
            st_excluded_count=snapshot_input.st_excluded_count,
            missing_tradability_count=snapshot_input.missing_tradability_count,
            fundamental_universe_size=len(snapshot_input.eligible_stocks),
            fundamental_covered_count=fundamental_covered_count,
            benchmark_trend_state=benchmark_trend.state.value,
            benchmark_trend_valid_count=benchmark_trend.valid_benchmarks,
            benchmark_trend_above_count=benchmark_trend.above_average_count,
            market_entry_allowed=benchmark_trend.entry_allowed,
            strategy_diversified_count=len(diversified),
            top_5=diversified[:5],
            top_10=diversified,
        ),
        scan_error_count=len(errors),
        scan_error_samples=errors[:3],
        stats=_snapshot_worker_stats(market_provider, strategy_provider),
    )


def _paper_eligible_card_ids(governance) -> set[str] | None:
    if not governance:
        return None
    return {
        audit.card_id
        for audit in governance
        if audit.gate_decision.paper_candidate_eligible
    }


def _adjusted_prefilter_bars(bars):
    if bars.empty or "adjusted_close" not in bars.columns:
        return bars
    adjusted = bars.loc[bars["adjusted_close"].notna()].copy()
    if adjusted.empty:
        return bars
    for column in ("open", "high", "low", "close"):
        adjusted_column = f"adjusted_{column}"
        fallback = adjusted[column]
        if "adjustment_factor" in adjusted.columns:
            fallback = adjusted[column] * adjusted["adjustment_factor"]
        if adjusted_column in adjusted.columns:
            adjusted[column] = adjusted[adjusted_column].where(
                adjusted[adjusted_column].notna(),
                fallback,
            )
        elif column == "close":
            adjusted[column] = adjusted["adjusted_close"]
        elif "adjustment_factor" in adjusted.columns:
            adjusted[column] = adjusted[column] * adjusted["adjustment_factor"]
    return adjusted


def _walk_forward_candidates(
    *,
    eligible: list[str],
    eligible_non_stocks: list[str],
    rankings: list[FactorRanking],
    limit: int,
) -> list[str]:
    if limit <= 0:
        raise ValueError("walk-forward candidate limit must be positive")
    universe = set(eligible)
    if len(eligible) <= limit:
        return list(eligible)
    ranked = [
        item.instrument_id
        for item in rankings
        if item.instrument_id in universe
    ]
    if not ranked:
        return sorted(eligible)[:limit]
    non_stocks = set(eligible_non_stocks)
    reserve = min(
        len(non_stocks),
        limit,
        max(1, math.ceil(limit * PREFILTER_NON_STOCK_RESERVE_RATIO)),
    )
    reserved_ids = [
        instrument_id
        for instrument_id in ranked
        if instrument_id in non_stocks
    ][:reserve]
    selected_ids = set(reserved_ids)
    for instrument_id in ranked:
        if len(selected_ids) >= limit:
            break
        selected_ids.add(instrument_id)
    if len(selected_ids) < limit:
        for instrument_id in sorted(universe.difference(selected_ids)):
            selected_ids.add(instrument_id)
            if len(selected_ids) >= limit:
                break
    ranked_position = {instrument_id: index for index, instrument_id in enumerate(ranked)}
    return sorted(
        selected_ids,
        key=lambda instrument_id: (
            ranked_position.get(instrument_id, len(ranked)),
            instrument_id,
        ),
    )


def _snapshot_worker_stats(
    market_provider: ReplayMarketDataProvider,
    strategy_provider: ReplayStrategyDataProvider,
) -> _WalkForwardWorkerStats:
    return _WalkForwardWorkerStats(
        market_queries=market_provider.query_count,
        full_window_queries=market_provider.full_window_queries,
        incremental_queries=market_provider.incremental_queries,
        rows_loaded=market_provider.rows_loaded,
        fundamental_prefetches=strategy_provider.prefetch_count,
        fundamental_fallback_queries=strategy_provider.query_count,
    )


def _sum_worker_stats(
    worker_stats: Iterable[_WalkForwardWorkerStats],
) -> _WalkForwardWorkerStats:
    values = list(worker_stats)
    return _WalkForwardWorkerStats(
        market_queries=sum(item.market_queries for item in values),
        full_window_queries=sum(item.full_window_queries for item in values),
        incremental_queries=sum(item.incremental_queries for item in values),
        rows_loaded=sum(item.rows_loaded for item in values),
        fundamental_prefetches=sum(item.fundamental_prefetches for item in values),
        fundamental_fallback_queries=sum(
            item.fundamental_fallback_queries for item in values
        ),
    )


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
    lease_maintenance_callback: Callable[[int, int, datetime], None] | None = None,
    initial_lease_maintenance_count: int = 0,
    initial_lease_recovery_count: int = 0,
    initial_lease_heartbeat_at: datetime | None = None,
    snapshot_workers: int = 1,
) -> WalkForwardSelectionResult:
    if start > end:
        raise ValueError("start must be on or before end")
    if rebalance_step_sessions <= 0:
        raise ValueError("rebalance_step_sessions must be positive")
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    if snapshot_workers <= 0:
        raise ValueError("snapshot_workers must be positive")
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
    owner_repository = repository.for_owner(owner_run_id)
    lease = owner_repository.acquire_dataset_lease()
    if lease.revision != revision:
        owner_repository.release_dataset_lease()
        raise RuntimeError("dataset revision changed while acquiring replay lease")
    market_provider = ReplayMarketDataProvider(owner_repository, revision)
    strategy_provider = ReplayStrategyDataProvider(owner_repository, revision)
    lease_heartbeat = _DatasetLeaseHeartbeat(
        owner_repository,
        expected_revision=revision,
        maintenance_callback=lease_maintenance_callback,
        initial_maintenance_count=initial_lease_maintenance_count,
        initial_recovery_count=initial_lease_recovery_count,
        initial_heartbeat_at=initial_lease_heartbeat_at,
    )
    lease_heartbeat.start()
    snapshots: list[WalkForwardSnapshot] = []
    eligible_universes: list[tuple[date, list[str]]] = []
    scan_error_count = 0
    scan_error_samples: list[str] = []
    selection_worker_stats = _WalkForwardWorkerStats()
    effective_snapshot_workers = 1
    try:
        sessions = trading_sessions_in_range(start, end)[::rebalance_step_sessions]
        resumed = {item.decision_date: item for item in (resume_snapshots or [])}
        unexpected_dates = set(resumed).difference(sessions)
        if unexpected_dates:
            raise ValueError("resume snapshots do not match the requested validation window")
        lifecycle_inventory = owner_repository.lifecycle_inventory(
            revision,
            decision_date=sessions[-1] if sessions else end,
        )
        missing_listing_dates = [
            item.instrument_id
            for item in lifecycle_inventory
            if item.listing_date is None
        ]
        if missing_listing_dates:
            raise ReplayEvidenceUnavailable(
                "lifecycle identity is incomplete; listing_date is missing for "
                + ", ".join(missing_listing_dates[:10])
            )
        missing_security_types = [
            item.instrument_id
            for item in lifecycle_inventory
            if not item.security_type or not item.security_type.strip()
        ]
        if missing_security_types:
            raise ReplayEvidenceUnavailable(
                "lifecycle identity is incomplete; security_type is missing for "
                + ", ".join(missing_security_types[:10])
            )
        _report_progress(
            progress_callback,
            phase="preparing_historical_replay",
            processed_snapshots=len(resumed),
            total_snapshots=len(sessions),
            lease_heartbeat=lease_heartbeat,
        )
        snapshot_inputs: list[_WalkForwardSnapshotInput] = []
        inventory_ids = [item.instrument_id for item in lifecycle_inventory]
        eligible_date_ranges: dict[str, tuple[date, date]] = {}
        for batch_start in range(0, len(sessions), 8):
            lease_heartbeat.maintain_now()
            batch_dates = sessions[batch_start : batch_start + 8]
            tradability_by_date = owner_repository.tradability_on_dates(
                inventory_ids,
                batch_dates,
                revision,
            )
            for decision_date in batch_dates:
                members = [
                    item
                    for item in lifecycle_inventory
                    if item.listing_date is not None
                    and item.listing_date <= decision_date
                    and (
                        item.delisting_date is None
                        or item.delisting_date > decision_date
                    )
                ]
                instrument_ids = [item.instrument_id for item in members]
                tradability = tradability_by_date.get(decision_date, {})
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
                eligible = sorted(eligible)
                for instrument_id in eligible:
                    first_date, last_date = eligible_date_ranges.get(
                        instrument_id,
                        (decision_date, decision_date),
                    )
                    eligible_date_ranges[instrument_id] = (
                        min(first_date, decision_date),
                        max(last_date, decision_date),
                    )
                eligible_universes.append((decision_date, eligible))
                stock_ids = {
                    item.instrument_id
                    for item in members
                    if item.security_type in {"stock", "1"}
                }
                eligible_stocks = [item for item in eligible if item in stock_ids]
                eligible_non_stocks = [
                    item for item in eligible if item not in stock_ids
                ]
                snapshot_inputs.append(
                    _WalkForwardSnapshotInput(
                        decision_date=decision_date,
                        historical_universe_size=len(instrument_ids),
                        eligible=eligible,
                        eligible_stocks=eligible_stocks,
                        eligible_non_stocks=eligible_non_stocks,
                        suspended_count=suspended_count,
                        st_excluded_count=st_excluded_count,
                        missing_tradability_count=missing_tradability_count,
                    )
                )
        metadata_profiles = [
            item.model_copy(
                update={
                    "listing_date": eligible_date_ranges[item.instrument_id][0],
                    "delisting_date": eligible_date_ranges[item.instrument_id][1],
                }
            )
            for item in lifecycle_inventory
            if item.instrument_id in eligible_date_ranges
        ]
        metadata_gaps = owner_repository.instrument_rule_metadata_gaps(
            metadata_profiles,
            start,
            end,
        )
        if metadata_gaps:
            samples = ", ".join(
                f"{instrument_id} {gap_start.isoformat()}..{gap_end.isoformat()}"
                for instrument_id, gap_start, gap_end in metadata_gaps
            )
            raise ReplayEvidenceUnavailable(
                "instrument rule metadata coverage is incomplete: "
                f"{samples}; rerun the historical backfill before validation"
            )
        snapshot_by_date = dict(resumed)
        pending_inputs = [
            item for item in snapshot_inputs if item.decision_date not in snapshot_by_date
        ]
        effective_snapshot_workers = min(snapshot_workers, len(pending_inputs)) or 1
        _report_progress(
            progress_callback,
            phase="historical_replay",
            processed_snapshots=len(snapshot_by_date),
            total_snapshots=len(sessions),
            current_date=max(snapshot_by_date, default=None),
            lease_heartbeat=lease_heartbeat,
        )
        if effective_snapshot_workers == 1:
            for snapshot_input in pending_inputs:
                lease_heartbeat.maintain_now()
                worker_result = _compute_walk_forward_snapshot(
                    snapshot_input,
                    lookback_days=lookback_days,
                    repository=owner_repository,
                    market_provider=market_provider,
                    strategy_provider=strategy_provider,
                )
                lease_heartbeat.raise_if_failed()
                snapshot_by_date[worker_result.snapshot.decision_date] = worker_result.snapshot
                scan_error_count += worker_result.scan_error_count
                scan_error_samples.extend(worker_result.scan_error_samples)
                _report_progress(
                    progress_callback,
                    phase="historical_replay",
                    processed_snapshots=len(snapshot_by_date),
                    total_snapshots=len(sessions),
                    current_date=worker_result.snapshot.decision_date,
                    snapshot=worker_result.snapshot,
                    lease_heartbeat=lease_heartbeat,
                )
        elif pending_inputs:
            database_url = _repository_database_url(owner_repository)
            stats_by_worker: dict[int, _WalkForwardWorkerStats] = {}
            with ProcessPoolExecutor(
                max_workers=effective_snapshot_workers,
                mp_context=get_context("spawn"),
                initializer=_initialize_snapshot_worker,
                initargs=(
                    database_url,
                    repository.provider_mode,
                    owner_run_id,
                    revision,
                ),
            ) as executor:
                futures = [
                    executor.submit(
                        _compute_snapshot_in_worker,
                        snapshot_input,
                        lookback_days,
                    )
                    for snapshot_input in pending_inputs
                ]
                for future in as_completed(futures):
                    worker_result = future.result()
                    lease_heartbeat.raise_if_failed()
                    snapshot_by_date[
                        worker_result.snapshot.decision_date
                    ] = worker_result.snapshot
                    scan_error_count += worker_result.scan_error_count
                    scan_error_samples.extend(worker_result.scan_error_samples)
                    stats_by_worker[worker_result.worker_pid] = worker_result.stats
                    _report_progress(
                        progress_callback,
                        phase="historical_replay",
                        processed_snapshots=len(snapshot_by_date),
                        total_snapshots=len(sessions),
                        current_date=worker_result.snapshot.decision_date,
                        snapshot=worker_result.snapshot,
                        lease_heartbeat=lease_heartbeat,
                    )
            selection_worker_stats = _sum_worker_stats(stats_by_worker.values())
        snapshots = [snapshot_by_date[item] for item in sessions]
        snapshots = _enrich_selection_constraints(
            snapshots,
            repository=owner_repository,
            revision=revision,
            asset_types={
                item.instrument_id: item.security_type
                for item in lifecycle_inventory
            },
        )
        _report_progress(
            progress_callback,
            phase="portfolio_simulation",
            processed_snapshots=len(snapshots),
            total_snapshots=len(sessions),
            lease_heartbeat=lease_heartbeat,
        )
        lease_heartbeat.maintain_now()
        execution_resolver = VersionedAshareExecutionResolver(
            owner_repository,
            dataset_revision=revision,
        )
        top_5_signals = _signals(snapshots, size=5)
        top_10_signals = _signals(snapshots, size=10)
        top_5_portfolio = run_signal_portfolio_backtest(
            signals=top_5_signals,
            instrument_ids=sorted({item.instrument_id for item in top_5_signals}),
            provider=market_provider,
            start=start,
            end=end,
            max_positions=5,
            execution_rule_resolver=execution_resolver,
        )
        top_10_portfolio = run_signal_portfolio_backtest(
            signals=top_10_signals,
            instrument_ids=sorted({item.instrument_id for item in top_10_signals}),
            provider=market_provider,
            start=start,
            end=end,
            max_positions=10,
            execution_rule_resolver=execution_resolver,
        )
        snapshots = _apply_dynamic_reranking(
            snapshots,
            top_10_portfolio=top_10_portfolio,
        )
        dynamic_top_5_signals = _signals(
            snapshots,
            size=5,
            selection_source="dynamic",
        )
        dynamic_top_5_portfolio = run_signal_portfolio_backtest(
            signals=dynamic_top_5_signals,
            instrument_ids=sorted(
                {item.instrument_id for item in dynamic_top_5_signals}
            ),
            provider=market_provider,
            start=start,
            end=end,
            max_positions=5,
            execution_rule_resolver=execution_resolver,
        )
        top_5_metrics = _portfolio_metrics(top_5_portfolio, start, end)
        top_10_metrics = _portfolio_metrics(top_10_portfolio, start, end)
        dynamic_top_5_metrics = _portfolio_metrics(
            dynamic_top_5_portfolio,
            start,
            end,
        )
        top_5_temporal_validation = _trade_temporal_validation(top_5_portfolio.trades)
        top_10_temporal_validation = _trade_temporal_validation(top_10_portfolio.trades)
        dynamic_top_5_temporal_validation = _trade_temporal_validation(
            dynamic_top_5_portfolio.trades
        )
        _report_progress(
            progress_callback,
            phase="validation_and_benchmarks",
            processed_snapshots=len(snapshots),
            total_snapshots=len(sessions),
            lease_heartbeat=lease_heartbeat,
        )
        cost_sensitivity = _cost_sensitivity(
            top_5_signals=top_5_signals,
            top_10_signals=top_10_signals,
            dynamic_top_5_signals=dynamic_top_5_signals,
            top_5_portfolio=top_5_portfolio,
            top_10_portfolio=top_10_portfolio,
            dynamic_top_5_portfolio=dynamic_top_5_portfolio,
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
            dynamic_top_5_return=dynamic_top_5_metrics.total_return_pct,
            eligible_universes=eligible_universes,
        )
        lease_heartbeat.raise_if_failed()
    finally:
        lease_heartbeat.stop()
        owner_repository.release_dataset_lease()
    coverage = _cross_section_coverage(snapshots)
    fundamental_coverage = _fundamental_coverage(snapshots)
    market_coverage_gate = (
        "ready" if coverage["ratio"] >= MIN_FULL_MARKET_COVERAGE_RATIO else "insufficient"
    )
    fundamental_coverage_gate = (
        "ready"
        if fundamental_coverage >= MIN_FUNDAMENTAL_COVERAGE_RATIO
        else "insufficient"
    )
    top_5_sample_gate = _oos_gate(top_5_temporal_validation)
    top_10_sample_gate = _oos_gate(top_10_temporal_validation)
    strategy_validation = _build_strategy_validation_center(
        snapshots=snapshots,
        portfolio=top_5_portfolio,
        temporal_validation=top_5_temporal_validation,
        benchmarks=benchmarks,
        cost_sensitivity=cost_sensitivity,
        market_coverage_ratio=coverage["ratio"],
        fundamental_coverage_ratio=fundamental_coverage,
        metrics=top_5_metrics,
    )
    dynamic_rerank = _build_dynamic_rerank_evaluation(
        snapshots=snapshots,
        baseline_metrics=top_5_metrics,
        baseline_temporal_validation=top_5_temporal_validation,
        portfolio=dynamic_top_5_portfolio,
        metrics=dynamic_top_5_metrics,
        temporal_validation=dynamic_top_5_temporal_validation,
        benchmarks=benchmarks,
        cost_sensitivity=cost_sensitivity,
        market_coverage_ratio=coverage["ratio"],
        fundamental_coverage_ratio=fundamental_coverage,
    )
    digest = _selection_digest(
        snapshots,
        revision,
        top_5_portfolio,
        top_10_portfolio,
        benchmarks,
        cost_sensitivity,
        strategy_validation,
        dynamic_rerank,
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
        strategy_validation=strategy_validation,
        dynamic_rerank=dynamic_rerank,
        experiment_manifest=experiment_manifest,
        reproducibility_digest=digest,
        data_health={
            "walk_forward_revision": str(revision),
            "walk_forward_snapshots": str(len(snapshots)),
            "walk_forward_lookback_days": str(lookback_days),
            "walk_forward_scan_errors": str(scan_error_count),
            "walk_forward_snapshot_workers": str(effective_snapshot_workers),
            "walk_forward_future_data_guard": "revision_lease_and_decision_date_cutoff",
            "walk_forward_lease_maintenance_count": str(
                lease_heartbeat.maintenance_count
            ),
            "walk_forward_lease_recovery_count": str(lease_heartbeat.recovery_count),
            "walk_forward_universe": "historical_lifecycle_per_rebalance_date",
            "walk_forward_selection_pipeline": (
                "full_market_point_in_time_factor_prefilter_then_full_strategy"
            ),
            "walk_forward_prefilter_lookback_days": str(PREFILTER_LOOKBACK_DAYS),
            "walk_forward_candidate_limit": str(PREFILTER_CANDIDATE_LIMIT),
            "walk_forward_median_evaluated_instruments": str(
                round(statistics.median(item.evaluated_size for item in snapshots))
                if snapshots
                else 0
            ),
            "walk_forward_recommendation_cards": str(
                sum(item.recommendation_card_count for item in snapshots)
            ),
            "walk_forward_paper_eligible_cards": str(
                sum(item.paper_eligible_card_count for item in snapshots)
            ),
            "walk_forward_paper_blocked_cards": str(
                sum(item.paper_blocked_card_count for item in snapshots)
            ),
            "walk_forward_execution_admission": (
                "final_policy_paper_candidate_eligible"
            ),
            "walk_forward_strategy_diversification_limit": "2",
            "walk_forward_strategy_diversified_selections": str(
                sum(item.strategy_diversified_count for item in snapshots)
            ),
            "walk_forward_benchmark_trend_policy": (
                "block_entries_when_3_of_4_benchmarks_below_ma60"
            ),
            "walk_forward_market_entry_blocked_snapshots": str(
                sum(not item.market_entry_allowed for item in snapshots)
            ),
            "walk_forward_benchmark_trend_unknown_snapshots": str(
                sum(
                    item.benchmark_trend_state == BenchmarkTrendState.UNKNOWN.value
                    for item in snapshots
                )
            ),
            "walk_forward_st_policy": "excluded",
            "walk_forward_validation_scope": (
                "full_market" if market_coverage_gate == "ready" else "pilot"
            ),
            "walk_forward_market_coverage_gate": market_coverage_gate,
            "walk_forward_minimum_market_coverage_pct": str(MIN_FULL_MARKET_COVERAGE_RATIO * 100),
            "walk_forward_cross_section_coverage_pct": str(round(coverage["ratio"] * 100, 4)),
            "walk_forward_median_covered_instruments": str(coverage["median_covered"]),
            "walk_forward_median_historical_universe": str(coverage["median_universe"]),
            "walk_forward_fundamental_coverage_pct": str(
                round(fundamental_coverage * 100, 4)
            ),
            "walk_forward_fundamental_coverage_gate": fundamental_coverage_gate,
            "walk_forward_minimum_fundamental_coverage_pct": str(
                MIN_FUNDAMENTAL_COVERAGE_RATIO * 100
            ),
            "walk_forward_top_5_trades": str(top_5_portfolio.summary.trade_count),
            "walk_forward_top_10_trades": str(top_10_portfolio.summary.trade_count),
            "walk_forward_dynamic_top_5_trades": str(
                dynamic_top_5_portfolio.summary.trade_count
            ),
            "walk_forward_dynamic_reranker_version": DYNAMIC_RERANKER_VERSION,
            "walk_forward_dynamic_reranker_gate": dynamic_rerank.status,
            "walk_forward_dynamic_changed_snapshots": str(
                dynamic_rerank.changed_snapshot_count
            ),
            "walk_forward_dynamic_promoted_selections": str(
                dynamic_rerank.promoted_selection_count
            ),
            "walk_forward_dynamic_constraint_blocked": str(
                dynamic_rerank.constraint_blocked_selection_count
            ),
            "walk_forward_dynamic_incomplete_index_snapshots": str(
                dynamic_rerank.incomplete_index_snapshot_count
            ),
            "walk_forward_dynamic_index_membership_policy": (
                "use_complete_snapshots_and_block_release_when_partial"
            ),
            "walk_forward_dynamic_portfolio_constraints": (
                "max_industry_2,max_overlapping_etf_1"
            ),
            "walk_forward_dynamic_future_data_guard": (
                "training_trade_exit_date_strictly_before_decision_date"
            ),
            "walk_forward_oos_minimum_trades": "30",
            "walk_forward_top_5_oos_trades": str(_oos_sample_count(top_5_temporal_validation)),
            "walk_forward_top_10_oos_trades": str(_oos_sample_count(top_10_temporal_validation)),
            "walk_forward_top_5_oos_gate": top_5_sample_gate,
            "walk_forward_top_10_oos_gate": top_10_sample_gate,
            "walk_forward_top_5_validation_gate": _combined_validation_gate(
                top_5_sample_gate,
                market_coverage_gate,
                fundamental_coverage_gate,
            ),
            "walk_forward_top_10_validation_gate": _combined_validation_gate(
                top_10_sample_gate,
                market_coverage_gate,
                fundamental_coverage_gate,
            ),
            "walk_forward_benchmarks_ready": (
                f"{sum(item.status == 'ready' for item in benchmarks)}/{len(benchmarks)}"
            ),
            "walk_forward_equal_weight_benchmark": next(
                item.status
                for item in benchmarks
                if item.benchmark_id == ELIGIBLE_UNIVERSE_BENCHMARK_ID
            ),
            "walk_forward_cost_scenarios": str(len(cost_sensitivity)),
            "walk_forward_replay_cache_queries": str(
                market_provider.query_count + selection_worker_stats.market_queries
            ),
            "walk_forward_replay_cache_full_queries": str(
                market_provider.full_window_queries
                + selection_worker_stats.full_window_queries
            ),
            "walk_forward_replay_cache_incremental_queries": str(
                market_provider.incremental_queries
                + selection_worker_stats.incremental_queries
            ),
            "walk_forward_replay_cache_rows_loaded": str(
                market_provider.rows_loaded + selection_worker_stats.rows_loaded
            ),
            "walk_forward_fundamental_prefetches": str(
                strategy_provider.prefetch_count
                + selection_worker_stats.fundamental_prefetches
            ),
            "walk_forward_fundamental_fallback_queries": str(
                strategy_provider.query_count
                + selection_worker_stats.fundamental_fallback_queries
            ),
            "walk_forward_release_gate": strategy_validation.status,
            "walk_forward_statistical_unit": "signal_date_cluster",
            "walk_forward_statistical_test": "cluster_bootstrap_sign_flip",
            "walk_forward_multiple_testing": "benjamini_hochberg",
            "walk_forward_significance_level": "0.05",
            "walk_forward_false_discovery_rate": "0.10",
            "walk_forward_enabled_strategies": str(
                sum(item.action == "increase" for item in strategy_validation.strategies)
            ),
            "walk_forward_disabled_strategies": str(
                sum(item.action == "disable" for item in strategy_validation.strategies)
            ),
            "walk_forward_stress_top_5_return_pct": str(cost_sensitivity[-1].top_5_return_pct),
            "walk_forward_stress_top_10_return_pct": str(cost_sensitivity[-1].top_10_return_pct),
            "walk_forward_stress_dynamic_top_5_return_pct": str(
                cost_sensitivity[-1].dynamic_top_5_return_pct
            ),
            "walk_forward_digest": digest,
            "walk_forward_experiment_digest": experiment_manifest.experiment_digest,
            "walk_forward_code_revision": experiment_manifest.code_revision,
            "walk_forward_runtime_revisions": ",".join(
                experiment_manifest.runtime_revisions
                or [experiment_manifest.code_revision]
            ),
            "walk_forward_strategy_registry_digest": (experiment_manifest.strategy_registry_digest),
            **(
                {"walk_forward_error_samples": " | ".join(scan_error_samples[:3])}
                if scan_error_samples
                else {}
            ),
        },
    )
    _report_progress(
        progress_callback,
        phase="completed",
        processed_snapshots=len(snapshots),
        total_snapshots=len(snapshots),
        lease_heartbeat=lease_heartbeat,
    )
    return result


def _cross_section_coverage(
    snapshots: list[WalkForwardSnapshot],
) -> dict[str, float | int]:
    universe_sizes = [item.historical_universe_size for item in snapshots]
    covered_sizes = [
        max(0, item.historical_universe_size - item.missing_tradability_count) for item in snapshots
    ]
    total_universe = sum(universe_sizes)
    ratio = sum(covered_sizes) / total_universe if total_universe else 0.0
    return {
        "ratio": ratio,
        "median_covered": int(statistics.median(covered_sizes)) if covered_sizes else 0,
        "median_universe": int(statistics.median(universe_sizes)) if universe_sizes else 0,
    }


def _fundamental_coverage(snapshots: list[WalkForwardSnapshot]) -> float:
    total = sum(item.fundamental_universe_size for item in snapshots)
    covered = sum(item.fundamental_covered_count for item in snapshots)
    return covered / total if total else 1.0


def _has_usable_fundamental(snapshot: object) -> bool:
    return any(
        getattr(snapshot, field, None) is not None
        for field in (
            "market_cap",
            "pe_ratio",
            "return_on_equity_pct",
            "revenue_growth_pct",
            "earnings_growth_pct",
        )
    )


def _combined_validation_gate(
    sample_gate: str,
    market_coverage_gate: str,
    fundamental_coverage_gate: str = "ready",
) -> str:
    if market_coverage_gate != "ready":
        return "insufficient_market_coverage"
    if fundamental_coverage_gate != "ready":
        return "insufficient_fundamental_coverage"
    return sample_gate


def _build_strategy_validation_center(
    *,
    snapshots: list[WalkForwardSnapshot],
    portfolio: PortfolioBacktestResult,
    temporal_validation: TemporalValidationResult,
    benchmarks: list[WalkForwardBenchmarkComparison],
    cost_sensitivity: list[WalkForwardCostScenario],
    market_coverage_ratio: float,
    fundamental_coverage_ratio: float,
    metrics: WalkForwardPortfolioMetrics,
) -> WalkForwardValidationCenter:
    selection_by_key = {
        (snapshot.decision_date, item.instrument_id): item
        for snapshot in snapshots
        for item in snapshot.top_5
    }
    strategy_groups: dict[str, list[object]] = {}
    factor_groups: dict[str, list[object]] = {}
    for trade in portfolio.trades:
        selection = selection_by_key.get((trade.signal_date, trade.instrument_id))
        strategy_key = trade.strategy_id or "unknown"
        strategy_groups.setdefault(strategy_key, []).append(trade)
        if selection is not None:
            for factor in selection.factor_signals:
                factor_groups.setdefault(factor, []).append(trade)

    strategies = [
        _walk_forward_evidence_metric("strategy", key, trades)
        for key, trades in strategy_groups.items()
    ]
    factors = [
        _walk_forward_evidence_metric("factor", key, trades)
        for key, trades in factor_groups.items()
    ]
    _apply_multiple_testing_control([*strategies, *factors])
    strategies.sort(key=_evidence_sort_key)
    factors.sort(key=_evidence_sort_key)

    eligible_benchmark = next(
        (item for item in benchmarks if item.benchmark_id == ELIGIBLE_UNIVERSE_BENCHMARK_ID),
        None,
    )
    stress = next(
        (item for item in cost_sensitivity if item.key == "stress"),
        cost_sensitivity[-1] if cost_sensitivity else None,
    )
    oos = temporal_validation.out_of_sample
    criteria = [
        _gate_criterion(
            key="statistical_control",
            label="统计显著性控制",
            ready=True,
            insufficient=False,
            value="日期聚类 + FDR",
            requirement="聚类检验并控制多重比较",
        ),
        _gate_criterion(
            key="market_coverage",
            label="历史市场覆盖",
            ready=market_coverage_ratio >= MIN_FULL_MARKET_COVERAGE_RATIO,
            insufficient=market_coverage_ratio < MIN_FULL_MARKET_COVERAGE_RATIO,
            value=f"{market_coverage_ratio:.1%}",
            requirement=f">= {MIN_FULL_MARKET_COVERAGE_RATIO:.0%}",
        ),
        _gate_criterion(
            key="fundamental_coverage",
            label="历史财务覆盖",
            ready=fundamental_coverage_ratio >= MIN_FUNDAMENTAL_COVERAGE_RATIO,
            insufficient=fundamental_coverage_ratio < MIN_FUNDAMENTAL_COVERAGE_RATIO,
            value=f"{fundamental_coverage_ratio:.1%}",
            requirement=f">= {MIN_FUNDAMENTAL_COVERAGE_RATIO:.0%}",
        ),
        _gate_criterion(
            key="out_of_sample_count",
            label="样本外交易数",
            ready=bool(oos and oos.sample_count >= 30),
            insufficient=not oos or oos.sample_count < 30,
            value=str(oos.sample_count if oos else 0),
            requirement=">= 30",
        ),
        _gate_criterion(
            key="out_of_sample_return",
            label="样本外收益置信度",
            ready=temporal_validation.verdict == "positive",
            insufficient=not oos or oos.sample_count < 30,
            value=(
                f"{oos.avg_return_pct:+.2f}% / {temporal_validation.verdict}"
                if oos and oos.avg_return_pct is not None
                else "-"
            ),
            requirement="均值为正且 95% 区间不跨 0",
        ),
        _gate_criterion(
            key="benchmark_excess",
            label="历史可交易池超额",
            ready=bool(
                eligible_benchmark
                and eligible_benchmark.status == "ready"
                and (eligible_benchmark.top_5_excess_return_pct or 0) > 0
            ),
            insufficient=not eligible_benchmark or eligible_benchmark.status != "ready",
            value=(
                f"{eligible_benchmark.top_5_excess_return_pct:+.2f}%"
                if eligible_benchmark and eligible_benchmark.top_5_excess_return_pct is not None
                else "-"
            ),
            requirement="> 0%",
        ),
        _gate_criterion(
            key="cost_stress",
            label="压力成本后收益",
            ready=bool(stress and stress.top_5_return_pct > 0),
            insufficient=stress is None,
            value=f"{stress.top_5_return_pct:+.2f}%" if stress else "-",
            requirement="> 0%",
        ),
        _gate_criterion(
            key="max_drawdown",
            label="最大回撤",
            ready=metrics.max_drawdown_pct >= -15,
            insufficient=False,
            value=f"{metrics.max_drawdown_pct:+.2f}%",
            requirement=">= -15%",
        ),
    ]
    if any(item.status == "insufficient" for item in criteria):
        status = "insufficient"
        headline = "历史证据不足：继续补齐全市场覆盖和样本外交易。"
    elif any(item.status == "fail" for item in criteria):
        status = "rejected"
        headline = "历史验证未通过：已知结果存在失败项，暂不提升自动推荐权重。"
    else:
        status = "accepted"
        headline = "历史验证通过：允许进入小仓位前向模拟，不代表可直接实盘。"
    _enforce_release_gate_on_positive_evidence(
        [*strategies, *factors],
        release_status=status,
    )
    return WalkForwardValidationCenter(
        status=status,
        headline=headline,
        criteria=criteria,
        strategies=strategies,
        factors=factors,
    )


def _build_dynamic_rerank_evaluation(
    *,
    snapshots: list[WalkForwardSnapshot],
    baseline_metrics: WalkForwardPortfolioMetrics,
    baseline_temporal_validation: TemporalValidationResult,
    portfolio: PortfolioBacktestResult,
    metrics: WalkForwardPortfolioMetrics,
    temporal_validation: TemporalValidationResult,
    benchmarks: list[WalkForwardBenchmarkComparison],
    cost_sensitivity: list[WalkForwardCostScenario],
    market_coverage_ratio: float,
    fundamental_coverage_ratio: float,
) -> WalkForwardRerankEvaluation:
    changed_snapshots = [
        snapshot
        for snapshot in snapshots
        if [item.instrument_id for item in snapshot.top_5]
        != [item.instrument_id for item in snapshot.dynamic_top_5]
    ]
    promoted_selection_count = sum(
        len(
            set(item.instrument_id for item in snapshot.dynamic_top_5).difference(
                item.instrument_id for item in snapshot.top_5
            )
        )
        for snapshot in snapshots
    )
    constraint_blocked_selection_count = sum(
        item.rerank_constraint_blocked_count for item in snapshots
    )
    incomplete_index_snapshot_count = sum(
        item.rerank_incomplete_index_snapshot_count for item in snapshots
    )
    maximum_training_samples = max(
        (item.rerank_training_sample_count for item in snapshots),
        default=0,
    )
    model_ready_snapshots = sum(item.rerank_model_ready for item in snapshots)
    eligible_benchmark = next(
        (
            item
            for item in benchmarks
            if item.benchmark_id == ELIGIBLE_UNIVERSE_BENCHMARK_ID
        ),
        None,
    )
    stress = next(
        (item for item in cost_sensitivity if item.key == "stress"),
        None,
    )
    challenger_oos = temporal_validation.out_of_sample
    baseline_oos = baseline_temporal_validation.out_of_sample
    challenger_oos_return = (
        challenger_oos.avg_return_pct
        if challenger_oos and challenger_oos.avg_return_pct is not None
        else None
    )
    baseline_oos_return = (
        baseline_oos.avg_return_pct
        if baseline_oos and baseline_oos.avg_return_pct is not None
        else None
    )
    oos_improvement = (
        challenger_oos_return - baseline_oos_return
        if challenger_oos_return is not None and baseline_oos_return is not None
        else None
    )
    return_delta = round(
        metrics.total_return_pct - baseline_metrics.total_return_pct,
        4,
    )
    drawdown_delta = round(
        metrics.max_drawdown_pct - baseline_metrics.max_drawdown_pct,
        4,
    )
    criteria = [
        _gate_criterion(
            key="market_coverage",
            label="历史市场覆盖",
            ready=market_coverage_ratio >= MIN_FULL_MARKET_COVERAGE_RATIO,
            insufficient=market_coverage_ratio < MIN_FULL_MARKET_COVERAGE_RATIO,
            value=f"{market_coverage_ratio:.1%}",
            requirement=f">= {MIN_FULL_MARKET_COVERAGE_RATIO:.0%}",
        ),
        _gate_criterion(
            key="fundamental_coverage",
            label="历史财务覆盖",
            ready=fundamental_coverage_ratio >= MIN_FUNDAMENTAL_COVERAGE_RATIO,
            insufficient=fundamental_coverage_ratio < MIN_FUNDAMENTAL_COVERAGE_RATIO,
            value=f"{fundamental_coverage_ratio:.1%}",
            requirement=f">= {MIN_FUNDAMENTAL_COVERAGE_RATIO:.0%}",
        ),
        _gate_criterion(
            key="index_membership_evidence",
            label="历史指数成分完整性",
            ready=incomplete_index_snapshot_count == 0,
            insufficient=incomplete_index_snapshot_count > 0,
            value=f"{incomplete_index_snapshot_count} 个不完整快照",
            requirement="0 个不完整快照",
        ),
        _gate_criterion(
            key="resolved_training_history",
            label="可用训练历史",
            ready=(
                maximum_training_samples >= MIN_RERANK_TRAINING_SAMPLES
                and model_ready_snapshots > 0
            ),
            insufficient=(
                maximum_training_samples < MIN_RERANK_TRAINING_SAMPLES
                or model_ready_snapshots == 0
            ),
            value=f"{maximum_training_samples} 笔 / {model_ready_snapshots} 期",
            requirement=f">= {MIN_RERANK_TRAINING_SAMPLES} 笔且至少 1 期启用",
        ),
        _gate_criterion(
            key="out_of_sample_count",
            label="挑战者样本外交易",
            ready=bool(challenger_oos and challenger_oos.sample_count >= 30),
            insufficient=not challenger_oos or challenger_oos.sample_count < 30,
            value=str(challenger_oos.sample_count if challenger_oos else 0),
            requirement=">= 30",
        ),
        _gate_criterion(
            key="out_of_sample_improvement",
            label="样本外相对基线",
            ready=bool(
                challenger_oos_return is not None
                and challenger_oos_return > 0
                and oos_improvement is not None
                and oos_improvement > 0
            ),
            insufficient=(
                challenger_oos_return is None
                or baseline_oos_return is None
                or not challenger_oos
                or challenger_oos.sample_count < 30
            ),
            value=(
                f"{challenger_oos_return:+.2f}% / 较基线 {oos_improvement:+.2f}%"
                if challenger_oos_return is not None and oos_improvement is not None
                else "-"
            ),
            requirement="样本外均值 > 0 且优于固定 Top5",
        ),
        _gate_criterion(
            key="baseline_total_return",
            label="全期相对基线",
            ready=return_delta > 0,
            insufficient=False,
            value=f"{return_delta:+.2f}%",
            requirement="> 0%",
        ),
        _gate_criterion(
            key="benchmark_excess",
            label="历史可交易池超额",
            ready=bool(
                eligible_benchmark
                and eligible_benchmark.status == "ready"
                and (eligible_benchmark.dynamic_top_5_excess_return_pct or 0) > 0
            ),
            insufficient=(
                not eligible_benchmark or eligible_benchmark.status != "ready"
            ),
            value=(
                f"{eligible_benchmark.dynamic_top_5_excess_return_pct:+.2f}%"
                if eligible_benchmark
                and eligible_benchmark.dynamic_top_5_excess_return_pct is not None
                else "-"
            ),
            requirement="> 0%",
        ),
        _gate_criterion(
            key="cost_stress",
            label="压力成本后收益",
            ready=bool(
                stress
                and stress.dynamic_top_5_return_pct is not None
                and stress.dynamic_top_5_return_pct > 0
            ),
            insufficient=(
                stress is None or stress.dynamic_top_5_return_pct is None
            ),
            value=(
                f"{stress.dynamic_top_5_return_pct:+.2f}%"
                if stress and stress.dynamic_top_5_return_pct is not None
                else "-"
            ),
            requirement="> 0%",
        ),
        _gate_criterion(
            key="max_drawdown",
            label="挑战者最大回撤",
            ready=(
                metrics.max_drawdown_pct >= -15
                and drawdown_delta >= -2
            ),
            insufficient=False,
            value=(
                f"{metrics.max_drawdown_pct:+.2f}% / 较基线 {drawdown_delta:+.2f}%"
            ),
            requirement=">= -15% 且不比基线恶化 2 个百分点以上",
        ),
    ]
    if any(item.status == "insufficient" for item in criteria):
        status = "insufficient"
        headline = "动态重排序仍在积累严格按时间截断的训练与样本外证据。"
    elif any(item.status == "fail" for item in criteria):
        status = "rejected"
        headline = "动态重排序尚未稳定优于固定 Top5，继续保留为挑战者。"
    else:
        status = "accepted"
        headline = "动态重排序通过全部门槛，可以进入 20 个交易日前向模拟。"
    return WalkForwardRerankEvaluation(
        model_version=DYNAMIC_RERANKER_VERSION,
        status=status,
        headline=headline,
        leakage_guard="仅使用 exit_date 严格早于当期 decision_date 的已结束交易",
        evaluated_snapshot_count=len(snapshots),
        changed_snapshot_count=len(changed_snapshots),
        promoted_selection_count=promoted_selection_count,
        constraint_blocked_selection_count=constraint_blocked_selection_count,
        incomplete_index_snapshot_count=incomplete_index_snapshot_count,
        maximum_training_sample_count=maximum_training_samples,
        baseline_return_delta_pct=return_delta,
        baseline_max_drawdown_delta_pct=drawdown_delta,
        portfolio=portfolio,
        metrics=metrics,
        temporal_validation=temporal_validation,
        criteria=criteria,
    )


def _enforce_release_gate_on_positive_evidence(
    metrics: list[WalkForwardEvidenceMetric],
    *,
    release_status: str,
) -> None:
    if release_status == "accepted":
        return
    for metric in metrics:
        if metric.action != "increase":
            continue
        metric.action = "observe"
        metric.suggested_weight_delta = 0.0
        metric.reason = "局部样本外结果为正，但整体上线门禁尚未通过，仅观察，不调整推荐权重。"


def _walk_forward_evidence_metric(
    dimension: str,
    key: str,
    trades: list[object],
) -> WalkForwardEvidenceMetric:
    returns = [float(trade.return_pct) for trade in trades]
    validation = _trade_temporal_validation(trades)
    oos = validation.out_of_sample
    oos_count = oos.sample_count if oos else 0
    oos_trades = [
        trade
        for trade in trades
        if oos is not None and oos.start_date <= trade.signal_date <= oos.end_date
    ]
    inference = clustered_return_inference(
        [(trade.signal_date, float(trade.return_pct)) for trade in oos_trades],
        seed=_stable_inference_seed(dimension, key),
    )
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    profit_factor = round(sum(wins) / abs(sum(losses)), 4) if wins and losses else None
    average_return = round(statistics.mean(returns), 4) if returns else None
    win_rate = round(len(wins) / len(returns), 4) if returns else None
    if oos_count < 30 or inference.verdict == "insufficient":
        action = "observe"
        delta = 0.0
        reason = (
            f"样本外 {oos_count} 笔、{inference.cluster_count} 个独立调仓日，"
            "未达到 30 笔且至少 10 个调仓日的准入门槛。"
        )
    elif inference.verdict == "positive":
        action = "increase"
        delta = 0.04
        reason = "样本外日期聚类置信区间为正，待通过多重检验后再提高权重。"
    elif inference.verdict == "negative":
        action = "disable"
        delta = -0.10
        reason = "样本外日期聚类结果显著为负，停止进入可买候选，等待重新验证。"
    elif validation.verdict == "negative" or (
        oos.avg_return_pct is not None and oos.avg_return_pct <= -1
    ):
        action = "reduce"
        delta = -0.03
        reason = "样本外均值偏弱，但聚类检验尚未确认显著为负，先降权观察。"
    else:
        action = "maintain"
        delta = 0.0
        reason = "样本外聚类结果尚未形成显著优势或劣势，保持当前权重。"
    return WalkForwardEvidenceMetric(
        dimension=dimension,
        key=key,
        label=_walk_forward_evidence_label(key),
        trade_count=len(returns),
        out_of_sample_count=oos_count,
        win_rate=win_rate,
        average_return_pct=average_return,
        worst_return_pct=min(returns) if returns else None,
        profit_factor=profit_factor,
        max_consecutive_losses=_max_consecutive_losses(returns),
        out_of_sample_verdict=validation.verdict,
        statistical_method=inference.method,
        statistical_sample_count=inference.sample_count,
        statistical_cluster_count=inference.cluster_count,
        confidence_low_pct=inference.confidence_low_pct,
        confidence_high_pct=inference.confidence_high_pct,
        positive_edge_p_value=inference.positive_edge_p_value,
        negative_edge_p_value=inference.negative_edge_p_value,
        statistical_verdict=inference.verdict,
        action=action,
        suggested_weight_delta=delta,
        reason=reason,
    )


def _apply_multiple_testing_control(metrics: list[WalkForwardEvidenceMetric]) -> None:
    adjusted = benjamini_hochberg(
        [
            item.positive_edge_p_value
            if item.statistical_verdict != "insufficient"
            else None
            for item in metrics
        ]
    )
    for metric, false_discovery_rate in zip(metrics, adjusted, strict=True):
        metric.false_discovery_rate = false_discovery_rate
        if metric.action != "increase":
            continue
        if false_discovery_rate is not None and false_discovery_rate <= 0.10:
            metric.reason = (
                "样本外日期聚类结果为正，且多重检验后的 FDR 不高于 10%，"
                "可在整体上线门禁通过后小幅提高权重。"
            )
            continue
        metric.action = "observe"
        metric.suggested_weight_delta = 0.0
        metric.reason = "局部结果为正，但未通过 10% FDR 多重检验，仅观察。"


def _stable_inference_seed(dimension: str, key: str) -> int:
    payload = f"{dimension}:{key}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:4], "big")


def _gate_criterion(
    *,
    key: str,
    label: str,
    ready: bool,
    insufficient: bool,
    value: str,
    requirement: str,
) -> WalkForwardGateCriterion:
    status = "pass" if ready else "insufficient" if insufficient else "fail"
    return WalkForwardGateCriterion(
        key=key,
        label=label,
        status=status,
        value=value,
        requirement=requirement,
    )


def _evidence_sort_key(item: WalkForwardEvidenceMetric) -> tuple[int, int, str]:
    action_rank = {"disable": 0, "reduce": 1, "increase": 2, "maintain": 3, "observe": 4}
    return (action_rank.get(item.action, 9), -item.trade_count, item.key)


def _walk_forward_evidence_label(key: str) -> str:
    return {
        "trend_momentum_stage2": "二阶段趋势动量",
        "breakout_volume_confirmation": "放量突破确认",
        "factor_rotation_watch": "因子轮动观察",
        "healthy_pullback": "健康回调",
        "short_squeeze_risk": "逼空风险监控",
        "momentum": "动量",
        "trend_quality": "趋势质量",
        "liquidity": "流动性",
        "low_risk": "低波动",
        "reversal": "反转/回踩",
        "valuation": "估值",
        "quality": "质量",
        "size": "市值",
        "high_volatility": "高波动",
        "overextended": "短线过热",
        "insufficient_history": "历史不足",
        "low_liquidity": "流动性偏弱",
        "unknown": "未分类策略",
    }.get(key, key)


def _report_progress(
    callback: Callable[[WalkForwardProgress], None] | None,
    *,
    phase: str,
    processed_snapshots: int,
    total_snapshots: int,
    current_date: date | None = None,
    snapshot: WalkForwardSnapshot | None = None,
    lease_heartbeat: _DatasetLeaseHeartbeat | None = None,
) -> None:
    if callback is None:
        return
    maintenance_count = 0
    recovery_count = 0
    last_heartbeat_at = None
    if lease_heartbeat is not None:
        maintenance_count, recovery_count, last_heartbeat_at = lease_heartbeat.telemetry()
    callback(
        WalkForwardProgress(
            phase=phase,
            processed_snapshots=processed_snapshots,
            total_snapshots=total_snapshots,
            current_date=current_date,
            snapshot=snapshot,
            lease_maintenance_count=maintenance_count,
            lease_recovery_count=recovery_count,
            last_lease_heartbeat_at=last_heartbeat_at,
        )
    )


def _selection(card) -> WalkForwardSelection:
    context = card.market_context
    return WalkForwardSelection(
        instrument_id=card.instrument_id,
        status=card.status.value,
        primary_strategy_id=card.primary_strategy_id,
        rank_score=Decimal(str(card.rank_score)),
        trigger_price=card.entry_plan.trigger_price,
        initial_stop=card.exit_plan.initial_stop,
        target_1=card.exit_plan.target_1,
        no_chase_above=card.entry_plan.no_chase_above,
        factor_signals=_selection_factor_signals(card),
        asset_type=card.asset_type,
        industry=context.industry if context else None,
        index_memberships=(
            sorted(set(context.index_memberships)) if context else []
        ),
    )


def _selection_factor_signals(card) -> list[str]:
    signals = [str(value) for value in card.factor_flags if value]
    for exposure in card.factor_exposures:
        if exposure.score >= 0.65:
            signals.append(exposure.factor_id)
    return sorted(set(signals))


def _enrich_selection_constraints(
    snapshots: list[WalkForwardSnapshot],
    *,
    repository: ReplayEvidenceRepository,
    revision: int,
    asset_types: dict[str, str],
) -> list[WalkForwardSnapshot]:
    enriched = []
    for snapshot in snapshots:
        instrument_ids = [item.instrument_id for item in snapshot.top_10]
        industries = repository.industries_as_of(
            instrument_ids,
            snapshot.decision_date,
            revision,
        )
        memberships, incomplete_index_snapshots = (
            repository.available_memberships_as_of(
            instrument_ids,
            snapshot.decision_date,
            revision,
        )
        )
        updated_by_instrument = {}
        for selection in snapshot.top_10:
            industry_snapshot = industries.get(selection.instrument_id)
            membership_rows = memberships.get(selection.instrument_id, [])
            asset_type = selection.asset_type
            if asset_type == "unknown":
                asset_type = asset_types.get(selection.instrument_id, "unknown")
            if asset_type == "1":
                asset_type = "stock"
            updated_by_instrument[selection.instrument_id] = selection.model_copy(
                update={
                    "asset_type": asset_type,
                    "industry": (
                        selection.industry
                        or (
                            industry_snapshot.industry
                            if industry_snapshot is not None
                            else None
                        )
                    ),
                    "index_memberships": (
                        selection.index_memberships
                        or sorted(
                            {
                                item.index_id
                                for item in membership_rows
                            }
                        )
                    ),
                }
            )
        enriched.append(
            snapshot.model_copy(
                update={
                    "top_10": [
                        updated_by_instrument[item.instrument_id]
                        for item in snapshot.top_10
                    ],
                    "top_5": [
                        updated_by_instrument.get(item.instrument_id, item)
                        for item in snapshot.top_5
                    ],
                    "rerank_incomplete_index_snapshot_count": len(
                        incomplete_index_snapshots
                    ),
                }
            )
        )
    return enriched


def _apply_dynamic_reranking(
    snapshots: list[WalkForwardSnapshot],
    *,
    top_10_portfolio: PortfolioBacktestResult,
) -> list[WalkForwardSnapshot]:
    selection_by_key = {
        (snapshot.decision_date, item.instrument_id): item
        for snapshot in snapshots
        for item in snapshot.top_10
    }
    regime_by_date = {
        snapshot.decision_date: snapshot.benchmark_trend_state
        for snapshot in snapshots
    }
    observations = []
    for trade in top_10_portfolio.trades:
        selection = selection_by_key.get((trade.signal_date, trade.instrument_id))
        observations.append(
            ResolvedRerankObservation(
                instrument_id=trade.instrument_id,
                signal_date=trade.signal_date,
                exit_date=trade.exit_date,
                return_pct=trade.return_pct,
                primary_strategy_id=trade.strategy_id,
                factor_signals=(
                    selection.factor_signals if selection is not None else []
                ),
                market_regime=regime_by_date.get(
                    trade.signal_date,
                    "unknown",
                ),
            )
        )

    reranked_snapshots = []
    for snapshot in snapshots:
        decision = rerank_candidates(
            [
                RerankCandidate(
                    instrument_id=item.instrument_id,
                    baseline_rank_score=float(item.rank_score),
                    primary_strategy_id=item.primary_strategy_id,
                    factor_signals=item.factor_signals,
                    market_regime=snapshot.benchmark_trend_state,
                )
                for item in snapshot.top_10
            ],
            observations,
            decision_date=snapshot.decision_date,
        )
        source_by_instrument = {
            item.instrument_id: item for item in snapshot.top_10
        }
        constrained_scores, constraint_blocked_count = (
            _select_constrained_dynamic_scores(
                decision.candidates,
                source_by_instrument=source_by_instrument,
                limit=5,
            )
        )
        dynamic_selections = []
        for score in constrained_scores:
            source = source_by_instrument[score.instrument_id]
            dynamic_selections.append(
                source.model_copy(
                    update={
                        "rerank_score": score.rerank_score,
                        "rerank_baseline_position": score.baseline_position,
                        "rerank_position": score.rerank_position,
                        "rerank_training_samples": score.training_sample_count,
                        "rerank_expected_return_pct": score.expected_return_pct,
                        "rerank_win_probability": score.win_probability,
                        "rerank_reason": score.reason,
                    }
                )
            )
        reranked_snapshots.append(
            snapshot.model_copy(
                update={
                    "dynamic_top_5": dynamic_selections,
                    "rerank_training_cutoff_date": decision.training_cutoff_date,
                    "rerank_training_sample_count": decision.training_sample_count,
                    "rerank_model_ready": decision.model_ready,
                    "rerank_constraint_blocked_count": (
                        constraint_blocked_count
                    ),
                    "rerank_incomplete_index_snapshot_count": (
                        snapshot.rerank_incomplete_index_snapshot_count
                    ),
                }
            )
        )
    return reranked_snapshots


def _select_constrained_dynamic_scores(
    scores: list[RerankCandidateScore],
    *,
    source_by_instrument: dict[str, WalkForwardSelection],
    limit: int,
) -> tuple[list[RerankCandidateScore], int]:
    selected = []
    industry_counts: dict[str, int] = {}
    etf_overlap_counts: dict[str, int] = {}
    blocked = 0
    for score in scores:
        source = source_by_instrument[score.instrument_id]
        industry = (source.industry or "").strip()
        constrained_industry = (
            industry
            if industry
            and industry.lower() not in {"unknown", "综合", "指数etf", "etf"}
            else None
        )
        if (
            constrained_industry is not None
            and industry_counts.get(constrained_industry, 0) >= 2
        ):
            blocked += 1
            continue
        is_etf = source.asset_type.lower() in {"etf", "fund", "index_fund"}
        overlap_keys = source.index_memberships if is_etf else []
        if any(etf_overlap_counts.get(key, 0) >= 1 for key in overlap_keys):
            blocked += 1
            continue
        selected.append(score)
        if constrained_industry is not None:
            industry_counts[constrained_industry] = (
                industry_counts.get(constrained_industry, 0) + 1
            )
        for key in overlap_keys:
            etf_overlap_counts[key] = etf_overlap_counts.get(key, 0) + 1
        if len(selected) >= limit:
            break
    return selected, blocked


def _signals(
    snapshots: list[WalkForwardSnapshot],
    *,
    size: int,
    selection_source: str = "baseline",
) -> list[BacktestSignal]:
    result = []
    for snapshot in snapshots:
        if selection_source == "dynamic":
            selections = snapshot.dynamic_top_5
        else:
            selections = snapshot.top_5 if size == 5 else snapshot.top_10
        result.extend(
            BacktestSignal(
                snapshot_id=(
                    f"walk-forward-{selection_source}-{size}-"
                    f"{snapshot.decision_date:%Y%m%d}:{item.instrument_id}"
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
                no_chase_above=item.no_chase_above,
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
    strategy_validation: WalkForwardValidationCenter,
    dynamic_rerank: WalkForwardRerankEvaluation,
) -> str:
    payload = {
        "dataset_revision": revision,
        "snapshots": [item.model_dump(mode="json") for item in snapshots],
        "top_5_portfolio": top_5_portfolio.model_dump(mode="json"),
        "top_10_portfolio": top_10_portfolio.model_dump(mode="json"),
        "top_5_temporal_validation": _trade_temporal_validation(top_5_portfolio.trades).model_dump(
            mode="json"
        ),
        "top_10_temporal_validation": _trade_temporal_validation(
            top_10_portfolio.trades
        ).model_dump(mode="json"),
        "benchmarks": [item.model_dump(mode="json") for item in benchmarks],
        "cost_sensitivity": [item.model_dump(mode="json") for item in cost_sensitivity],
        "strategy_validation": strategy_validation.model_dump(mode="json"),
        "dynamic_rerank": dynamic_rerank.model_dump(mode="json"),
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
    dynamic_top_5_signals: list[BacktestSignal],
    top_5_portfolio: PortfolioBacktestResult,
    top_10_portfolio: PortfolioBacktestResult,
    dynamic_top_5_portfolio: PortfolioBacktestResult,
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
            dynamic_top_5 = dynamic_top_5_portfolio
        else:
            top_5 = run_signal_portfolio_backtest(
                signals=top_5_signals,
                instrument_ids=sorted({item.instrument_id for item in top_5_signals}),
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
                instrument_ids=sorted({item.instrument_id for item in top_10_signals}),
                provider=market_provider,
                start=start,
                end=end,
                max_positions=10,
                slippage_bps=slippage_bps,
                fee_multiplier=fee_multiplier,
                execution_rule_resolver=execution_resolver,
            )
            dynamic_top_5 = run_signal_portfolio_backtest(
                signals=dynamic_top_5_signals,
                instrument_ids=sorted(
                    {item.instrument_id for item in dynamic_top_5_signals}
                ),
                provider=market_provider,
                start=start,
                end=end,
                max_positions=5,
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
                top_5_total_costs=sum((item.costs for item in top_5.trades), Decimal("0")),
                top_10_total_costs=sum((item.costs for item in top_10.trades), Decimal("0")),
                dynamic_top_5_return_pct=dynamic_top_5.summary.total_return_pct,
                dynamic_top_5_max_drawdown_pct=(
                    dynamic_top_5.summary.max_drawdown_pct
                ),
                dynamic_top_5_total_costs=sum(
                    (item.costs for item in dynamic_top_5.trades),
                    Decimal("0"),
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
    turnover = (
        sum(float(item.entry_price * item.shares) for item in trades)
        / float(portfolio.summary.initial_capital)
        * 100
    )
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
    dynamic_top_5_return: float,
    eligible_universes: list[tuple[date, list[str]]],
) -> list[WalkForwardBenchmarkComparison]:
    bars = provider.get_daily_bars(list(REQUIRED_BENCHMARK_IDS), start, end)
    comparisons = []
    for benchmark_id in REQUIRED_BENCHMARK_IDS:
        frame = (
            bars.loc[bars["instrument_id"] == benchmark_id].sort_values("trade_date")
            if not bars.empty
            else bars
        )
        if frame.empty:
            comparisons.append(
                WalkForwardBenchmarkComparison(
                    benchmark_id=benchmark_id,
                    status="missing",
                    benchmark_return_pct=None,
                    top_5_excess_return_pct=None,
                    top_10_excess_return_pct=None,
                    dynamic_top_5_excess_return_pct=None,
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
                dynamic_top_5_excess_return_pct=(
                    round(dynamic_top_5_return - benchmark_return, 4)
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
            dynamic_top_5_excess_return_pct=(
                round(dynamic_top_5_return - equal_weight_return, 4)
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
        {instrument_id for _, members in eligible_universes for instrument_id in members}
    )
    if not instrument_ids:
        return None
    first_date = eligible_universes[0][0]
    stream_loader = getattr(provider, "iter_adjusted_closes", None)
    if callable(stream_loader):
        return _equal_weight_eligible_return_from_stream(
            stream_loader(instrument_ids, first_date, end),
            eligible_universes,
            end=end,
        )
    bars = provider.get_daily_bars(instrument_ids, first_date, end)
    if bars.empty:
        return None
    price_series = {}
    for instrument_id, frame in bars.groupby("instrument_id", sort=False):
        ordered = frame.sort_values("trade_date")
        price_series[instrument_id] = (
            ordered["trade_date"].tolist(),
            ordered["adjusted_close"].astype(float).tolist(),
        )
    compounded = 1.0
    completed_periods = 0
    for index, (decision_date, members) in enumerate(eligible_universes):
        period_end = (
            eligible_universes[index + 1][0] if index + 1 < len(eligible_universes) else end
        )
        if period_end <= decision_date:
            continue
        returns = []
        for instrument_id in members:
            series = price_series.get(instrument_id)
            if series is None:
                continue
            dates, closes = series
            first_index = bisect_right(dates, decision_date)
            final_index = bisect_right(dates, period_end) - 1
            if final_index - first_index < 1:
                continue
            first = closes[first_index]
            last = closes[final_index]
            if first > 0:
                returns.append(last / first - 1)
        if not returns:
            continue
        compounded *= 1 + statistics.mean(returns)
        completed_periods += 1
    return round((compounded - 1) * 100, 4) if completed_periods else None


def _equal_weight_eligible_return_from_stream(
    rows,
    eligible_universes: list[tuple[date, list[str]]],
    *,
    end: date,
) -> float | None:
    decision_dates = [decision_date for decision_date, _ in eligible_universes]
    period_ends = [*decision_dates[1:], end]
    member_sets = [set(members) for _, members in eligible_universes]
    return_sums = [0.0] * len(eligible_universes)
    return_counts = [0] * len(eligible_universes)
    current_instrument: str | None = None
    period_prices: dict[int, tuple[float, float, int]] = {}

    def flush_instrument() -> None:
        for period_index, (first_close, last_close, count) in period_prices.items():
            if count < 2 or first_close <= 0:
                continue
            return_sums[period_index] += last_close / first_close - 1
            return_counts[period_index] += 1

    for instrument_id, trade_date, adjusted_close in rows:
        if instrument_id != current_instrument:
            if current_instrument is not None:
                flush_instrument()
            current_instrument = instrument_id
            period_prices = {}
        period_index = bisect_left(decision_dates, trade_date) - 1
        if (
            period_index < 0
            or trade_date > period_ends[period_index]
            or instrument_id not in member_sets[period_index]
        ):
            continue
        previous = period_prices.get(period_index)
        if previous is None:
            period_prices[period_index] = (
                adjusted_close,
                adjusted_close,
                1,
            )
        else:
            period_prices[period_index] = (
                previous[0],
                adjusted_close,
                previous[2] + 1,
            )
    if current_instrument is not None:
        flush_instrument()

    compounded = 1.0
    completed_periods = 0
    for total_return, sample_count in zip(
        return_sums,
        return_counts,
        strict=True,
    ):
        if sample_count <= 0:
            continue
        compounded *= 1 + total_return / sample_count
        completed_periods += 1
    return round((compounded - 1) * 100, 4) if completed_periods else None
