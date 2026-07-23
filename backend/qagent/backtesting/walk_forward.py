from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
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
from qagent.backtesting.statistical_validation import (
    benjamini_hochberg,
    clustered_return_inference,
)
from qagent.backtesting.temporal_validation import (
    TemporalValidationResult,
    build_temporal_validation,
)
from qagent.jobs.daily_scan import run_daily_scan
from qagent.market.astock_enhanced import EmptyAShareEnhancedDataProvider
from qagent.market.calendars import trading_sessions_in_range
from qagent.historical_evidence.providers import REQUIRED_BENCHMARK_IDS
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
    factor_signals: list[str] = Field(default_factory=list)


class WalkForwardSnapshot(BaseModel):
    decision_date: date
    historical_universe_size: int
    eligible_size: int
    suspended_count: int
    st_excluded_count: int
    missing_tradability_count: int
    fundamental_universe_size: int = 0
    fundamental_covered_count: int = 0
    top_5: list[WalkForwardSelection] = Field(default_factory=list)
    top_10: list[WalkForwardSelection] = Field(default_factory=list)


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
    lease_maintenance_count: int = 0
    lease_recovery_count: int = 0
    last_lease_heartbeat_at: datetime | None = None


class _WalkForwardSnapshotInput(BaseModel):
    decision_date: date
    historical_universe_size: int
    eligible: list[str]
    eligible_stocks: list[str]
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
    decision_date = snapshot_input.decision_date
    fundamental_evidence = repository.fundamentals_as_of(
        snapshot_input.eligible_stocks,
        decision_date,
        market_provider.revision,
    )
    fundamental_covered_count = sum(
        _has_usable_fundamental(item) for item in fundamental_evidence.values()
    )
    window_start = decision_date - timedelta(days=lookback_days)
    market_provider.prefetch_daily_bars(
        snapshot_input.eligible,
        window_start,
        decision_date,
    )
    strategy_provider.prefetch_fundamentals(
        snapshot_input.eligible,
        decision_date,
        snapshots=fundamental_evidence,
    )
    scan = run_daily_scan(
        snapshot_input.eligible,
        market_provider,
        mode="historical_replay",
        strategy_data_provider=strategy_provider,
        a_share_enhanced_provider=EmptyAShareEnhancedDataProvider(),
        start=window_start,
        end=decision_date,
    )
    errors = [item.reason for item in scan.items if item.status == "error"]
    selections = [
        _selection(card)
        for card in scan.cards
        if card.status.value not in EXCLUDED_STATUSES
    ]
    return _WalkForwardWorkerResult(
        worker_pid=os.getpid(),
        snapshot=WalkForwardSnapshot(
            decision_date=decision_date,
            historical_universe_size=snapshot_input.historical_universe_size,
            eligible_size=len(snapshot_input.eligible),
            suspended_count=snapshot_input.suspended_count,
            st_excluded_count=snapshot_input.st_excluded_count,
            missing_tradability_count=snapshot_input.missing_tradability_count,
            fundamental_universe_size=len(snapshot_input.eligible_stocks),
            fundamental_covered_count=fundamental_covered_count,
            top_5=selections[:5],
            top_10=selections[:10],
        ),
        scan_error_count=len(errors),
        scan_error_samples=errors[:3],
        stats=_snapshot_worker_stats(market_provider, strategy_provider),
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
                eligible_universes.append((decision_date, eligible))
                stock_ids = {
                    item.instrument_id
                    for item in members
                    if item.security_type in {"stock", "1"}
                }
                eligible_stocks = [item for item in eligible if item in stock_ids]
                snapshot_inputs.append(
                    _WalkForwardSnapshotInput(
                        decision_date=decision_date,
                        historical_universe_size=len(instrument_ids),
                        eligible=eligible,
                        eligible_stocks=eligible_stocks,
                        suspended_count=suspended_count,
                        st_excluded_count=st_excluded_count,
                        missing_tradability_count=missing_tradability_count,
                    )
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
        top_5_metrics = _portfolio_metrics(top_5_portfolio, start, end)
        top_10_metrics = _portfolio_metrics(top_10_portfolio, start, end)
        top_5_temporal_validation = _trade_temporal_validation(top_5_portfolio.trades)
        top_10_temporal_validation = _trade_temporal_validation(top_10_portfolio.trades)
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
    digest = _selection_digest(
        snapshots,
        revision,
        top_5_portfolio,
        top_10_portfolio,
        benchmarks,
        cost_sensitivity,
        strategy_validation,
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
    return WalkForwardSelection(
        instrument_id=card.instrument_id,
        status=card.status.value,
        primary_strategy_id=card.primary_strategy_id,
        rank_score=Decimal(str(card.rank_score)),
        trigger_price=card.entry_plan.trigger_price,
        initial_stop=card.exit_plan.initial_stop,
        target_1=card.exit_plan.target_1,
        factor_signals=_selection_factor_signals(card),
    )


def _selection_factor_signals(card) -> list[str]:
    signals = [str(value) for value in card.factor_flags if value]
    for exposure in card.factor_exposures:
        if exposure.score >= 0.65:
            signals.append(exposure.factor_id)
    return sorted(set(signals))


def _signals(snapshots: list[WalkForwardSnapshot], *, size: int) -> list[BacktestSignal]:
    result = []
    for snapshot in snapshots:
        selections = snapshot.top_5 if size == 5 else snapshot.top_10
        result.extend(
            BacktestSignal(
                snapshot_id=(
                    f"walk-forward-{size}-{snapshot.decision_date:%Y%m%d}:{item.instrument_id}"
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
    strategy_validation: WalkForwardValidationCenter,
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
        {instrument_id for _, members in eligible_universes for instrument_id in members}
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
            eligible_universes[index + 1][0] if index + 1 < len(eligible_universes) else end
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
