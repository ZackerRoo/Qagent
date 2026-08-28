from __future__ import annotations

import gc
import hashlib
import hmac
import json
import math
import os
import statistics
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import date, datetime, timedelta
from decimal import Decimal
from multiprocessing import get_context
from threading import Event, Lock, Thread
from types import SimpleNamespace
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from qagent.backtesting.engine import BacktestSignal
from qagent.backtesting.experiment import (
    WalkForwardExperimentManifest,
    build_walk_forward_experiment_manifest,
    walk_forward_manifest_digest_is_valid,
)
from qagent.backtesting.execution import VersionedAshareExecutionResolver
from qagent.backtesting.baseline_challenger import (
    BASELINE_CHALLENGER_VERSION,
    BASELINE_REPLACEMENT_EXCESS_MARGIN_PCT,
    BASELINE_REPLACEMENT_SCORE_MARGIN,
    MIN_BASELINE_TRAINING_SAMPLES,
    BaselineCandidate,
    BaselineCandidateScore,
    ResolvedBaselineObservation,
    score_baseline_candidates,
)
from qagent.backtesting.portfolio import (
    ADAPTIVE_CONFIRMATION_EXECUTION_PROFILE,
    ADAPTIVE_EXECUTION_PROFILE,
    CandidateOutcomeLedgerResult,
    CandidateOutcomeStatus,
    PortfolioBacktestResult,
    resolve_candidate_outcome_ledger,
    run_signal_portfolio_backtest,
)
from qagent.backtesting.ranking_v3 import (
    RankingV3Candidate,
    RankingV3CandidateScore,
    RankingV3Decision,
    RankingV3FeatureVector,
    RankingV3FrozenScoringArtifact,
    ResolvedRankingV3Observation,
    build_ranking_v3_frozen_scoring_artifact,
    score_ranking_v3_candidates,
)
from qagent.backtesting.ranking_v3_pbo import (
    RankingV3DatedModelReturn,
    evaluate_ranking_v3_cscv_pbo,
)
from qagent.backtesting.ranking_v3_protocol import (
    RANKING_V3_CANDIDATE_BENCHMARK_IDS,
    RANKING_V3_CANDIDATE_POOL_LIMIT,
    RANKING_V3_EMBARGO_SESSIONS,
    RANKING_V3_ENTRY_WAIT_SESSIONS,
    RANKING_V3_HISTORICAL_AUDIT_END,
    RANKING_V3_HISTORICAL_AUDIT_START,
    RANKING_V3_HOLDING_SESSIONS,
    RANKING_V3_HISTORICAL_PORTFOLIO_BENCHMARK_ID,
    RANKING_V3_MAX_PER_STRATEGY,
    RANKING_V3_MAX_POSITIONS,
    RANKING_V3_MODEL_VERSION,
    RankingV3Protocol,
    build_ranking_v3_protocol,
)
from qagent.backtesting.ranking_v3_validation import (
    RankingV3ReturnObservation,
    RankingV3ValidationEvaluation,
    evaluate_ranking_v3_validation,
)
from qagent.backtesting.ranking_v4 import (
    RankingV4Candidate,
    RankingV4CandidateScore,
    RankingV4Decision,
    RankingV4FeatureVector,
    ResolvedRankingV4Observation,
    score_ranking_v4_candidates,
)
from qagent.backtesting.ranking_v4_candidates import (
    RankingV4CandidatePoolEntry,
    build_preregistered_ranking_v4_candidate_pool,
)
from qagent.backtesting.ranking_v4_portfolio import (
    RankingV4PortfolioDecision,
    select_ranking_v4_portfolio,
)
from qagent.backtesting.ranking_v4_pbo import (
    RANKING_V4_FROZEN_PBO_MODEL_IDS,
    RankingV4DatedModelReturn,
    evaluate_ranking_v4_cscv_pbo,
)
from qagent.backtesting.ranking_v4_protocol import (
    RANKING_V4_MODEL_VERSION,
    RankingV4Protocol,
    build_ranking_v4_protocol,
)
from qagent.backtesting.ranking_v4_validation import (
    RankingV4HistoricalValidationEvaluation,
    RankingV4ReturnObservation,
    RankingV4TrialLedgerEvidence,
    build_ranking_v4_trial_ledger,
    evaluate_ranking_v4_historical_validation,
)
from qagent.backtesting.replay_provider import (
    ReplayMarketDataProvider,
    ReplayStrategyDataProvider,
)
from qagent.backtesting.reranking import (
    DYNAMIC_RERANKER_VERSION,
    MIN_RERANK_TRAINING_SAMPLES,
    RERANK_PROMOTION_MARGIN,
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
from qagent.market.calendars import trading_day_offset, trading_sessions_in_range
from qagent.market import instruments as market_instruments
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
ELIGIBLE_UNIVERSE_BENCHMARK_ID = RANKING_V3_HISTORICAL_PORTFOLIO_BENCHMARK_ID
MIN_FULL_MARKET_COVERAGE_RATIO = 0.90
MIN_FUNDAMENTAL_COVERAGE_RATIO = 0.80
PREFILTER_LOOKBACK_DAYS = 220
PREFILTER_CANDIDATE_LIMIT = 300
PREFILTER_NON_STOCK_RESERVE_RATIO = 0.20
RANKING_V3_VALID_CASH_DETAILS = frozenset(
    {
        "entry_not_triggered",
        "entry_fill_outside_plan",
    }
)
RANKING_V4_VALID_CASH_DETAILS = frozenset(
    {
        "entry_not_triggered",
        "entry_fill_outside_plan",
    }
)
RANKING_V4_RISK_BENCHMARK_ID = "CN:000300.IDX"
RANKING_V4_RISK_LOOKBACK_SESSIONS = 120
RANKING_V4_MIN_COMMON_RETURN_OBSERVATIONS = 60
RANKING_V4_FULL_MODEL_ID = RANKING_V4_FROZEN_PBO_MODEL_IDS[1]
WALK_FORWARD_RESULT_DIGEST_SCHEMA = "walk-forward-result-digest-v2"
WALK_FORWARD_RESULT_DIGEST_PREFIX = "v2"


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
    themes: list[str] = Field(default_factory=list)
    index_memberships: list[str] = Field(default_factory=list)
    underlying_ids: list[str] = Field(default_factory=list)
    ranking_v4_fundamental_as_of: date | None = None
    ranking_v4_industry_snapshot_date: date | None = None
    ranking_v4_index_membership_snapshot_date: date | None = None
    ranking_v4_bar_end_date: date | None = None
    ranking_v4_cost_effective_date: date | None = None
    ranking_features: RankingV3FeatureVector = Field(default_factory=RankingV3FeatureVector)
    ranking_v4_features: RankingV4FeatureVector = Field(default_factory=RankingV4FeatureVector)
    ranking_v4_primary_channel: str | None = None
    ranking_v4_matched_channels: list[str] = Field(default_factory=list)
    ranking_v4_channel_scores: dict[str, float] = Field(default_factory=dict)
    ranking_v4_score: float | None = None
    ranking_v4_position: int | None = None
    ranking_v4_training_dates: int = 0
    ranking_v4_trigger_probability: float | None = None
    ranking_v4_expected_net_excess_pct: float | None = None
    ranking_v4_net_excess_lower_bound_pct: float | None = None
    ranking_v4_expected_utility_pct: float | None = None
    ranking_v4_utility_lower_bound_pct: float | None = None
    ranking_v4_blocked_reasons: list[str] = Field(default_factory=list)
    ranking_v4_reason: str = ""
    ranking_v4_beta: float | None = None
    ranking_v4_return_observation_count: int = 0
    ranking_v4_temporal_evidence_complete: bool = False
    ranking_v4_constraint_data_complete: bool = False
    ranking_v4_constraint_evidence_mode: str = "incomplete"
    ranking_v4_fundamental_evidence_complete: bool = False
    ranking_v4_industry_evidence_complete: bool = False
    ranking_v4_underlying_evidence_complete: bool = False
    ranking_v4_underlying_evidence_mode: str = "unknown"
    ranking_v4_raw_metadata_complete: bool = False
    ranking_v4_return_risk_evidence_complete: bool = False
    ranking_v4_combined_constraint_evidence_complete: bool = False
    ranking_v4_first_blocked_reason: str | None = None
    ranking_v4_replacement_cost_pct: float | None = None
    ranking_v4_benchmark_opportunity_cost_pct: float | None = None
    ranking_v4_liquidity_penalty_pct: float | None = None
    ranking_v4_tail_risk_penalty_pct: float | None = None
    ranking_v3_score: float | None = None
    ranking_v3_position: int | None = None
    ranking_v3_training_dates: int = 0
    ranking_v3_expected_net_excess_pct: float | None = None
    ranking_v3_net_excess_lower_bound_pct: float | None = None
    ranking_v3_trigger_probability: float | None = None
    ranking_v3_reason: str = ""
    rerank_score: float | None = None
    rerank_baseline_position: int | None = None
    rerank_position: int | None = None
    rerank_training_samples: int = 0
    rerank_expected_return_pct: float | None = None
    rerank_expected_net_return_pct: float | None = None
    rerank_expected_return_lower_bound_pct: float | None = None
    rerank_win_probability: float | None = None
    rerank_win_probability_lower_bound: float | None = None
    rerank_promotion_eligible: bool = False
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
    ranking_v4_market_breadth: float | None = None
    ranking_v4_benchmark_slope: float | None = None
    ranking_v4_realized_volatility: float | None = None
    ranking_v4_cross_sectional_dispersion: float | None = None
    ranking_v4_market_features_complete: bool = False
    strategy_diversification_limit: int = 2
    strategy_diversified_count: int = 0
    candidate_pool: list[WalkForwardSelection] = Field(default_factory=list)
    top_5: list[WalkForwardSelection] = Field(default_factory=list)
    top_10: list[WalkForwardSelection] = Field(default_factory=list)
    constraint_matched_baseline_top_5: list[WalkForwardSelection] = Field(default_factory=list)
    ranking_v3_top_5: list[WalkForwardSelection] = Field(default_factory=list)
    ranking_v4_candidate_pool: list[WalkForwardSelection] = Field(default_factory=list)
    ranking_v4_constraint_matched_baseline_top_5: list[WalkForwardSelection] = Field(
        default_factory=list
    )
    ranking_v4_top_5: list[WalkForwardSelection] = Field(default_factory=list)
    dynamic_top_5: list[WalkForwardSelection] = Field(default_factory=list)
    baseline_challenger_top_5: list[WalkForwardSelection] = Field(default_factory=list)
    rerank_training_cutoff_date: date | None = None
    rerank_training_sample_count: int = 0
    rerank_model_ready: bool = False
    rerank_constraint_blocked_count: int = 0
    rerank_evidence_blocked_count: int = 0
    rerank_hysteresis_blocked_count: int = 0
    rerank_incomplete_index_snapshot_count: int = 0
    baseline_challenger_training_cutoff_date: date | None = None
    baseline_challenger_training_sample_count: int = 0
    baseline_challenger_model_ready: bool = False
    baseline_challenger_cash_slots: int = 0
    baseline_challenger_retained_count: int = 0
    baseline_challenger_evidence_blocked_count: int = 0
    baseline_challenger_hysteresis_blocked_count: int = 0
    baseline_challenger_constraint_blocked_count: int = 0
    ranking_v3_training_cutoff_date: date | None = None
    ranking_v3_training_observation_count: int = 0
    ranking_v3_training_date_count: int = 0
    ranking_v3_model_ready: bool = False
    ranking_v3_constraint_blocked_count: int = 0
    ranking_v4_training_cutoff_date: date | None = None
    ranking_v4_training_observation_count: int = 0
    ranking_v4_training_date_count: int = 0
    ranking_v4_model_ready: bool = False
    ranking_v4_cash_slot_count: int = 5
    ranking_v4_constraint_blocked_count: int = 0
    ranking_v4_portfolio: RankingV4PortfolioDecision | None = None
    ranking_v4_pairwise_correlations: dict[str, float] = Field(default_factory=dict)
    ranking_v4_evidence_coverage: dict[str, float] = Field(default_factory=dict)
    ranking_v4_blocked_reason_counts: dict[str, int] = Field(default_factory=dict)
    ranking_v4_first_blocked_reason_counts: dict[str, int] = Field(default_factory=dict)
    ranking_v4_gate_reconciliation: dict[str, int] = Field(default_factory=dict)


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
    evidence_blocked_selection_count: int = 0
    hysteresis_blocked_selection_count: int = 0
    incomplete_index_snapshot_count: int
    maximum_training_sample_count: int
    baseline_return_delta_pct: float
    baseline_max_drawdown_delta_pct: float
    portfolio: PortfolioBacktestResult
    metrics: "WalkForwardPortfolioMetrics"
    temporal_validation: TemporalValidationResult
    criteria: list[WalkForwardGateCriterion] = Field(default_factory=list)


class WalkForwardLossAttribution(BaseModel):
    dimension: str
    key: str
    label: str
    trade_count: int
    win_rate: float | None
    average_return_pct: float
    average_benchmark_return_pct: float
    average_net_excess_return_pct: float
    worst_net_excess_return_pct: float


class _EqualWeightBenchmarkResult(BaseModel):
    return_pct: float | None
    expected_member_observations: int
    priced_member_observations: int
    member_coverage_ratio: float


class WalkForwardBaselineChallengerEvaluation(BaseModel):
    model_version: str
    status: str
    headline: str
    leakage_guard: str
    evaluated_snapshot_count: int
    model_ready_snapshot_count: int
    changed_snapshot_count: int
    cash_snapshot_count: int
    average_positions: float
    retained_selection_count: int
    evidence_blocked_selection_count: int
    hysteresis_blocked_selection_count: int
    constraint_blocked_selection_count: int
    maximum_training_sample_count: int
    baseline_return_delta_pct: float
    baseline_max_drawdown_delta_pct: float
    baseline_turnover_delta_pct: float
    portfolio: PortfolioBacktestResult
    metrics: "WalkForwardPortfolioMetrics"
    temporal_validation: TemporalValidationResult
    criteria: list[WalkForwardGateCriterion] = Field(default_factory=list)
    worst_segments: list[WalkForwardLossAttribution] = Field(default_factory=list)


class WalkForwardExecutionChallengerEvaluation(BaseModel):
    model_version: str
    status: str
    headline: str
    leakage_guard: str
    baseline_return_delta_pct: float
    baseline_max_drawdown_delta_pct: float
    baseline_trade_count: int
    trade_count_ratio: float
    baseline_stop_rate_pct: float
    challenger_stop_rate_pct: float
    stop_rate_delta_pct: float
    baseline_target_rate_pct: float
    challenger_target_rate_pct: float
    target_rate_delta_pct: float
    portfolio: PortfolioBacktestResult
    metrics: "WalkForwardPortfolioMetrics"
    temporal_validation: TemporalValidationResult
    criteria: list[WalkForwardGateCriterion] = Field(default_factory=list)


class WalkForwardRankingV3Evaluation(BaseModel):
    model_version: str = RANKING_V3_MODEL_VERSION
    status: str
    headline: str
    deployment_scope: str = "shadow_only"
    official_release_allowed: bool = False
    leakage_guard: str
    protocol: RankingV3Protocol
    candidate_pool_signal_count: int
    resolved_candidate_count: int
    untriggered_candidate_count: int
    invalid_candidate_count: int
    valid_candidate_outcome_count: int
    candidate_outcome_coverage_ratio: float
    validation_selected_outcome_count: int
    validation_valid_outcome_count: int
    validation_invalid_outcome_count: int
    validation_excluded_rebalance_date_count: int
    validation_valid_outcome_coverage_ratio: float
    validation_paired_rebalance_date_coverage_ratio: float
    stratified_coverage_group_count: int
    stratified_coverage_failure_count: int
    worst_stratified_outcome_coverage_ratio: float | None = None
    stratified_outcome_coverage: list[dict[str, object]] = Field(default_factory=list)
    changed_snapshot_count: int
    maximum_training_observation_count: int
    maximum_training_date_count: int
    historical_audit_start: date
    historical_audit_end: date
    historical_audit_last_decision_date: date
    benchmark_id: str
    benchmark_status: str
    benchmark_return_pct: float | None = None
    benchmark_member_coverage_ratio: float | None = None
    benchmark_expected_member_observations: int = 0
    benchmark_priced_member_observations: int = 0
    benchmark_excess_return_pct: float | None = None
    turnover_reduction_pct: float | None = None
    max_drawdown_degradation_pct: float | None = None
    constraint_matched_baseline_portfolio: PortfolioBacktestResult
    constraint_matched_baseline_metrics: "WalkForwardPortfolioMetrics"
    portfolio: PortfolioBacktestResult
    metrics: "WalkForwardPortfolioMetrics"
    stress_metrics: "WalkForwardPortfolioMetrics"
    historical_validation: RankingV3ValidationEvaluation
    pbo_evidence: dict[str, object]
    forward_scoring_artifact: RankingV3FrozenScoringArtifact
    forward_scoring_artifact_digest: str
    criteria: list[WalkForwardGateCriterion] = Field(default_factory=list)


class WalkForwardRankingV4Evaluation(BaseModel):
    model_version: str = RANKING_V4_MODEL_VERSION
    status: str
    headline: str
    deployment_scope: str = "shadow_only"
    official_release_allowed: Literal[False] = False
    leakage_guard: str
    protocol: RankingV4Protocol
    candidate_pool_signal_count: int
    valid_candidate_outcome_count: int
    candidate_outcome_coverage_ratio: float
    changed_snapshot_count: int
    maximum_training_observation_count: int
    maximum_training_date_count: int
    constraint_matched_baseline_portfolio: PortfolioBacktestResult
    constraint_matched_baseline_metrics: "WalkForwardPortfolioMetrics"
    portfolio: PortfolioBacktestResult
    metrics: "WalkForwardPortfolioMetrics"
    stress_metrics: "WalkForwardPortfolioMetrics"
    historical_validation: RankingV4HistoricalValidationEvaluation
    pbo_evidence: dict[str, object]
    trial_ledger: RankingV4TrialLedgerEvidence
    evidence_coverage: dict[str, float] = Field(default_factory=dict)
    blocked_reason_counts: dict[str, int] = Field(default_factory=dict)
    first_blocked_reason_counts: dict[str, int] = Field(default_factory=dict)
    gate_reconciliation: dict[str, int] = Field(default_factory=dict)
    criteria: list[WalkForwardGateCriterion] = Field(default_factory=list)


class WalkForwardSelectionResult(BaseModel):
    result_digest_schema: Literal["walk-forward-result-digest-v2"] = (
        WALK_FORWARD_RESULT_DIGEST_SCHEMA
    )
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
    baseline_challenger: WalkForwardBaselineChallengerEvaluation
    execution_challenger: WalkForwardExecutionChallengerEvaluation
    ranking_v3: WalkForwardRankingV3Evaluation | None = None
    ranking_v4: WalkForwardRankingV4Evaluation | None = None
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
    baseline_challenger_excess_return_pct: float | None = None
    execution_challenger_excess_return_pct: float | None = None


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
    baseline_challenger_return_pct: float | None = None
    baseline_challenger_max_drawdown_pct: float | None = None
    baseline_challenger_total_costs: Decimal | None = None
    execution_challenger_return_pct: float | None = None
    execution_challenger_max_drawdown_pct: float | None = None
    execution_challenger_total_costs: Decimal | None = None


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
    factor_prefilter_queries: int = 0
    factor_prefilter_full_window_queries: int = 0
    factor_prefilter_incremental_queries: int = 0
    factor_prefilter_rows_loaded: int = 0
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
_snapshot_worker_display_context_token = None


def _frozen_replay_display_names(lifecycle_inventory: Iterable[object]) -> dict[str, str]:
    """Return display-only names contained in the frozen lifecycle revision.

    The current lifecycle table does not persist names, so this normally returns
    an empty mapping and labels deterministically fall back to instrument IDs.
    """
    names: dict[str, str] = {}
    for profile in lifecycle_inventory:
        instrument_id = str(getattr(profile, "instrument_id", "")).strip().upper()
        name = str(getattr(profile, "name", "") or "").strip()
        if instrument_id.startswith("CN:") and name:
            names[instrument_id.removeprefix("CN:")] = name
    return names


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
    frozen_cn_instrument_names: dict[str, str],
) -> None:
    from qagent.db import create_session_factory

    global _snapshot_worker_repository
    global _snapshot_worker_market_provider
    global _snapshot_worker_strategy_provider
    global _snapshot_worker_display_context_token
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
    # Historical replay workers must not hydrate display names from the current
    # tradable universe. Besides being non-hermetic, that would introduce a live,
    # future-dated dependency into a frozen replay.
    if _snapshot_worker_display_context_token is not None:
        market_instruments.reset_cn_instrument_display_context(
            _snapshot_worker_display_context_token
        )
    _snapshot_worker_display_context_token = (
        market_instruments.activate_cn_instrument_display_context(
            frozen_cn_instrument_names,
            ready=True,
            no_hydration=True,
        )
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
    prefilter_start = decision_date - timedelta(days=min(lookback_days, PREFILTER_LOOKBACK_DAYS))
    prefilter_bars = market_provider.get_factor_prefilter_bars(
        snapshot_input.eligible,
        prefilter_start,
        decision_date,
    )
    factor_rankings = build_factor_rankings(
        prefilter_bars,
        fundamentals=fundamental_evidence,
        asset_types={
            **{instrument_id: "stock" for instrument_id in snapshot_input.eligible_stocks},
            **{instrument_id: "etf" for instrument_id in snapshot_input.eligible_non_stocks},
        },
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
        [card for card in recommendation_cards if card.card_id in paper_eligible_ids]
        if paper_eligible_ids is not None
        else recommendation_cards
    )
    ranking_by_instrument = {ranking.instrument_id: ranking for ranking in factor_rankings}
    selections = [
        _selection(
            card,
            factor_ranking=ranking_by_instrument.get(card.instrument_id),
        )
        for card in eligible_cards
    ]
    benchmark_bars = market_provider.get_daily_bars(
        list(REQUIRED_BENCHMARK_IDS),
        decision_date - timedelta(days=200),
        decision_date,
    )
    benchmark_trend = build_benchmark_trend_snapshot(
        benchmark_bars,
        as_of=decision_date,
        lookback_sessions=50,
    )
    stock_ids = set(snapshot_input.eligible_stocks)
    ranking_v4_market_features = _ranking_v4_market_features(
        [ranking for ranking in factor_rankings if ranking.instrument_id in stock_ids],
        benchmark_valid_count=benchmark_trend.valid_benchmarks,
        benchmark_required_count=benchmark_trend.required_benchmarks,
        benchmark_above_count=benchmark_trend.above_average_count,
    )
    ranking_v4_feature_update = {
        key: float(ranking_v4_market_features[key])
        for key in (
            "market_breadth",
            "benchmark_slope",
            "realized_volatility",
            "cross_sectional_dispersion",
        )
    }
    selections = [
        item.model_copy(
            update={
                "ranking_v4_features": item.ranking_v4_features.model_copy(
                    update=ranking_v4_feature_update
                )
            }
        )
        for item in selections
    ]
    selections = _with_ranking_v4_industry_strength(selections)
    candidate_pool = selections[:RANKING_V3_CANDIDATE_POOL_LIMIT]
    ranking_v4_entries = build_preregistered_ranking_v4_candidate_pool(
        [
            _ranking_v4_candidate_from_selection(
                item,
                decision_date=decision_date,
                market_regime=benchmark_trend.state.value,
                market_features_complete=bool(
                    ranking_v4_market_features["market_features_complete"]
                ),
            )
            for item in selections
        ]
    )
    selection_by_instrument = {item.instrument_id: item for item in selections}
    ranking_v4_candidate_pool = [
        _selection_with_ranking_v4_channel(
            selection_by_instrument[entry.candidate.instrument_id],
            entry,
        )
        for entry in ranking_v4_entries
    ]
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
            paper_blocked_card_count=(len(recommendation_cards) - len(eligible_cards)),
            suspended_count=snapshot_input.suspended_count,
            st_excluded_count=snapshot_input.st_excluded_count,
            missing_tradability_count=snapshot_input.missing_tradability_count,
            fundamental_universe_size=len(snapshot_input.eligible_stocks),
            fundamental_covered_count=fundamental_covered_count,
            benchmark_trend_state=benchmark_trend.state.value,
            benchmark_trend_valid_count=benchmark_trend.valid_benchmarks,
            benchmark_trend_above_count=benchmark_trend.above_average_count,
            market_entry_allowed=benchmark_trend.entry_allowed,
            ranking_v4_market_breadth=ranking_v4_market_features["market_breadth"],
            ranking_v4_benchmark_slope=ranking_v4_market_features["benchmark_slope"],
            ranking_v4_realized_volatility=(ranking_v4_market_features["realized_volatility"]),
            ranking_v4_cross_sectional_dispersion=(
                ranking_v4_market_features["cross_sectional_dispersion"]
            ),
            ranking_v4_market_features_complete=bool(
                ranking_v4_market_features["market_features_complete"]
            ),
            strategy_diversified_count=len(diversified),
            candidate_pool=candidate_pool,
            ranking_v4_candidate_pool=ranking_v4_candidate_pool,
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
    return {audit.card_id for audit in governance if audit.gate_decision.paper_candidate_eligible}


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
    ranked = [item.instrument_id for item in rankings if item.instrument_id in universe]
    if not ranked:
        return sorted(eligible)[:limit]
    non_stocks = set(eligible_non_stocks)
    reserve = min(
        len(non_stocks),
        limit,
        max(1, math.ceil(limit * PREFILTER_NON_STOCK_RESERVE_RATIO)),
    )
    reserved_ids = [instrument_id for instrument_id in ranked if instrument_id in non_stocks][
        :reserve
    ]
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
        factor_prefilter_queries=market_provider.factor_prefilter_query_count,
        factor_prefilter_full_window_queries=(market_provider.factor_prefilter_full_window_queries),
        factor_prefilter_incremental_queries=(market_provider.factor_prefilter_incremental_queries),
        factor_prefilter_rows_loaded=(market_provider.factor_prefilter_rows_loaded),
        fundamental_prefetches=strategy_provider.prefetch_count,
        fundamental_fallback_queries=strategy_provider.query_count,
    )


class WalkForwardCancellationRequested(RuntimeError):
    """Raised when a persisted cancellation asks replay workers to stop."""


def _raise_if_walk_forward_cancelled(
    cancellation_check: Callable[[], bool] | None,
) -> None:
    if cancellation_check is not None and cancellation_check():
        raise WalkForwardCancellationRequested(
            "walk-forward validation was cancelled before publication"
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
        factor_prefilter_queries=sum(item.factor_prefilter_queries for item in values),
        factor_prefilter_full_window_queries=sum(
            item.factor_prefilter_full_window_queries for item in values
        ),
        factor_prefilter_incremental_queries=sum(
            item.factor_prefilter_incremental_queries for item in values
        ),
        factor_prefilter_rows_loaded=sum(item.factor_prefilter_rows_loaded for item in values),
        fundamental_prefetches=sum(item.fundamental_prefetches for item in values),
        fundamental_fallback_queries=sum(item.fundamental_fallback_queries for item in values),
    )


def _terminate_snapshot_executor(
    executor: ProcessPoolExecutor,
    futures: Iterable[object],
) -> None:
    """Stop canceled replay work instead of waiting for expensive snapshots."""

    for future in futures:
        cancel = getattr(future, "cancel", None)
        if callable(cancel):
            cancel()
    for process in list(getattr(executor, "_processes", {}).values()):
        if process.is_alive():
            process.terminate()
    executor.shutdown(wait=False, cancel_futures=True)


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
    cancellation_check: Callable[[], bool] | None = None,
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
    if not walk_forward_manifest_digest_is_valid(experiment_manifest):
        raise RuntimeError("experiment manifest digest is invalid")
    if experiment_manifest.dataset_revision != revision:
        raise RuntimeError("experiment dataset revision no longer matches replay data")
    observed_plan = (
        experiment_manifest.provider_mode,
        experiment_manifest.start_date,
        experiment_manifest.end_date,
        experiment_manifest.rebalance_step_sessions,
        experiment_manifest.lookback_days,
    )
    requested_plan = (
        repository.provider_mode,
        start,
        end,
        rebalance_step_sessions,
        lookback_days,
    )
    if observed_plan != requested_plan:
        raise RuntimeError(
            "experiment manifest execution plan does not match the requested walk-forward run"
        )
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
        _raise_if_walk_forward_cancelled(cancellation_check)
        sessions = trading_sessions_in_range(start, end)[::rebalance_step_sessions]
        resumed = {item.decision_date: item for item in (resume_snapshots or [])}
        unexpected_dates = set(resumed).difference(sessions)
        if unexpected_dates:
            raise ValueError("resume snapshots do not match the requested validation window")
        lifecycle_inventory = owner_repository.lifecycle_inventory(
            revision,
            decision_date=sessions[-1] if sessions else end,
        )
        replay_display_names = _frozen_replay_display_names(lifecycle_inventory)
        missing_listing_dates = [
            item.instrument_id for item in lifecycle_inventory if item.listing_date is None
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
                    and (item.delisting_date is None or item.delisting_date > decision_date)
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
                    item.instrument_id for item in members if item.security_type in {"stock", "1"}
                }
                eligible_stocks = [item for item in eligible if item in stock_ids]
                eligible_non_stocks = [item for item in eligible if item not in stock_ids]
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
        isolate_snapshot_work = cancellation_check is not None
        if effective_snapshot_workers == 1 and not isolate_snapshot_work:
            with market_instruments.cn_instrument_display_context(
                replay_display_names,
                ready=True,
                no_hydration=True,
            ):
                for snapshot_input in pending_inputs:
                    _raise_if_walk_forward_cancelled(cancellation_check)
                    lease_heartbeat.maintain_now()
                    worker_result = _compute_walk_forward_snapshot(
                        snapshot_input,
                        lookback_days=lookback_days,
                        repository=owner_repository,
                        market_provider=market_provider,
                        strategy_provider=strategy_provider,
                    )
                    lease_heartbeat.raise_if_failed()
                    snapshot_by_date[worker_result.snapshot.decision_date] = (
                        worker_result.snapshot
                    )
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
            executor = ProcessPoolExecutor(
                max_workers=effective_snapshot_workers,
                mp_context=get_context("spawn"),
                initializer=_initialize_snapshot_worker,
                initargs=(
                    database_url,
                    repository.provider_mode,
                    owner_run_id,
                    revision,
                    replay_display_names,
                ),
            )
            in_flight = set()
            try:
                pending_iterator = iter(pending_inputs)
                in_flight = {
                    executor.submit(
                        _compute_snapshot_in_worker,
                        snapshot_input,
                        lookback_days,
                    )
                    for snapshot_input in (
                        next(pending_iterator, None) for _ in range(effective_snapshot_workers)
                    )
                    if snapshot_input is not None
                }
                while in_flight:
                    _raise_if_walk_forward_cancelled(cancellation_check)
                    completed, in_flight = wait(
                        in_flight,
                        timeout=1.0,
                        return_when=FIRST_COMPLETED,
                    )
                    lease_heartbeat.raise_if_failed()
                    for future in completed:
                        _raise_if_walk_forward_cancelled(cancellation_check)
                        worker_result = future.result()
                        lease_heartbeat.raise_if_failed()
                        snapshot_by_date[worker_result.snapshot.decision_date] = (
                            worker_result.snapshot
                        )
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
                        snapshot_input = next(pending_iterator, None)
                        if snapshot_input is not None:
                            in_flight.add(
                                executor.submit(
                                    _compute_snapshot_in_worker,
                                    snapshot_input,
                                    lookback_days,
                                )
                            )
            except BaseException:
                _terminate_snapshot_executor(executor, in_flight)
                raise
            else:
                executor.shutdown(wait=True)
            selection_worker_stats = _sum_worker_stats(stats_by_worker.values())
        snapshots = [snapshot_by_date[item] for item in sessions]
        snapshots = _enrich_selection_constraints(
            snapshots,
            repository=owner_repository,
            revision=revision,
            asset_types={item.instrument_id: item.security_type for item in lifecycle_inventory},
        )
        _report_progress(
            progress_callback,
            phase="portfolio_baseline",
            processed_snapshots=len(snapshots),
            total_snapshots=len(sessions),
            lease_heartbeat=lease_heartbeat,
        )
        _raise_if_walk_forward_cancelled(cancellation_check)
        lease_heartbeat.maintain_now()
        execution_resolver = VersionedAshareExecutionResolver(
            owner_repository,
            dataset_revision=revision,
        )
        top_5_signals = _signals(snapshots, size=5)
        top_10_signals = _signals(snapshots, size=10)
        candidate_pool_signals = _signals(
            snapshots,
            size=RANKING_V3_CANDIDATE_POOL_LIMIT,
            selection_source="candidate_pool",
        )
        ranking_v4_candidate_pool_signals = _signals(
            snapshots,
            size=50,
            selection_source="ranking_v4_candidate_pool",
        )
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
        _report_progress(
            progress_callback,
            phase="candidate_outcomes",
            processed_snapshots=len(snapshots),
            total_snapshots=len(sessions),
            lease_heartbeat=lease_heartbeat,
        )
        _raise_if_walk_forward_cancelled(cancellation_check)
        candidate_outcome_ledger = resolve_candidate_outcome_ledger(
            signals=candidate_pool_signals,
            provider=market_provider,
            start=start,
            end=end,
            execution_rule_resolver=execution_resolver,
        )
        ranking_v4_candidate_outcome_ledger = resolve_candidate_outcome_ledger(
            signals=ranking_v4_candidate_pool_signals,
            provider=market_provider,
            start=start,
            end=end,
            max_entry_wait_days=5,
            max_holding_days=20,
            execution_rule_resolver=execution_resolver,
        )
        _report_progress(
            progress_callback,
            phase="candidate_outcomes_stress",
            processed_snapshots=len(snapshots),
            total_snapshots=len(sessions),
            lease_heartbeat=lease_heartbeat,
        )
        _raise_if_walk_forward_cancelled(cancellation_check)
        ranking_v4_stress_candidate_outcome_ledger = resolve_candidate_outcome_ledger(
            signals=ranking_v4_candidate_pool_signals,
            provider=market_provider,
            start=start,
            end=end,
            slippage_bps=Decimal("15"),
            fee_multiplier=Decimal("1.5"),
            max_entry_wait_days=5,
            max_holding_days=20,
            execution_rule_resolver=execution_resolver,
        )
        del candidate_pool_signals
        del ranking_v4_candidate_pool_signals
        gc.collect()
        _report_progress(
            progress_callback,
            phase="ranking_models",
            processed_snapshots=len(snapshots),
            total_snapshots=len(sessions),
            lease_heartbeat=lease_heartbeat,
        )
        _raise_if_walk_forward_cancelled(cancellation_check)
        ranking_v3_observations = _build_ranking_v3_observations(
            snapshots,
            ledger=candidate_outcome_ledger,
            market_provider=market_provider,
            start=start,
            end=end,
        )
        snapshots = _apply_ranking_v3(
            snapshots,
            observations=ranking_v3_observations,
        )
        ranking_v4_observations = _build_ranking_v4_observations(
            snapshots,
            ledger=ranking_v4_candidate_outcome_ledger,
            market_provider=market_provider,
            start=start,
            end=end,
        )
        snapshots = _apply_ranking_v4(
            snapshots,
            observations=ranking_v4_observations,
            market_provider=market_provider,
        )
        del ranking_v4_observations
        gc.collect()
        ranking_v4_model_selections_by_date: dict[
            date,
            dict[str, list[WalkForwardSelection]],
        ] = {}
        for selection_index, snapshot in enumerate(snapshots, start=1):
            _raise_if_walk_forward_cancelled(cancellation_check)
            ranking_v4_model_selections_by_date[snapshot.decision_date] = (
                _ranking_v4_model_selections(snapshot)
            )
            if (
                selection_index == 1
                or selection_index % 5 == 0
                or selection_index == len(snapshots)
            ):
                _report_progress(
                    progress_callback,
                    phase="portfolio_channel_selection",
                    processed_snapshots=len(snapshots),
                    total_snapshots=len(sessions),
                    current_date=snapshot.decision_date,
                    lease_heartbeat=lease_heartbeat,
                )
        execution_challenger_portfolio = run_signal_portfolio_backtest(
            signals=top_5_signals,
            instrument_ids=sorted({item.instrument_id for item in top_5_signals}),
            provider=market_provider,
            start=start,
            end=end,
            max_positions=5,
            execution_rule_resolver=execution_resolver,
            execution_profile=ADAPTIVE_CONFIRMATION_EXECUTION_PROFILE,
        )
        snapshots = _apply_dynamic_reranking(
            snapshots,
            top_10_portfolio=top_10_portfolio,
        )
        snapshots, baseline_observations = _apply_baseline_challenger(
            snapshots,
            top_10_portfolio=top_10_portfolio,
            market_provider=market_provider,
            start=start,
            end=end,
        )
        dynamic_top_5_signals = _signals(
            snapshots,
            size=5,
            selection_source="dynamic",
        )
        baseline_challenger_signals = _signals(
            snapshots,
            size=5,
            selection_source="baseline_challenger",
        )
        ranking_v4_baseline_signals = _signals(
            snapshots,
            size=5,
            selection_source="ranking_v4_constraint_matched_baseline",
        )
        ranking_v4_signals = _signals(
            snapshots,
            size=5,
            selection_source="ranking_v4",
        )
        dynamic_top_5_portfolio = run_signal_portfolio_backtest(
            signals=dynamic_top_5_signals,
            instrument_ids=sorted({item.instrument_id for item in dynamic_top_5_signals}),
            provider=market_provider,
            start=start,
            end=end,
            max_positions=5,
            execution_rule_resolver=execution_resolver,
        )
        baseline_challenger_portfolio = run_signal_portfolio_backtest(
            signals=baseline_challenger_signals,
            instrument_ids=sorted({item.instrument_id for item in baseline_challenger_signals}),
            provider=market_provider,
            start=start,
            end=end,
            max_positions=5,
            execution_rule_resolver=execution_resolver,
        )
        ranking_v4_baseline_portfolio = run_signal_portfolio_backtest(
            signals=ranking_v4_baseline_signals,
            instrument_ids=sorted({item.instrument_id for item in ranking_v4_baseline_signals}),
            provider=market_provider,
            start=start,
            end=end,
            max_positions=5,
            execution_rule_resolver=execution_resolver,
        )
        ranking_v4_portfolio = run_signal_portfolio_backtest(
            signals=ranking_v4_signals,
            instrument_ids=sorted({item.instrument_id for item in ranking_v4_signals}),
            provider=market_provider,
            start=start,
            end=end,
            max_positions=5,
            execution_rule_resolver=execution_resolver,
        )
        ranking_v4_stress_portfolio = run_signal_portfolio_backtest(
            signals=ranking_v4_signals,
            instrument_ids=sorted({item.instrument_id for item in ranking_v4_signals}),
            provider=market_provider,
            start=start,
            end=end,
            max_positions=5,
            slippage_bps=Decimal("15"),
            fee_multiplier=Decimal("1.5"),
            execution_rule_resolver=execution_resolver,
        )
        ranking_v4_channel_portfolios = {}
        for channel in (
            "baseline",
            "trend",
            "breakout",
            "quality_value",
            "defensive_low_vol",
            "etf_industry",
        ):
            model_id = f"channel_{channel}"
            channel_signals = _signals(
                snapshots,
                size=5,
                selection_source=f"ranking_v4_{model_id}",
                ranking_v4_model_selections_by_date=(ranking_v4_model_selections_by_date),
            )
            ranking_v4_channel_portfolios[model_id] = run_signal_portfolio_backtest(
                signals=channel_signals,
                instrument_ids=sorted({item.instrument_id for item in channel_signals}),
                provider=market_provider,
                start=start,
                end=end,
                max_positions=5,
                execution_rule_resolver=execution_resolver,
            )
        _report_progress(
            progress_callback,
            phase="portfolio_channel_backtests",
            processed_snapshots=len(snapshots),
            total_snapshots=len(sessions),
            lease_heartbeat=lease_heartbeat,
        )
        _raise_if_walk_forward_cancelled(cancellation_check)
        audit_last_decision_date = _ranking_v3_historical_audit_last_decision_date(
            start,
            end,
        )
        audit_snapshots = [
            snapshot
            for snapshot in snapshots
            if RANKING_V3_HISTORICAL_AUDIT_START
            <= snapshot.decision_date
            <= audit_last_decision_date
        ]
        audit_baseline_signals = _signals(
            audit_snapshots,
            size=5,
            selection_source="constraint_matched_baseline",
        )
        audit_ranking_v3_signals = _signals(
            audit_snapshots,
            size=5,
            selection_source="ranking_v3",
        )
        audit_window_available = max(start, RANKING_V3_HISTORICAL_AUDIT_START) <= min(
            end, RANKING_V3_HISTORICAL_AUDIT_END
        )
        audit_start = (
            max(start, RANKING_V3_HISTORICAL_AUDIT_START) if audit_window_available else end
        )
        audit_end = min(end, RANKING_V3_HISTORICAL_AUDIT_END) if audit_window_available else end
        audit_constraint_matched_baseline_portfolio = run_signal_portfolio_backtest(
            signals=audit_baseline_signals,
            instrument_ids=sorted({item.instrument_id for item in audit_baseline_signals}),
            provider=market_provider,
            start=audit_start,
            end=audit_end,
            max_positions=5,
            execution_rule_resolver=execution_resolver,
        )
        audit_ranking_v3_portfolio = run_signal_portfolio_backtest(
            signals=audit_ranking_v3_signals,
            instrument_ids=sorted({item.instrument_id for item in audit_ranking_v3_signals}),
            provider=market_provider,
            start=audit_start,
            end=audit_end,
            max_positions=5,
            execution_rule_resolver=execution_resolver,
        )
        audit_ranking_v3_stress_portfolio = run_signal_portfolio_backtest(
            signals=audit_ranking_v3_signals,
            instrument_ids=sorted({item.instrument_id for item in audit_ranking_v3_signals}),
            provider=market_provider,
            start=audit_start,
            end=audit_end,
            max_positions=5,
            slippage_bps=Decimal("15"),
            fee_multiplier=Decimal("1.5"),
            execution_rule_resolver=execution_resolver,
        )
        top_5_metrics = _portfolio_metrics(top_5_portfolio, start, end)
        top_10_metrics = _portfolio_metrics(top_10_portfolio, start, end)
        dynamic_top_5_metrics = _portfolio_metrics(
            dynamic_top_5_portfolio,
            start,
            end,
        )
        baseline_challenger_metrics = _portfolio_metrics(
            baseline_challenger_portfolio,
            start,
            end,
        )
        ranking_v4_baseline_metrics = _portfolio_metrics(
            ranking_v4_baseline_portfolio,
            start,
            end,
        )
        ranking_v4_metrics = _portfolio_metrics(
            ranking_v4_portfolio,
            start,
            end,
        )
        ranking_v4_stress_metrics = _portfolio_metrics(
            ranking_v4_stress_portfolio,
            start,
            end,
        )
        execution_challenger_metrics = _portfolio_metrics(
            execution_challenger_portfolio,
            start,
            end,
        )
        audit_constraint_matched_baseline_metrics = _portfolio_metrics(
            audit_constraint_matched_baseline_portfolio,
            audit_start,
            audit_end,
        )
        audit_ranking_v3_metrics = _portfolio_metrics(
            audit_ranking_v3_portfolio,
            audit_start,
            audit_end,
        )
        audit_ranking_v3_stress_metrics = _portfolio_metrics(
            audit_ranking_v3_stress_portfolio,
            audit_start,
            audit_end,
        )
        audit_eligible_universes = [
            (decision_date, members)
            for decision_date, members in eligible_universes
            if audit_start <= decision_date <= audit_last_decision_date
        ]
        audit_benchmark = _equal_weight_eligible_return_with_coverage(
            market_provider,
            audit_eligible_universes,
            end=audit_end,
        )
        audit_benchmark_return = audit_benchmark.return_pct
        top_5_temporal_validation = _trade_temporal_validation(top_5_portfolio.trades)
        top_10_temporal_validation = _trade_temporal_validation(top_10_portfolio.trades)
        dynamic_top_5_temporal_validation = _trade_temporal_validation(
            dynamic_top_5_portfolio.trades
        )
        baseline_challenger_temporal_validation = _trade_temporal_validation(
            baseline_challenger_portfolio.trades
        )
        execution_challenger_temporal_validation = _trade_temporal_validation(
            execution_challenger_portfolio.trades
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
            baseline_challenger_signals=baseline_challenger_signals,
            top_5_portfolio=top_5_portfolio,
            top_10_portfolio=top_10_portfolio,
            dynamic_top_5_portfolio=dynamic_top_5_portfolio,
            baseline_challenger_portfolio=baseline_challenger_portfolio,
            execution_challenger_portfolio=execution_challenger_portfolio,
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
            baseline_challenger_return=baseline_challenger_metrics.total_return_pct,
            execution_challenger_return=execution_challenger_metrics.total_return_pct,
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
        "ready" if fundamental_coverage >= MIN_FUNDAMENTAL_COVERAGE_RATIO else "insufficient"
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
    baseline_challenger = _build_baseline_challenger_evaluation(
        snapshots=snapshots,
        observations=baseline_observations,
        baseline_metrics=top_5_metrics,
        baseline_temporal_validation=top_5_temporal_validation,
        portfolio=baseline_challenger_portfolio,
        metrics=baseline_challenger_metrics,
        temporal_validation=baseline_challenger_temporal_validation,
        benchmarks=benchmarks,
        cost_sensitivity=cost_sensitivity,
        market_coverage_ratio=coverage["ratio"],
        fundamental_coverage_ratio=fundamental_coverage,
    )
    execution_challenger = _build_execution_challenger_evaluation(
        baseline_portfolio=top_5_portfolio,
        baseline_metrics=top_5_metrics,
        baseline_temporal_validation=top_5_temporal_validation,
        portfolio=execution_challenger_portfolio,
        metrics=execution_challenger_metrics,
        temporal_validation=execution_challenger_temporal_validation,
        benchmarks=benchmarks,
        cost_sensitivity=cost_sensitivity,
        market_coverage_ratio=coverage["ratio"],
        fundamental_coverage_ratio=fundamental_coverage,
    )
    (
        ranking_v3_baseline_returns,
        ranking_v3_challenger_returns,
        completed_v3_trades,
        ranking_v3_sample_quality,
    ) = _ranking_v3_common_return_observations(
        audit_snapshots,
        ledger=candidate_outcome_ledger,
    )
    ranking_v3_protocol = build_ranking_v3_protocol()
    ranking_v3_forward_artifact = build_ranking_v3_frozen_scoring_artifact(
        ranking_v3_observations,
        cutoff=ranking_v3_protocol.prospective_shadow_start,
    )
    ranking_v3_pbo_evidence = _ranking_v3_pbo_evidence(
        audit_snapshots,
        ledger=candidate_outcome_ledger,
        protocol=ranking_v3_protocol,
    )
    ranking_v3_historical_validation = evaluate_ranking_v3_validation(
        ranking_v3_baseline_returns,
        ranking_v3_challenger_returns,
        completed_trade_count=completed_v3_trades,
        additional_hypothesis_p_values=(ranking_v3_protocol.registered_holm_p_values),
        prior_experiment_count=ranking_v3_protocol.prior_experiment_count,
        pbo_evidence=ranking_v3_pbo_evidence,
    )
    ranking_v3 = _build_ranking_v3_evaluation(
        snapshots=snapshots,
        ledger=candidate_outcome_ledger,
        constraint_matched_baseline_portfolio=(audit_constraint_matched_baseline_portfolio),
        constraint_matched_baseline_metrics=(audit_constraint_matched_baseline_metrics),
        portfolio=audit_ranking_v3_portfolio,
        metrics=audit_ranking_v3_metrics,
        stress_metrics=audit_ranking_v3_stress_metrics,
        historical_validation=ranking_v3_historical_validation,
        audit_start=audit_start,
        audit_end=audit_end,
        benchmark_return_pct=audit_benchmark_return,
        benchmark_coverage=audit_benchmark,
        audit_last_decision_date=audit_last_decision_date,
        validation_sample_quality=ranking_v3_sample_quality,
        pbo_evidence=ranking_v3_pbo_evidence,
        forward_scoring_artifact=ranking_v3_forward_artifact,
    )
    del candidate_outcome_ledger
    del ranking_v3_observations
    gc.collect()
    ranking_v4_model_matrix = _ranking_v4_model_return_matrix(
        snapshots,
        portfolios={
            "constraint_matched_baseline": ranking_v4_baseline_portfolio,
            RANKING_V4_FULL_MODEL_ID: ranking_v4_portfolio,
            **ranking_v4_channel_portfolios,
        },
    )
    (
        ranking_v4_baseline_returns,
        ranking_v4_challenger_returns,
        ranking_v4_completed_trades,
        ranking_v4_valid_outcomes,
        ranking_v4_expected_outcomes,
    ) = _ranking_v4_selected_return_observations(
        snapshots,
        normal_ledger=ranking_v4_candidate_outcome_ledger,
        stress_ledger=ranking_v4_stress_candidate_outcome_ledger,
        baseline_portfolio=ranking_v4_baseline_portfolio,
        challenger_portfolio=ranking_v4_portfolio,
        stress_portfolio=ranking_v4_stress_portfolio,
        model_selections_by_date=ranking_v4_model_selections_by_date,
    )
    ranking_v4_validation_dates = {item.rebalance_date for item in ranking_v4_baseline_returns}
    ranking_v4_model_matrix = {
        model_id: [item for item in rows if item.rebalance_date in ranking_v4_validation_dates]
        for model_id, rows in ranking_v4_model_matrix.items()
    }
    ranking_v4_pbo_evidence = evaluate_ranking_v4_cscv_pbo(ranking_v4_model_matrix)
    ranking_v4_protocol = build_ranking_v4_protocol()
    ranking_v4_known_attempt_ids = tuple(
        attempt_id
        for attempt_id in owner_repository.walk_forward_research_attempt_ids()
        if attempt_id != owner_run_id
    )
    ranking_v4_trial_ledger = build_ranking_v4_trial_ledger(
        ranking_v4_model_matrix,
        experiment_registry_digest=(ranking_v4_protocol.experiment_registry.registry_digest),
        known_research_attempt_ids=ranking_v4_known_attempt_ids,
    )
    ranking_v4_historical_validation = evaluate_ranking_v4_historical_validation(
        ranking_v4_baseline_returns,
        ranking_v4_challenger_returns,
        completed_trade_count=ranking_v4_completed_trades,
        valid_outcome_count=ranking_v4_valid_outcomes,
        expected_outcome_count=ranking_v4_expected_outcomes,
        execution_start_date=start,
        execution_end_date=end,
        execution_rebalance_step_sessions=rebalance_step_sessions,
        execution_lookback_days=lookback_days,
        challenger_max_drawdown_pct=ranking_v4_metrics.max_drawdown_pct,
        known_research_attempt_ids=ranking_v4_known_attempt_ids,
        pbo_evidence=ranking_v4_pbo_evidence,
        trial_ledger=ranking_v4_trial_ledger,
    )
    ranking_v4 = _build_ranking_v4_evaluation(
        snapshots=snapshots,
        ledger=ranking_v4_candidate_outcome_ledger,
        constraint_matched_baseline_portfolio=ranking_v4_baseline_portfolio,
        constraint_matched_baseline_metrics=ranking_v4_baseline_metrics,
        portfolio=ranking_v4_portfolio,
        metrics=ranking_v4_metrics,
        stress_metrics=ranking_v4_stress_metrics,
        historical_validation=ranking_v4_historical_validation,
        pbo_evidence=ranking_v4_pbo_evidence,
        trial_ledger=ranking_v4_trial_ledger,
    )
    del ranking_v4_candidate_outcome_ledger
    del ranking_v4_stress_candidate_outcome_ledger
    del ranking_v4_model_selections_by_date
    del ranking_v4_model_matrix
    del ranking_v4_channel_portfolios
    gc.collect()
    digest = _selection_digest(
        provider_mode=repository.provider_mode,
        revision=revision,
        start=start,
        end=end,
        rebalance_step_sessions=rebalance_step_sessions,
        lookback_days=lookback_days,
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
        baseline_challenger=baseline_challenger,
        execution_challenger=execution_challenger,
        ranking_v3=ranking_v3,
        ranking_v4=ranking_v4,
        experiment_manifest=experiment_manifest,
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
        baseline_challenger=baseline_challenger,
        execution_challenger=execution_challenger,
        ranking_v3=ranking_v3,
        ranking_v4=ranking_v4,
        experiment_manifest=experiment_manifest,
        reproducibility_digest=digest,
        data_health={
            "walk_forward_revision": str(revision),
            "walk_forward_snapshots": str(len(snapshots)),
            "walk_forward_lookback_days": str(lookback_days),
            "walk_forward_scan_errors": str(scan_error_count),
            "walk_forward_snapshot_workers": str(effective_snapshot_workers),
            "walk_forward_future_data_guard": (
                "frozen_dataset_revision_and_economic_availability_date"
            ),
            "walk_forward_historical_ingestion_mode": ("pre_run_reconstruction_exploratory_only"),
            "walk_forward_lease_maintenance_count": str(lease_heartbeat.maintenance_count),
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
            "walk_forward_execution_admission": ("final_policy_paper_candidate_eligible"),
            "walk_forward_strategy_diversification_limit": "2",
            "walk_forward_strategy_diversified_selections": str(
                sum(item.strategy_diversified_count for item in snapshots)
            ),
            "walk_forward_benchmark_trend_policy": (
                "block_entries_when_3_of_4_benchmarks_below_ma50"
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
            "walk_forward_fundamental_coverage_pct": str(round(fundamental_coverage * 100, 4)),
            "walk_forward_fundamental_coverage_gate": fundamental_coverage_gate,
            "walk_forward_minimum_fundamental_coverage_pct": str(
                MIN_FUNDAMENTAL_COVERAGE_RATIO * 100
            ),
            "walk_forward_top_5_trades": str(top_5_portfolio.summary.trade_count),
            "walk_forward_top_10_trades": str(top_10_portfolio.summary.trade_count),
            "walk_forward_dynamic_top_5_trades": str(dynamic_top_5_portfolio.summary.trade_count),
            "walk_forward_dynamic_reranker_version": DYNAMIC_RERANKER_VERSION,
            "walk_forward_dynamic_reranker_gate": dynamic_rerank.status,
            "walk_forward_dynamic_changed_snapshots": str(dynamic_rerank.changed_snapshot_count),
            "walk_forward_dynamic_promoted_selections": str(
                dynamic_rerank.promoted_selection_count
            ),
            "walk_forward_dynamic_constraint_blocked": str(
                dynamic_rerank.constraint_blocked_selection_count
            ),
            "walk_forward_dynamic_evidence_blocked": str(
                dynamic_rerank.evidence_blocked_selection_count
            ),
            "walk_forward_dynamic_hysteresis_blocked": str(
                dynamic_rerank.hysteresis_blocked_selection_count
            ),
            "walk_forward_dynamic_incomplete_index_snapshots": str(
                dynamic_rerank.incomplete_index_snapshot_count
            ),
            "walk_forward_dynamic_index_membership_policy": (
                "use_complete_snapshots_and_block_release_when_partial"
            ),
            "walk_forward_dynamic_portfolio_constraints": (
                "anchor_baseline_top5,max_strategy_2,max_industry_2,"
                "max_overlapping_etf_1,promotion_margin_0.03"
            ),
            "walk_forward_dynamic_future_data_guard": (
                "training_trade_exit_date_strictly_before_decision_date"
            ),
            "walk_forward_baseline_challenger_trades": str(
                baseline_challenger_portfolio.summary.trade_count
            ),
            "walk_forward_baseline_challenger_version": (BASELINE_CHALLENGER_VERSION),
            "walk_forward_baseline_challenger_gate": baseline_challenger.status,
            "walk_forward_baseline_challenger_changed_snapshots": str(
                baseline_challenger.changed_snapshot_count
            ),
            "walk_forward_baseline_challenger_cash_snapshots": str(
                baseline_challenger.cash_snapshot_count
            ),
            "walk_forward_baseline_challenger_average_positions": str(
                baseline_challenger.average_positions
            ),
            "walk_forward_baseline_challenger_evidence_blocked": str(
                baseline_challenger.evidence_blocked_selection_count
            ),
            "walk_forward_baseline_challenger_hysteresis_blocked": str(
                baseline_challenger.hysteresis_blocked_selection_count
            ),
            "walk_forward_baseline_challenger_constraint_blocked": str(
                baseline_challenger.constraint_blocked_selection_count
            ),
            "walk_forward_baseline_challenger_policy": (
                "point_in_time_net_excess_bayesian_cash_hysteresis_constraints"
            ),
            "walk_forward_baseline_challenger_future_data_guard": (
                "training_trade_exit_date_strictly_before_decision_date"
            ),
            "walk_forward_execution_challenger_trades": str(
                execution_challenger_portfolio.summary.trade_count
            ),
            "walk_forward_execution_challenger_version": ADAPTIVE_EXECUTION_PROFILE,
            "walk_forward_execution_challenger_gate": execution_challenger.status,
            "walk_forward_execution_challenger_policy": (
                "close_confirmation_next_open_two_atr_stop_two_r_target_breakeven"
            ),
            "walk_forward_execution_challenger_future_data_guard": (
                "confirmation_on_signal_close_execution_next_session"
            ),
            "walk_forward_ranking_v3_protocol": ranking_v3.protocol.protocol_id,
            "walk_forward_ranking_v3_protocol_digest": (ranking_v3.protocol.protocol_digest),
            "walk_forward_ranking_v3_status": ranking_v3.status,
            "walk_forward_ranking_v3_scope": ranking_v3.deployment_scope,
            "walk_forward_ranking_v3_official_release_allowed": str(
                ranking_v3.official_release_allowed
            ).lower(),
            "walk_forward_ranking_v3_candidate_signals": str(
                ranking_v3.candidate_pool_signal_count
            ),
            "walk_forward_ranking_v3_resolved_candidates": str(ranking_v3.resolved_candidate_count),
            "walk_forward_ranking_v3_common_rebalance_dates": str(
                ranking_v3.historical_validation.common_rebalance_date_count
            ),
            "walk_forward_ranking_v3_historical_gate": (
                ranking_v3.historical_validation.statistical_gate_status
            ),
            "walk_forward_ranking_v3_prospective_shadow_start": (
                ranking_v3.protocol.prospective_shadow_start.isoformat()
            ),
            "walk_forward_ranking_v3_isolation": (
                "historical_reused_oos_shadow_only_no_paper_contamination"
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
                market_provider.full_window_queries + selection_worker_stats.full_window_queries
            ),
            "walk_forward_replay_cache_incremental_queries": str(
                market_provider.incremental_queries + selection_worker_stats.incremental_queries
            ),
            "walk_forward_replay_cache_rows_loaded": str(
                market_provider.rows_loaded + selection_worker_stats.rows_loaded
            ),
            "walk_forward_factor_prefilter_queries": str(
                market_provider.factor_prefilter_query_count
                + selection_worker_stats.factor_prefilter_queries
            ),
            "walk_forward_factor_prefilter_full_queries": str(
                market_provider.factor_prefilter_full_window_queries
                + selection_worker_stats.factor_prefilter_full_window_queries
            ),
            "walk_forward_factor_prefilter_incremental_queries": str(
                market_provider.factor_prefilter_incremental_queries
                + selection_worker_stats.factor_prefilter_incremental_queries
            ),
            "walk_forward_factor_prefilter_rows_loaded": str(
                market_provider.factor_prefilter_rows_loaded
                + selection_worker_stats.factor_prefilter_rows_loaded
            ),
            "walk_forward_fundamental_prefetches": str(
                strategy_provider.prefetch_count + selection_worker_stats.fundamental_prefetches
            ),
            "walk_forward_fundamental_fallback_queries": str(
                strategy_provider.query_count + selection_worker_stats.fundamental_fallback_queries
            ),
            "walk_forward_release_gate": strategy_validation.status,
            "walk_forward_ranking_v4_status": ranking_v4.status,
            "walk_forward_ranking_v4_scope": ranking_v4.deployment_scope,
            "walk_forward_ranking_v4_official_release_allowed": str(
                ranking_v4.official_release_allowed
            ).lower(),
            "walk_forward_ranking_v4_protocol_digest": (ranking_v4.protocol.protocol_digest),
            "walk_forward_ranking_v4_candidate_coverage": (
                f"{ranking_v4.candidate_outcome_coverage_ratio:.6f}"
            ),
            "walk_forward_ranking_v4_pbo_status": (ranking_v4.historical_validation.pbo_status),
            "walk_forward_ranking_v4_forward_eligible": str(
                ranking_v4.historical_validation.eligible_for_confirmatory_forward
            ).lower(),
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
            "walk_forward_stress_baseline_challenger_return_pct": str(
                cost_sensitivity[-1].baseline_challenger_return_pct
            ),
            "walk_forward_stress_execution_challenger_return_pct": str(
                cost_sensitivity[-1].execution_challenger_return_pct
            ),
            "walk_forward_digest": digest,
            "walk_forward_experiment_digest": experiment_manifest.experiment_digest,
            "walk_forward_code_revision": experiment_manifest.code_revision,
            "walk_forward_runtime_revisions": ",".join(
                experiment_manifest.runtime_revisions or [experiment_manifest.code_revision]
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
        if _selection_membership_changed(
            snapshot.top_5,
            snapshot.dynamic_top_5,
        )
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
    evidence_blocked_selection_count = sum(item.rerank_evidence_blocked_count for item in snapshots)
    hysteresis_blocked_selection_count = sum(
        item.rerank_hysteresis_blocked_count for item in snapshots
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
        (item for item in benchmarks if item.benchmark_id == ELIGIBLE_UNIVERSE_BENCHMARK_ID),
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
                maximum_training_samples < MIN_RERANK_TRAINING_SAMPLES or model_ready_snapshots == 0
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
            insufficient=(not eligible_benchmark or eligible_benchmark.status != "ready"),
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
            insufficient=(stress is None or stress.dynamic_top_5_return_pct is None),
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
            ready=(metrics.max_drawdown_pct >= -15 and drawdown_delta >= -2),
            insufficient=False,
            value=(f"{metrics.max_drawdown_pct:+.2f}% / 较基线 {drawdown_delta:+.2f}%"),
            requirement=">= -15% 且不比基线恶化 2 个百分点以上",
        ),
    ]
    status, headline = _dynamic_rerank_gate_outcome(criteria)
    return WalkForwardRerankEvaluation(
        model_version=DYNAMIC_RERANKER_VERSION,
        status=status,
        headline=headline,
        leakage_guard="仅使用 exit_date 严格早于当期 decision_date 的已结束交易",
        evaluated_snapshot_count=len(snapshots),
        changed_snapshot_count=len(changed_snapshots),
        promoted_selection_count=promoted_selection_count,
        constraint_blocked_selection_count=constraint_blocked_selection_count,
        evidence_blocked_selection_count=evidence_blocked_selection_count,
        hysteresis_blocked_selection_count=hysteresis_blocked_selection_count,
        incomplete_index_snapshot_count=incomplete_index_snapshot_count,
        maximum_training_sample_count=maximum_training_samples,
        baseline_return_delta_pct=return_delta,
        baseline_max_drawdown_delta_pct=drawdown_delta,
        portfolio=portfolio,
        metrics=metrics,
        temporal_validation=temporal_validation,
        criteria=criteria,
    )


def _selection_membership_changed(
    baseline: list[WalkForwardSelection],
    challenger: list[WalkForwardSelection],
) -> bool:
    return {item.instrument_id for item in baseline} != {item.instrument_id for item in challenger}


def _dynamic_rerank_gate_outcome(
    criteria: list[WalkForwardGateCriterion],
) -> tuple[str, str]:
    has_insufficient_evidence = any(item.status == "insufficient" for item in criteria)
    if any(item.status == "fail" for item in criteria):
        return (
            "rejected",
            (
                "动态重排序未优于固定 Top5，且仍有证据缺口；保持拒绝，不进入模拟盘。"
                if has_insufficient_evidence
                else "动态重排序未优于固定 Top5，保持拒绝，不进入模拟盘。"
            ),
        )
    if has_insufficient_evidence:
        return (
            "insufficient",
            "动态重排序仍在积累严格按时间截断的训练与样本外证据。",
        )
    return (
        "accepted",
        "动态重排序通过全部门槛，可以进入 20 个交易日前向模拟。",
    )


def _build_baseline_challenger_evaluation(
    *,
    snapshots: list[WalkForwardSnapshot],
    observations: list[ResolvedBaselineObservation],
    baseline_metrics: WalkForwardPortfolioMetrics,
    baseline_temporal_validation: TemporalValidationResult,
    portfolio: PortfolioBacktestResult,
    metrics: WalkForwardPortfolioMetrics,
    temporal_validation: TemporalValidationResult,
    benchmarks: list[WalkForwardBenchmarkComparison],
    cost_sensitivity: list[WalkForwardCostScenario],
    market_coverage_ratio: float,
    fundamental_coverage_ratio: float,
) -> WalkForwardBaselineChallengerEvaluation:
    changed_snapshot_count = sum(
        _selection_membership_changed(
            snapshot.top_5,
            snapshot.baseline_challenger_top_5,
        )
        for snapshot in snapshots
    )
    cash_snapshot_count = sum(len(snapshot.baseline_challenger_top_5) < 5 for snapshot in snapshots)
    model_ready_snapshot_count = sum(
        snapshot.baseline_challenger_model_ready for snapshot in snapshots
    )
    maximum_training_sample_count = max(
        (snapshot.baseline_challenger_training_sample_count for snapshot in snapshots),
        default=0,
    )
    average_positions = (
        round(
            statistics.mean(len(snapshot.baseline_challenger_top_5) for snapshot in snapshots),
            2,
        )
        if snapshots
        else 0.0
    )
    return_delta = round(
        metrics.total_return_pct - baseline_metrics.total_return_pct,
        4,
    )
    drawdown_delta = round(
        metrics.max_drawdown_pct - baseline_metrics.max_drawdown_pct,
        4,
    )
    turnover_delta = round(
        metrics.turnover_pct - baseline_metrics.turnover_pct,
        4,
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
    oos_delta = (
        challenger_oos_return - baseline_oos_return
        if challenger_oos_return is not None and baseline_oos_return is not None
        else None
    )
    eligible_benchmark = next(
        (item for item in benchmarks if item.benchmark_id == ELIGIBLE_UNIVERSE_BENCHMARK_ID),
        None,
    )
    stress = next((item for item in cost_sensitivity if item.key == "stress"), None)
    turnover_requirement = baseline_metrics.turnover_pct * 0.75
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
            key="resolved_training_history",
            label="严格时序训练历史",
            ready=(
                maximum_training_sample_count >= MIN_BASELINE_TRAINING_SAMPLES
                and model_ready_snapshot_count > 0
            ),
            insufficient=(
                maximum_training_sample_count < MIN_BASELINE_TRAINING_SAMPLES
                or model_ready_snapshot_count == 0
            ),
            value=(f"{maximum_training_sample_count} 笔 / {model_ready_snapshot_count} 期启用"),
            requirement=(f">= {MIN_BASELINE_TRAINING_SAMPLES} 笔且至少 1 期启用"),
        ),
        _gate_criterion(
            key="out_of_sample_count",
            label="样本外交易数",
            ready=bool(challenger_oos and challenger_oos.sample_count >= 30),
            insufficient=not challenger_oos or challenger_oos.sample_count < 30,
            value=str(challenger_oos.sample_count if challenger_oos else 0),
            requirement=">= 30",
        ),
        _gate_criterion(
            key="out_of_sample_net_return",
            label="样本外净收益",
            ready=bool(
                challenger_oos_return is not None
                and challenger_oos_return > 0
                and oos_delta is not None
                and oos_delta > 0
            ),
            insufficient=(
                challenger_oos_return is None
                or baseline_oos_return is None
                or not challenger_oos
                or challenger_oos.sample_count < 30
            ),
            value=(
                f"{challenger_oos_return:+.2f}% / 较基线 {oos_delta:+.2f}%"
                if challenger_oos_return is not None and oos_delta is not None
                else "-"
            ),
            requirement="> 0% 且优于固定 Top 5",
        ),
        _gate_criterion(
            key="full_period_net_return",
            label="全期净收益",
            ready=metrics.total_return_pct > 0 and return_delta > 0,
            insufficient=False,
            value=(f"{metrics.total_return_pct:+.2f}% / 较基线 {return_delta:+.2f}%"),
            requirement="> 0% 且优于固定 Top 5",
        ),
        _gate_criterion(
            key="benchmark_excess",
            label="历史可交易池超额",
            ready=bool(
                eligible_benchmark
                and eligible_benchmark.status == "ready"
                and (eligible_benchmark.baseline_challenger_excess_return_pct or 0) > 0
            ),
            insufficient=(not eligible_benchmark or eligible_benchmark.status != "ready"),
            value=(
                f"{eligible_benchmark.baseline_challenger_excess_return_pct:+.2f}%"
                if eligible_benchmark
                and eligible_benchmark.baseline_challenger_excess_return_pct is not None
                else "-"
            ),
            requirement="> 0%",
        ),
        _gate_criterion(
            key="cost_stress",
            label="压力成本后收益",
            ready=bool(
                stress
                and stress.baseline_challenger_return_pct is not None
                and stress.baseline_challenger_return_pct > 0
            ),
            insufficient=(stress is None or stress.baseline_challenger_return_pct is None),
            value=(
                f"{stress.baseline_challenger_return_pct:+.2f}%"
                if stress and stress.baseline_challenger_return_pct is not None
                else "-"
            ),
            requirement="> 0%",
        ),
        _gate_criterion(
            key="turnover_reduction",
            label="换手率下降",
            ready=(
                metrics.turnover_pct < baseline_metrics.turnover_pct
                and metrics.turnover_pct <= turnover_requirement
            ),
            insufficient=False,
            value=(f"{metrics.turnover_pct:.0f}% / 较基线 {turnover_delta:+.0f}%"),
            requirement="低于基线且至少下降 25%",
        ),
        _gate_criterion(
            key="max_drawdown",
            label="最大回撤",
            ready=metrics.max_drawdown_pct >= -15 and drawdown_delta >= -2,
            insufficient=False,
            value=(f"{metrics.max_drawdown_pct:+.2f}% / 较基线 {drawdown_delta:+.2f}%"),
            requirement=">= -15% 且不比基线恶化 2 个百分点以上",
        ),
    ]
    status, headline = _baseline_challenger_gate_outcome(criteria)
    return WalkForwardBaselineChallengerEvaluation(
        model_version=BASELINE_CHALLENGER_VERSION,
        status=status,
        headline=headline,
        leakage_guard=(
            "选股仅使用 exit_date 严格早于 decision_date 的已结束交易；"
            "止损原因和持有期只做事后归因，不参与候选评分"
        ),
        evaluated_snapshot_count=len(snapshots),
        model_ready_snapshot_count=model_ready_snapshot_count,
        changed_snapshot_count=changed_snapshot_count,
        cash_snapshot_count=cash_snapshot_count,
        average_positions=average_positions,
        retained_selection_count=sum(
            snapshot.baseline_challenger_retained_count for snapshot in snapshots
        ),
        evidence_blocked_selection_count=sum(
            snapshot.baseline_challenger_evidence_blocked_count for snapshot in snapshots
        ),
        hysteresis_blocked_selection_count=sum(
            snapshot.baseline_challenger_hysteresis_blocked_count for snapshot in snapshots
        ),
        constraint_blocked_selection_count=sum(
            snapshot.baseline_challenger_constraint_blocked_count for snapshot in snapshots
        ),
        maximum_training_sample_count=maximum_training_sample_count,
        baseline_return_delta_pct=return_delta,
        baseline_max_drawdown_delta_pct=drawdown_delta,
        baseline_turnover_delta_pct=turnover_delta,
        portfolio=portfolio,
        metrics=metrics,
        temporal_validation=temporal_validation,
        criteria=criteria,
        worst_segments=_loss_attribution(observations),
    )


def _baseline_challenger_gate_outcome(
    criteria: list[WalkForwardGateCriterion],
) -> tuple[str, str]:
    if any(item.status == "fail" for item in criteria):
        return (
            "rejected",
            "基线优化挑战者未通过收益、超额、成本、换手和回撤门槛，不替换正式 Top 5。",
        )
    if any(item.status == "insufficient" for item in criteria):
        return (
            "insufficient",
            "基线优化挑战者仍缺少严格时序的样本外证据，不进入模拟盘。",
        )
    return (
        "accepted",
        "基线优化挑战者通过全部门槛，可进入 20 个交易日前向模拟。",
    )


def _build_execution_challenger_evaluation(
    *,
    baseline_portfolio: PortfolioBacktestResult,
    baseline_metrics: WalkForwardPortfolioMetrics,
    baseline_temporal_validation: TemporalValidationResult,
    portfolio: PortfolioBacktestResult,
    metrics: WalkForwardPortfolioMetrics,
    temporal_validation: TemporalValidationResult,
    benchmarks: list[WalkForwardBenchmarkComparison],
    cost_sensitivity: list[WalkForwardCostScenario],
    market_coverage_ratio: float,
    fundamental_coverage_ratio: float,
) -> WalkForwardExecutionChallengerEvaluation:
    return_delta = round(
        metrics.total_return_pct - baseline_metrics.total_return_pct,
        4,
    )
    drawdown_delta = round(
        metrics.max_drawdown_pct - baseline_metrics.max_drawdown_pct,
        4,
    )
    baseline_trade_count = baseline_metrics.trade_count
    trade_count_ratio = (
        round(metrics.trade_count / baseline_trade_count, 4) if baseline_trade_count else 0.0
    )
    baseline_stop_rate = _exit_reason_rate(baseline_portfolio, "stopped")
    challenger_stop_rate = _exit_reason_rate(portfolio, "stopped")
    baseline_target_rate = _exit_reason_rate(baseline_portfolio, "target_1_hit")
    challenger_target_rate = _exit_reason_rate(portfolio, "target_1_hit")
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
    oos_delta = (
        challenger_oos_return - baseline_oos_return
        if challenger_oos_return is not None and baseline_oos_return is not None
        else None
    )
    eligible_benchmark = next(
        (item for item in benchmarks if item.benchmark_id == ELIGIBLE_UNIVERSE_BENCHMARK_ID),
        None,
    )
    stress = next((item for item in cost_sensitivity if item.key == "stress"), None)
    stop_rate_ready = challenger_stop_rate <= 25 or challenger_stop_rate <= baseline_stop_rate * 0.8
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
            key="sample_count",
            label="执行挑战者交易数",
            ready=metrics.trade_count >= 30 and trade_count_ratio >= 0.5,
            insufficient=metrics.trade_count < 30,
            value=f"{metrics.trade_count} / 基线 {trade_count_ratio:.0%}",
            requirement=">= 30 且不少于基线 50%",
        ),
        _gate_criterion(
            key="out_of_sample_return",
            label="样本外净收益",
            ready=bool(
                challenger_oos_return is not None
                and challenger_oos_return > 0
                and oos_delta is not None
                and oos_delta > 0
            ),
            insufficient=(
                challenger_oos is None
                or challenger_oos.sample_count < 30
                or challenger_oos_return is None
                or baseline_oos_return is None
            ),
            value=(
                f"{challenger_oos_return:+.2f}% / 较基线 {oos_delta:+.2f}%"
                if challenger_oos_return is not None and oos_delta is not None
                else "-"
            ),
            requirement="> 0% 且优于原执行",
        ),
        _gate_criterion(
            key="full_period_return",
            label="全期净收益",
            ready=metrics.total_return_pct > 0 and return_delta > 0,
            insufficient=False,
            value=f"{metrics.total_return_pct:+.2f}% / 较基线 {return_delta:+.2f}%",
            requirement="> 0% 且优于原执行",
        ),
        _gate_criterion(
            key="benchmark_excess",
            label="历史可交易池超额",
            ready=bool(
                eligible_benchmark
                and eligible_benchmark.status == "ready"
                and (eligible_benchmark.execution_challenger_excess_return_pct or 0) > 0
            ),
            insufficient=not eligible_benchmark or eligible_benchmark.status != "ready",
            value=(
                f"{eligible_benchmark.execution_challenger_excess_return_pct:+.2f}%"
                if eligible_benchmark
                and eligible_benchmark.execution_challenger_excess_return_pct is not None
                else "-"
            ),
            requirement="> 0%",
        ),
        _gate_criterion(
            key="cost_stress",
            label="压力成本后收益",
            ready=bool(
                stress
                and stress.execution_challenger_return_pct is not None
                and stress.execution_challenger_return_pct > 0
            ),
            insufficient=not stress or stress.execution_challenger_return_pct is None,
            value=(
                f"{stress.execution_challenger_return_pct:+.2f}%"
                if stress and stress.execution_challenger_return_pct is not None
                else "-"
            ),
            requirement="> 0%",
        ),
        _gate_criterion(
            key="stop_rate",
            label="止损退出比例",
            ready=stop_rate_ready,
            insufficient=False,
            value=(
                f"{challenger_stop_rate:.1f}% / 较基线 "
                f"{challenger_stop_rate - baseline_stop_rate:+.1f}%"
            ),
            requirement="<= 25% 或较原执行下降 20%",
        ),
        _gate_criterion(
            key="payoff_quality",
            label="盈亏质量",
            ready=bool(
                portfolio.summary.profit_factor is not None and portfolio.summary.profit_factor > 1
            ),
            insufficient=portfolio.summary.profit_factor is None,
            value=(
                f"PF {portfolio.summary.profit_factor:.2f} / 目标命中 {challenger_target_rate:.1f}%"
                if portfolio.summary.profit_factor is not None
                else "-"
            ),
            requirement="PF > 1",
        ),
        _gate_criterion(
            key="max_drawdown",
            label="最大回撤",
            ready=metrics.max_drawdown_pct >= -15 and drawdown_delta >= -2,
            insufficient=False,
            value=f"{metrics.max_drawdown_pct:+.2f}% / 较基线 {drawdown_delta:+.2f}%",
            requirement=">= -15% 且不比基线恶化 2 个百分点以上",
        ),
    ]
    status, headline = _execution_challenger_gate_outcome(criteria)
    return WalkForwardExecutionChallengerEvaluation(
        model_version=ADAPTIVE_EXECUTION_PROFILE,
        status=status,
        headline=headline,
        leakage_guard=(
            "信号日收盘确认只决定次一交易日委托；ATR、结构止损和风险距离"
            "只使用 signal_date 当日及以前的行情"
        ),
        baseline_return_delta_pct=return_delta,
        baseline_max_drawdown_delta_pct=drawdown_delta,
        baseline_trade_count=baseline_trade_count,
        trade_count_ratio=trade_count_ratio,
        baseline_stop_rate_pct=baseline_stop_rate,
        challenger_stop_rate_pct=challenger_stop_rate,
        stop_rate_delta_pct=round(challenger_stop_rate - baseline_stop_rate, 4),
        baseline_target_rate_pct=baseline_target_rate,
        challenger_target_rate_pct=challenger_target_rate,
        target_rate_delta_pct=round(challenger_target_rate - baseline_target_rate, 4),
        portfolio=portfolio,
        metrics=metrics,
        temporal_validation=temporal_validation,
        criteria=criteria,
    )


def _execution_challenger_gate_outcome(
    criteria: list[WalkForwardGateCriterion],
) -> tuple[str, str]:
    if any(item.status == "fail" for item in criteria):
        return (
            "rejected",
            "自适应执行未同时改善收益、止损率、成本和回撤，保持影子实验。",
        )
    if any(item.status == "insufficient" for item in criteria):
        return (
            "insufficient",
            "自适应执行仍缺少足够样本，不接入正式推荐或模拟盘。",
        )
    return (
        "accepted",
        "自适应执行通过全部门槛，可进入 20 个交易日前向影子模拟。",
    )


def _exit_reason_rate(
    portfolio: PortfolioBacktestResult,
    reason: str,
) -> float:
    count = len(portfolio.trades)
    if not count:
        return 0.0
    return round(
        sum(item.exit_reason == reason for item in portfolio.trades) / count * 100,
        4,
    )


def _loss_attribution(
    observations: list[ResolvedBaselineObservation],
) -> list[WalkForwardLossAttribution]:
    grouped: dict[tuple[str, str], list[ResolvedBaselineObservation]] = {}
    for observation in observations:
        dimensions = [
            ("strategy", observation.primary_strategy_id or "unknown"),
            ("market_regime", observation.market_regime or "unknown"),
            ("industry", observation.industry or "unknown"),
            ("asset_type", observation.asset_type or "unknown"),
            ("exit_reason", observation.exit_reason or "unknown"),
            ("holding_period", _holding_period_bucket(observation.holding_days)),
        ]
        dimensions.extend(("factor", factor) for factor in set(observation.factor_signals))
        for dimension, key in dimensions:
            grouped.setdefault((dimension, key), []).append(observation)

    metrics = []
    for (dimension, key), rows in grouped.items():
        if len(rows) < 3:
            continue
        excess_returns = [item.net_excess_return_pct for item in rows]
        metrics.append(
            WalkForwardLossAttribution(
                dimension=dimension,
                key=key,
                label=_attribution_label(dimension, key),
                trade_count=len(rows),
                win_rate=round(
                    sum(value > 0 for value in excess_returns) / len(excess_returns),
                    4,
                ),
                average_return_pct=round(
                    statistics.mean(item.return_pct for item in rows),
                    4,
                ),
                average_benchmark_return_pct=round(
                    statistics.mean(item.benchmark_return_pct for item in rows),
                    4,
                ),
                average_net_excess_return_pct=round(
                    statistics.mean(excess_returns),
                    4,
                ),
                worst_net_excess_return_pct=round(min(excess_returns), 4),
            )
        )
    return sorted(
        metrics,
        key=lambda item: (
            item.average_net_excess_return_pct,
            -item.trade_count,
            item.dimension,
            item.key,
        ),
    )[:12]


def _holding_period_bucket(holding_days: int) -> str:
    if holding_days <= 5:
        return "0-5d"
    if holding_days <= 10:
        return "6-10d"
    if holding_days <= 20:
        return "11-20d"
    return "20d+"


def _attribution_label(dimension: str, key: str) -> str:
    if dimension in {"strategy", "factor"}:
        return _walk_forward_evidence_label(key)
    if dimension == "market_regime":
        return {
            "risk_on": "风险偏好",
            "mixed": "市场分化",
            "risk_off": "风险规避",
            "unknown": "市场状态未知",
        }.get(key, key)
    if dimension == "asset_type":
        return {"stock": "股票", "etf": "ETF", "fund": "基金"}.get(key, key)
    if dimension == "exit_reason":
        return {
            "target_1_hit": "目标止盈",
            "target_1": "目标止盈",
            "stopped": "触发止损",
            "initial_stop": "触发止损",
            "time_exit": "持有期到期",
            "max_holding_days": "持有期到期",
            "end_of_period": "回测期结束",
        }.get(key, key)
    if dimension == "holding_period":
        return f"持有 {key}"
    return key


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
            item.positive_edge_p_value if item.statistical_verdict != "insufficient" else None
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


def _selection(
    card,
    *,
    factor_ranking: FactorRanking | None = None,
) -> WalkForwardSelection:
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
        themes=(sorted(set(context.themes)) if context else []),
        index_memberships=(sorted(set(context.index_memberships)) if context else []),
        ranking_features=_ranking_v3_features(card, factor_ranking),
        ranking_v4_features=_ranking_v4_features(card, factor_ranking),
    )


def _ranking_v3_features(
    card,
    factor_ranking: FactorRanking | None,
) -> RankingV3FeatureVector:
    exposure_scores = {
        exposure.factor_id: float(exposure.score) for exposure in card.factor_exposures
    }

    def score(name: str, default: float = 0.5) -> float:
        if factor_ranking is not None:
            field_name = f"{name}_score"
            if hasattr(factor_ranking, field_name):
                return float(getattr(factor_ranking, field_name))
        return exposure_scores.get(name, default)

    data_completeness = (
        float(factor_ranking.data_completeness)
        if factor_ranking is not None
        else (float(card.data_quality_audit.score) if card.data_quality_audit is not None else 0.0)
    )
    return RankingV3FeatureVector(
        strategy_score=float(card.strategy_score),
        factor_score=float(card.factor_score),
        valuation=score("valuation"),
        size=score("size"),
        quality=score("quality"),
        momentum=score("momentum"),
        trend_quality=score("trend_quality"),
        liquidity=score("liquidity"),
        low_risk=score("low_risk"),
        risk_filter=score("risk_filter"),
        reversal=score("reversal"),
        execution_penalty=(
            float(factor_ranking.execution_penalty) if factor_ranking is not None else 0.0
        ),
        data_completeness=data_completeness,
    )


def _ranking_v4_features(
    card,
    factor_ranking: FactorRanking | None,
) -> RankingV4FeatureVector:
    v3 = _ranking_v3_features(card, factor_ranking)
    breakout_quality = (
        float(card.strategy_score)
        if (card.primary_strategy_id or "") == "breakout_volume_confirmation"
        else 0.0
    )
    realized_volatility = max(0.0, min(1.0, 1.0 - v3.low_risk))
    tail_risk = max(
        realized_volatility,
        1.0 - v3.risk_filter,
    )
    return RankingV4FeatureVector(
        strategy_score=v3.strategy_score,
        factor_score=v3.factor_score,
        valuation=v3.valuation,
        size=v3.size,
        quality=v3.quality,
        momentum=v3.momentum,
        trend_quality=v3.trend_quality,
        breakout_quality=breakout_quality,
        liquidity=v3.liquidity,
        low_risk=v3.low_risk,
        risk_filter=v3.risk_filter,
        reversal=v3.reversal,
        capacity=v3.liquidity,
        tail_risk=tail_risk,
        execution_penalty=v3.execution_penalty,
        data_completeness=v3.data_completeness,
    )


def _ranking_v4_market_features(
    rankings: list[FactorRanking],
    *,
    benchmark_valid_count: int,
    benchmark_required_count: int,
    benchmark_above_count: int,
) -> dict[str, float | bool | None]:
    if not rankings:
        return {
            "market_breadth": 0.5,
            "benchmark_slope": 0.5,
            "realized_volatility": 0.5,
            "cross_sectional_dispersion": 0.5,
            "market_features_complete": False,
        }
    factor_scores = [float(item.factor_score) for item in rankings]
    market_breadth = sum(value >= 0.5 for value in factor_scores) / len(factor_scores)
    realized_volatility = statistics.fmean(1.0 - float(item.low_risk_score) for item in rankings)
    cross_sectional_dispersion = min(
        1.0,
        statistics.pstdev(factor_scores) * 4.0,
    )
    benchmark_complete = (
        benchmark_required_count > 0 and benchmark_valid_count == benchmark_required_count
    )
    benchmark_slope = (
        benchmark_above_count / benchmark_required_count if benchmark_complete else 0.5
    )
    complete = (
        benchmark_complete
        and len(rankings) >= 30
        and all(
            math.isfinite(value)
            for value in (
                market_breadth,
                realized_volatility,
                cross_sectional_dispersion,
            )
        )
    )
    return {
        "market_breadth": round(market_breadth, 8),
        "benchmark_slope": round(benchmark_slope, 8),
        "realized_volatility": round(realized_volatility, 8),
        "cross_sectional_dispersion": round(cross_sectional_dispersion, 8),
        "market_features_complete": complete,
    }


def _ranking_v4_candidate_from_selection(
    selection: WalkForwardSelection,
    *,
    decision_date: date,
    market_regime: str,
    market_features_complete: bool,
    incumbent: bool = False,
) -> RankingV4Candidate:
    features = selection.ranking_v4_features
    benchmark_opportunity_cost = max(
        0.0,
        ((features.benchmark_slope or 0.0) - 0.5) * 0.5,
    )
    liquidity_penalty = max(0.0, (1.0 - features.liquidity) * 0.25)
    tail_risk_penalty = max(0.0, features.tail_risk * 0.25)
    is_etf = selection.asset_type.strip().lower() in {
        "etf",
        "fund",
        "index_fund",
    }
    feature_as_of = _latest_evidence_date(
        selection.ranking_v4_fundamental_as_of,
        selection.ranking_v4_bar_end_date,
    )
    constraint_as_of = _latest_evidence_date(
        selection.ranking_v4_industry_snapshot_date,
        selection.ranking_v4_index_membership_snapshot_date,
        selection.ranking_v4_bar_end_date,
    )
    return RankingV4Candidate(
        instrument_id=selection.instrument_id,
        baseline_rank_score=float(selection.rank_score),
        decision_date=decision_date,
        feature_as_of=feature_as_of,
        market_regime_as_of=selection.ranking_v4_bar_end_date,
        constraint_as_of=constraint_as_of,
        cost_as_of=selection.ranking_v4_cost_effective_date,
        primary_strategy_id=selection.primary_strategy_id,
        primary_channel=selection.ranking_v4_primary_channel,
        factor_signals=selection.factor_signals,
        market_regime=market_regime,
        asset_type=selection.asset_type,
        industry=selection.industry,
        themes=selection.themes,
        index_memberships=selection.index_memberships,
        underlying_ids=selection.underlying_ids,
        factor_exposures=(
            {"beta": selection.ranking_v4_beta} if selection.ranking_v4_beta is not None else {}
        ),
        features=features,
        point_in_time_evidence_complete=(selection.ranking_v4_temporal_evidence_complete),
        market_regime_features_complete=market_features_complete,
        constraint_data_complete=(selection.ranking_v4_constraint_data_complete),
        constraint_evidence_mode=(selection.ranking_v4_constraint_evidence_mode),
        underlying_evidence_complete=(
            selection.ranking_v4_underlying_evidence_complete or not is_etf
        ),
        incumbent=incumbent,
        replacement_cost_pct=0.0 if incumbent else 0.15,
        stage_two_embedded_cost_pct=0.0,
        replacement_cost_evidence_complete=True,
        benchmark_opportunity_cost_pct=round(
            benchmark_opportunity_cost,
            6,
        ),
        liquidity_penalty_pct=round(liquidity_penalty, 6),
        tail_risk_penalty_pct=round(tail_risk_penalty, 6),
    )


def _latest_evidence_date(*values: date | None) -> date | None:
    available = [value for value in values if value is not None]
    return max(available) if available else None


def _selection_with_ranking_v4_channel(
    selection: WalkForwardSelection,
    entry: RankingV4CandidatePoolEntry,
) -> WalkForwardSelection:
    return selection.model_copy(
        update={
            "ranking_v4_primary_channel": entry.primary_channel,
            "ranking_v4_matched_channels": list(entry.matched_channels),
            "ranking_v4_channel_scores": dict(entry.channel_scores),
            "ranking_v4_replacement_cost_pct": (entry.candidate.replacement_cost_pct),
            "ranking_v4_benchmark_opportunity_cost_pct": (
                entry.candidate.benchmark_opportunity_cost_pct
            ),
            "ranking_v4_liquidity_penalty_pct": (entry.candidate.liquidity_penalty_pct),
            "ranking_v4_tail_risk_penalty_pct": (entry.candidate.tail_risk_penalty_pct),
        }
    )


def _with_ranking_v4_industry_strength(
    selections: list[WalkForwardSelection],
) -> list[WalkForwardSelection]:
    scores_by_industry: dict[str, list[float]] = {}
    for selection in selections:
        industry = (selection.industry or "").strip()
        if not industry or industry.lower() in {"unknown", "未知", "未分类"}:
            continue
        scores_by_industry.setdefault(industry, []).append(
            selection.ranking_v4_features.factor_score
        )
    strength_by_industry = {
        industry: statistics.fmean(values)
        for industry, values in scores_by_industry.items()
        if values
    }
    return [
        selection.model_copy(
            update={
                "ranking_v4_features": selection.ranking_v4_features.model_copy(
                    update={
                        "industry_strength": round(
                            strength_by_industry.get(
                                (selection.industry or "").strip(),
                                0.5,
                            ),
                            8,
                        )
                    }
                )
            }
        )
        for selection in selections
    ]


def _selection_factor_signals(card) -> list[str]:
    signals = [str(value) for value in card.factor_flags if value]
    for exposure in card.factor_exposures:
        if exposure.score >= 0.65:
            signals.append(exposure.factor_id)
    return sorted(set(signals))


def _ranking_v4_cost_effective_date(
    repository: ReplayEvidenceRepository,
    *,
    instrument_id: str,
    decision_date: date,
) -> date | None:
    metadata_reader = getattr(repository, "instrument_rule_metadata_on", None)
    fee_reader = getattr(repository, "fee_rules_on", None)
    if not callable(metadata_reader) or not callable(fee_reader):
        return None
    try:
        metadata = metadata_reader(instrument_id, decision_date)
        fee_rules = fee_reader(
            fee_schedule_version=metadata.fee_schedule_version,
            fee_rule_key=metadata.fee_rule_key,
            trade_date=decision_date,
        )
    except (LookupError, ReplayEvidenceUnavailable, ValueError):
        return None
    effective_dates = [
        metadata.effective_from,
        *(item.effective_from for item in fee_rules),
    ]
    if not fee_rules or any(item > decision_date for item in effective_dates):
        return None
    return max(effective_dates)


def _enrich_selection_constraints(
    snapshots: list[WalkForwardSnapshot],
    *,
    repository: ReplayEvidenceRepository,
    revision: int,
    asset_types: dict[str, str],
) -> list[WalkForwardSnapshot]:
    enriched = []
    for snapshot in snapshots:
        source_items = list(
            {
                item.instrument_id: item
                for item in [
                    *snapshot.candidate_pool,
                    *snapshot.ranking_v4_candidate_pool,
                    *snapshot.top_10,
                ]
            }.values()
        )
        instrument_ids = [item.instrument_id for item in source_items]
        fundamentals = repository.fundamentals_as_of(
            instrument_ids,
            snapshot.decision_date,
            revision,
        )
        industries = repository.industries_as_of(
            instrument_ids,
            snapshot.decision_date,
            revision,
        )
        memberships, incomplete_index_snapshots = repository.available_memberships_as_of(
            instrument_ids,
            snapshot.decision_date,
            revision,
        )
        adjusted_bar_counts = repository.adjusted_bar_vintage_counts(
            instrument_ids,
            snapshot.decision_date - timedelta(days=365),
            snapshot.decision_date,
            revision,
        )
        updated_by_instrument = {}
        for selection in source_items:
            fundamental_snapshot = fundamentals.get(selection.instrument_id)
            industry_snapshot = industries.get(selection.instrument_id)
            membership_rows = [
                item
                for item in memberships.get(selection.instrument_id, [])
                if item.snapshot_date <= snapshot.decision_date
            ]
            asset_type = selection.asset_type
            if asset_type == "unknown":
                asset_type = asset_types.get(selection.instrument_id, "unknown")
            if asset_type == "1":
                asset_type = "stock"
            normalized_asset_type = asset_type.strip().lower()
            is_stock = normalized_asset_type in {"stock", "equity"}
            is_etf = normalized_asset_type in {"etf", "fund", "index_fund"}
            fundamental_as_of = getattr(fundamental_snapshot, "as_of_date", None)
            fundamental_evidence_complete = not is_stock or (
                fundamental_as_of is not None
                and fundamental_as_of <= snapshot.decision_date
                and _has_usable_fundamental(fundamental_snapshot)
            )
            industry_snapshot_date = (
                industry_snapshot.snapshot_date
                if (
                    industry_snapshot is not None
                    and industry_snapshot.snapshot_date <= snapshot.decision_date
                )
                else None
            )
            industry = (
                industry_snapshot.industry.strip()
                if (
                    industry_snapshot_date is not None
                    and industry_snapshot.industry.strip()
                    and industry_snapshot.industry.strip().lower()
                    not in {"unknown", "未知", "未分类"}
                )
                else None
            )
            industry_evidence_complete = industry is not None
            membership_snapshot_date = _latest_evidence_date(
                *(item.snapshot_date for item in membership_rows)
            )
            cost_effective_date = _ranking_v4_cost_effective_date(
                repository,
                instrument_id=selection.instrument_id,
                decision_date=snapshot.decision_date,
            )
            temporal_evidence_complete = (
                fundamental_evidence_complete
            ) and adjusted_bar_counts.get(
                selection.instrument_id, 0
            ) >= RANKING_V4_MIN_COMMON_RETURN_OBSERVATIONS + 1
            asset_type_complete = normalized_asset_type not in {
                "",
                "unknown",
                "未知",
            }
            underlying_evidence_complete = not is_etf
            raw_metadata_complete = (
                asset_type_complete and industry_evidence_complete and underlying_evidence_complete
            )
            updated_by_instrument[selection.instrument_id] = selection.model_copy(
                update={
                    "asset_type": asset_type,
                    "industry": industry,
                    "index_memberships": sorted({item.index_id for item in membership_rows}),
                    "underlying_ids": [],
                    "ranking_v4_fundamental_as_of": fundamental_as_of,
                    "ranking_v4_industry_snapshot_date": (industry_snapshot_date),
                    "ranking_v4_index_membership_snapshot_date": (membership_snapshot_date),
                    "ranking_v4_cost_effective_date": cost_effective_date,
                    "ranking_v4_temporal_evidence_complete": (temporal_evidence_complete),
                    "ranking_v4_fundamental_evidence_complete": (fundamental_evidence_complete),
                    "ranking_v4_industry_evidence_complete": (industry_evidence_complete),
                    "ranking_v4_underlying_evidence_complete": (underlying_evidence_complete),
                    "ranking_v4_underlying_evidence_mode": (
                        "unknown_no_holdings" if is_etf else "not_applicable"
                    ),
                    "ranking_v4_raw_metadata_complete": raw_metadata_complete,
                    "ranking_v4_return_risk_evidence_complete": False,
                    "ranking_v4_combined_constraint_evidence_complete": False,
                }
            )
        updated_v4_pool = _with_ranking_v4_industry_strength(
            [
                updated_by_instrument[item.instrument_id].model_copy(update={"themes": []})
                for item in snapshot.ranking_v4_candidate_pool
            ]
        )
        enriched.append(
            snapshot.model_copy(
                update={
                    "top_10": [
                        updated_by_instrument[item.instrument_id] for item in snapshot.top_10
                    ],
                    "candidate_pool": [
                        updated_by_instrument[item.instrument_id]
                        for item in snapshot.candidate_pool
                    ],
                    "ranking_v4_candidate_pool": updated_v4_pool,
                    "top_5": [
                        updated_by_instrument.get(item.instrument_id, item)
                        for item in snapshot.top_5
                    ],
                    "rerank_incomplete_index_snapshot_count": len(incomplete_index_snapshots),
                }
            )
        )
    return enriched


def _ranking_v4_risk_evidence(
    snapshot: WalkForwardSnapshot,
    *,
    market_provider: ReplayMarketDataProvider,
) -> tuple[list[WalkForwardSelection], dict[tuple[str, str], float]]:
    pool = snapshot.ranking_v4_candidate_pool
    instrument_ids = [item.instrument_id for item in pool]
    if not instrument_ids:
        return [], {}
    bars = market_provider.get_daily_bars(
        sorted({*instrument_ids, RANKING_V4_RISK_BENCHMARK_ID}),
        snapshot.decision_date - timedelta(days=365),
        snapshot.decision_date,
    )
    if not bars.empty:
        parsed_trade_dates = pd.to_datetime(bars["trade_date"], errors="coerce")
        bars = bars.loc[
            parsed_trade_dates.notna() & (parsed_trade_dates.dt.date <= snapshot.decision_date)
        ].copy()
    if bars.empty:
        return [
            item.model_copy(
                update={
                    "ranking_v4_beta": None,
                    "ranking_v4_return_observation_count": 0,
                    "ranking_v4_bar_end_date": None,
                    "ranking_v4_temporal_evidence_complete": False,
                    "ranking_v4_constraint_data_complete": False,
                    "ranking_v4_constraint_evidence_mode": "incomplete",
                    "ranking_v4_return_risk_evidence_complete": False,
                    "ranking_v4_combined_constraint_evidence_complete": False,
                }
            )
            for item in pool
        ], {}

    benchmark_frame = bars.loc[bars["instrument_id"] == RANKING_V4_RISK_BENCHMARK_ID]
    benchmark_returns = _ranking_v4_return_series(
        benchmark_frame,
        adjusted_required=False,
    )
    returns_by_instrument: dict[str, dict[date, float]] = {}
    beta_by_instrument: dict[str, float] = {}
    bar_end_by_instrument: dict[str, date] = {}
    for instrument_id in instrument_ids:
        frame = bars.loc[bars["instrument_id"] == instrument_id]
        bar_end = _ranking_v4_bar_end_date(frame)
        if bar_end is not None:
            bar_end_by_instrument[instrument_id] = bar_end
        returns = _ranking_v4_return_series(
            frame,
            adjusted_required=True,
        )
        returns_by_instrument[instrument_id] = returns
        beta = _ranking_v4_beta(returns, benchmark_returns)
        if beta is not None:
            beta_by_instrument[instrument_id] = beta

    correlations: dict[tuple[str, str], float] = {}
    for left_index, left in enumerate(instrument_ids):
        for right in instrument_ids[left_index + 1 :]:
            correlation = _ranking_v4_correlation(
                returns_by_instrument.get(left, {}),
                returns_by_instrument.get(right, {}),
            )
            if correlation is not None:
                correlations[(left, right)] = correlation

    updated = []
    for selection in pool:
        asset_type = selection.asset_type.strip().lower()
        industry = (selection.industry or "").strip().lower()
        beta = beta_by_instrument.get(selection.instrument_id)
        return_observation_count = len(returns_by_instrument.get(selection.instrument_id, {}))
        bar_end_date = bar_end_by_instrument.get(selection.instrument_id)
        asset_type_complete = asset_type not in {"", "unknown", "未知"}
        metadata_complete = selection.ranking_v4_raw_metadata_complete and industry not in {
            "",
            "unknown",
            "未知",
            "未分类",
        }
        return_risk_complete = (
            asset_type_complete
            and beta is not None
            and return_observation_count >= RANKING_V4_MIN_COMMON_RETURN_OBSERVATIONS
        )
        combined_constraint_complete = metadata_complete and return_risk_complete
        constraint_evidence_mode = (
            "point_in_time_metadata"
            if combined_constraint_complete
            else "return_risk_proxy"
            if return_risk_complete
            else "incomplete"
        )
        temporal_evidence_complete = (
            selection.ranking_v4_fundamental_evidence_complete
            and bar_end_date is not None
            and bar_end_date <= snapshot.decision_date
            and return_observation_count >= RANKING_V4_MIN_COMMON_RETURN_OBSERVATIONS
        )
        updated.append(
            selection.model_copy(
                update={
                    "ranking_v4_beta": beta,
                    "ranking_v4_return_observation_count": (return_observation_count),
                    "ranking_v4_bar_end_date": bar_end_date,
                    "ranking_v4_temporal_evidence_complete": (temporal_evidence_complete),
                    "ranking_v4_constraint_data_complete": (return_risk_complete),
                    "ranking_v4_constraint_evidence_mode": (constraint_evidence_mode),
                    "ranking_v4_return_risk_evidence_complete": (return_risk_complete),
                    "ranking_v4_combined_constraint_evidence_complete": (
                        combined_constraint_complete
                    ),
                }
            )
        )
    return updated, correlations


def _ranking_v4_bar_end_date(frame: pd.DataFrame) -> date | None:
    if frame.empty or "adjusted_close" not in frame.columns:
        return None
    valid = frame.loc[frame["adjusted_close"].notna(), ["trade_date", "adjusted_close"]].copy()
    if valid.empty:
        return None
    valid["_adjusted_close"] = pd.to_numeric(valid["adjusted_close"], errors="coerce")
    valid = valid.loc[
        valid["_adjusted_close"].map(
            lambda value: value is not None and math.isfinite(value) and value > 0
        )
    ]
    if valid.empty:
        return None
    value = valid["trade_date"].max()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _ranking_v4_return_series(
    frame: pd.DataFrame,
    *,
    adjusted_required: bool,
) -> dict[date, float]:
    if frame.empty:
        return {}
    ordered = frame.sort_values("trade_date")
    if adjusted_required:
        if "adjusted_close" not in ordered.columns:
            return {}
        ordered = ordered.loc[ordered["adjusted_close"].notna()].copy()
        price_column = "adjusted_close"
    else:
        if "adjusted_close" in ordered.columns:
            ordered = ordered.copy()
            ordered["_risk_price"] = ordered["adjusted_close"].where(
                ordered["adjusted_close"].notna(),
                ordered["close"],
            )
            price_column = "_risk_price"
        else:
            price_column = "close"
    if len(ordered) < 2:
        return {}
    ordered = ordered.tail(RANKING_V4_RISK_LOOKBACK_SESSIONS + 1)
    result: dict[date, float] = {}
    previous: float | None = None
    for trade_date, raw_value in zip(
        ordered["trade_date"],
        ordered[price_column],
        strict=True,
    ):
        value = float(raw_value)
        if not math.isfinite(value) or value <= 0:
            previous = None
            continue
        if previous is not None:
            result[trade_date] = value / previous - 1.0
        previous = value
    return result


def _ranking_v4_beta(
    candidate_returns: dict[date, float],
    benchmark_returns: dict[date, float],
) -> float | None:
    common_dates = sorted(set(candidate_returns).intersection(benchmark_returns))
    if len(common_dates) < RANKING_V4_MIN_COMMON_RETURN_OBSERVATIONS:
        return None
    candidate_values = [candidate_returns[item] for item in common_dates]
    benchmark_values = [benchmark_returns[item] for item in common_dates]
    benchmark_mean = statistics.fmean(benchmark_values)
    candidate_mean = statistics.fmean(candidate_values)
    variance = sum((value - benchmark_mean) ** 2 for value in benchmark_values)
    if variance <= 0:
        return None
    beta = (
        sum(
            (benchmark - benchmark_mean) * (candidate - candidate_mean)
            for benchmark, candidate in zip(
                benchmark_values,
                candidate_values,
                strict=True,
            )
        )
        / variance
    )
    if not math.isfinite(beta) or beta < 0:
        return None
    return round(beta, 8)


def _ranking_v4_correlation(
    left_returns: dict[date, float],
    right_returns: dict[date, float],
) -> float | None:
    common_dates = sorted(set(left_returns).intersection(right_returns))
    if len(common_dates) < RANKING_V4_MIN_COMMON_RETURN_OBSERVATIONS:
        return None
    left_values = [left_returns[item] for item in common_dates]
    right_values = [right_returns[item] for item in common_dates]
    left_mean = statistics.fmean(left_values)
    right_mean = statistics.fmean(right_values)
    left_sum = sum((value - left_mean) ** 2 for value in left_values)
    right_sum = sum((value - right_mean) ** 2 for value in right_values)
    if left_sum <= 0 or right_sum <= 0:
        return None
    covariance = sum(
        (left - left_mean) * (right - right_mean)
        for left, right in zip(left_values, right_values, strict=True)
    )
    correlation = covariance / math.sqrt(left_sum * right_sum)
    if not math.isfinite(correlation):
        return None
    return round(max(-1.0, min(correlation, 1.0)), 8)


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
        snapshot.decision_date: snapshot.benchmark_trend_state for snapshot in snapshots
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
                factor_signals=(selection.factor_signals if selection is not None else []),
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
        source_by_instrument = {item.instrument_id: item for item in snapshot.top_10}
        (
            constrained_scores,
            constraint_blocked_count,
            evidence_blocked_count,
            hysteresis_blocked_count,
        ) = _select_constrained_dynamic_scores(
            decision.candidates,
            source_by_instrument=source_by_instrument,
            limit=5,
            baseline_instrument_ids=[item.instrument_id for item in snapshot.top_5],
            strategy_limit=snapshot.strategy_diversification_limit,
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
                        "rerank_expected_net_return_pct": (score.expected_net_return_pct),
                        "rerank_expected_return_lower_bound_pct": (
                            score.expected_return_lower_bound_pct
                        ),
                        "rerank_win_probability": score.win_probability,
                        "rerank_win_probability_lower_bound": (score.win_probability_lower_bound),
                        "rerank_promotion_eligible": score.promotion_eligible,
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
                    "rerank_constraint_blocked_count": (constraint_blocked_count),
                    "rerank_evidence_blocked_count": evidence_blocked_count,
                    "rerank_hysteresis_blocked_count": hysteresis_blocked_count,
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
    baseline_instrument_ids: list[str] | None = None,
    strategy_limit: int | None = None,
) -> tuple[list[RerankCandidateScore], int, int, int]:
    if baseline_instrument_ids is not None:
        return _select_anchored_dynamic_scores(
            scores,
            source_by_instrument=source_by_instrument,
            baseline_instrument_ids=baseline_instrument_ids,
            limit=limit,
            strategy_limit=strategy_limit,
        )

    selected = []
    blocked = 0
    for score in scores:
        trial = [*selected, score]
        if not _dynamic_selection_constraints_hold(
            trial,
            source_by_instrument=source_by_instrument,
            strategy_limit=strategy_limit,
        ):
            blocked += 1
            continue
        selected.append(score)
        if len(selected) >= limit:
            break
    return selected, blocked, 0, 0


def _select_anchored_dynamic_scores(
    scores: list[RerankCandidateScore],
    *,
    source_by_instrument: dict[str, WalkForwardSelection],
    baseline_instrument_ids: list[str],
    limit: int,
    strategy_limit: int | None,
) -> tuple[list[RerankCandidateScore], int, int, int]:
    score_by_instrument = {item.instrument_id: item for item in scores}
    selected = [
        score_by_instrument[instrument_id]
        for instrument_id in baseline_instrument_ids
        if instrument_id in score_by_instrument
    ][:limit]
    baseline_set = {item.instrument_id for item in selected}
    constraint_blocked = 0
    evidence_blocked = 0
    hysteresis_blocked = 0

    for challenger in scores:
        if challenger.instrument_id in baseline_set:
            continue
        if not challenger.promotion_eligible:
            evidence_blocked += 1
            continue
        if len(selected) < limit:
            trial = [*selected, challenger]
            if _dynamic_selection_constraints_hold(
                trial,
                source_by_instrument=source_by_instrument,
                strategy_limit=strategy_limit,
            ):
                selected = trial
            else:
                constraint_blocked += 1
            continue

        replacement_candidates = sorted(
            selected,
            key=lambda item: (
                item.rerank_score,
                -item.baseline_position,
                item.instrument_id,
            ),
        )
        material_replacements = [
            incumbent
            for incumbent in replacement_candidates
            if challenger.rerank_score >= incumbent.rerank_score + RERANK_PROMOTION_MARGIN
        ]
        if not material_replacements:
            hysteresis_blocked += 1
            continue
        replaced = False
        for incumbent in material_replacements:
            trial = [
                challenger if item.instrument_id == incumbent.instrument_id else item
                for item in selected
            ]
            if not _dynamic_selection_constraints_hold(
                trial,
                source_by_instrument=source_by_instrument,
                strategy_limit=strategy_limit,
            ):
                continue
            selected = trial
            replaced = True
            break
        if not replaced:
            constraint_blocked += 1

    return (
        sorted(
            selected,
            key=lambda item: (
                -item.rerank_score,
                item.baseline_position,
                item.instrument_id,
            ),
        ),
        constraint_blocked,
        evidence_blocked,
        hysteresis_blocked,
    )


def _dynamic_selection_constraints_hold(
    scores: list[RerankCandidateScore],
    *,
    source_by_instrument: dict[str, WalkForwardSelection],
    strategy_limit: int | None,
) -> bool:
    industry_counts: dict[str, int] = {}
    strategy_counts: dict[str, int] = {}
    etf_overlap_counts: dict[str, int] = {}
    for score in scores:
        source = source_by_instrument[score.instrument_id]
        strategy = (source.primary_strategy_id or "").strip()
        if strategy and strategy_limit is not None:
            strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1
            if strategy_counts[strategy] > strategy_limit:
                return False
        industry = (source.industry or "").strip()
        constrained_industry = (
            industry
            if industry and industry.lower() not in {"unknown", "综合", "指数etf", "etf"}
            else None
        )
        if constrained_industry is not None:
            industry_counts[constrained_industry] = industry_counts.get(constrained_industry, 0) + 1
            if industry_counts[constrained_industry] > 2:
                return False
        if source.asset_type.lower() not in {"etf", "fund", "index_fund"}:
            continue
        for key in source.index_memberships:
            etf_overlap_counts[key] = etf_overlap_counts.get(key, 0) + 1
            if etf_overlap_counts[key] > 1:
                return False
    return True


def _build_ranking_v4_observations(
    snapshots: list[WalkForwardSnapshot],
    *,
    ledger: CandidateOutcomeLedgerResult,
    market_provider: ReplayMarketDataProvider,
    start: date,
    end: date,
) -> list[ResolvedRankingV4Observation]:
    selection_by_key = {
        (snapshot.decision_date, item.instrument_id): item
        for snapshot in snapshots
        for item in snapshot.ranking_v4_candidate_pool
    }
    regime_by_date = {
        snapshot.decision_date: snapshot.benchmark_trend_state for snapshot in snapshots
    }
    benchmark_series = _benchmark_price_series(
        market_provider,
        start=start,
        end=end,
    )
    observations: list[ResolvedRankingV4Observation] = []
    for outcome in ledger.outcomes:
        selection = selection_by_key.get((outcome.signal_date, outcome.instrument_id))
        if (
            selection is None
            or outcome.resolved_at is None
            or not selection.ranking_v4_temporal_evidence_complete
        ):
            continue
        triggered = outcome.status == CandidateOutcomeStatus.RESOLVED
        if triggered:
            if (
                outcome.entry_date is None
                or outcome.exit_date is None
                or outcome.return_pct is None
                or not math.isfinite(float(outcome.return_pct))
            ):
                continue
            benchmark_return = _composite_benchmark_return(
                benchmark_series,
                start=outcome.entry_date,
                end=outcome.exit_date,
            )
            if benchmark_return is None:
                continue
            available_at = outcome.resolved_at
            return_pct = float(outcome.return_pct)
            net_excess = round(return_pct - benchmark_return, 4)
            semantic_status = "resolved"
        elif (
            outcome.status == CandidateOutcomeStatus.NOT_TRIGGERED_OR_UNFILLABLE
            and outcome.status_detail in RANKING_V4_VALID_CASH_DETAILS
        ):
            maturity_date = trading_day_offset(
                outcome.signal_date,
                25,
            )
            benchmark_return = _composite_benchmark_return(
                benchmark_series,
                start=outcome.signal_date,
                end=maturity_date,
            )
            if benchmark_return is None:
                continue
            available_at = max(outcome.resolved_at, maturity_date)
            return_pct = None
            net_excess = None
            semantic_status = "not_triggered"
        else:
            continue
        observations.append(
            ResolvedRankingV4Observation(
                instrument_id=outcome.instrument_id,
                signal_date=outcome.signal_date,
                available_at=available_at,
                outcome_status=semantic_status,
                triggered=triggered,
                return_pct=return_pct,
                benchmark_return_pct=benchmark_return,
                cost_adjusted_net_excess_return_pct=net_excess,
                primary_strategy_id=outcome.strategy_id,
                primary_channel=selection.ranking_v4_primary_channel,
                factor_signals=selection.factor_signals,
                market_regime=regime_by_date.get(
                    outcome.signal_date,
                    "unknown",
                ),
                asset_type=selection.asset_type,
                industry=selection.industry,
                features=selection.ranking_v4_features,
            )
        )
    return observations


def _ranking_v4_evidence_coverage(
    snapshots: list[WalkForwardSnapshot],
) -> dict[str, float]:
    rows = [
        (snapshot.decision_date, selection)
        for snapshot in snapshots
        for selection in snapshot.ranking_v4_candidate_pool
    ]
    candidate_date_count = len(rows)
    instrument_ids = {selection.instrument_id for _, selection in rows}
    decision_dates = {decision_date for decision_date, _ in rows}
    result = {
        "candidate_count": float(candidate_date_count),
        "candidate_date_count": float(candidate_date_count),
        "distinct_instrument_count": float(len(instrument_ids)),
        "distinct_decision_date_count": float(len(decision_dates)),
    }

    def add_coverage(
        key: str,
        predicate: Callable[[WalkForwardSelection], bool],
    ) -> None:
        covered = [
            (decision_date, selection) for decision_date, selection in rows if predicate(selection)
        ]
        counts = {
            "candidate_date": len(covered),
            "distinct_instrument": len({selection.instrument_id for _, selection in covered}),
            "distinct_decision_date": len({decision_date for decision_date, _ in covered}),
        }
        denominators = {
            "candidate_date": candidate_date_count,
            "distinct_instrument": len(instrument_ids),
            "distinct_decision_date": len(decision_dates),
        }
        for dimension, count in counts.items():
            result[f"{key}_{dimension}_count"] = float(count)
            denominator = denominators[dimension]
            result[f"{key}_{dimension}_ratio"] = (
                round(count / denominator, 8) if denominator else 0.0
            )

    add_coverage(
        "raw_metadata",
        lambda item: item.ranking_v4_raw_metadata_complete,
    )
    add_coverage(
        "return_risk",
        lambda item: item.ranking_v4_return_risk_evidence_complete,
    )
    add_coverage(
        "combined_constraint",
        lambda item: item.ranking_v4_combined_constraint_evidence_complete,
    )
    add_coverage(
        "constraint_mode_point_in_time_metadata",
        lambda item: item.ranking_v4_constraint_evidence_mode == "point_in_time_metadata",
    )
    add_coverage(
        "constraint_mode_return_risk_proxy",
        lambda item: item.ranking_v4_constraint_evidence_mode == "return_risk_proxy",
    )
    add_coverage(
        "constraint_mode_incomplete",
        lambda item: item.ranking_v4_constraint_evidence_mode == "incomplete",
    )
    return result


def _ranking_v4_gate_decomposition(
    *,
    candidate_ids: list[str],
    selected_ids: set[str],
    scoring_reasons: dict[str, list[str]],
    portfolio_reasons: dict[str, list[str]],
) -> tuple[
    dict[str, list[str]],
    dict[str, int],
    dict[str, int],
    dict[str, int],
]:
    reasons_by_instrument: dict[str, list[str]] = {}
    all_reason_counts: dict[str, int] = {}
    first_reason_counts: dict[str, int] = {}
    for instrument_id in candidate_ids:
        reasons = list(
            dict.fromkeys(
                [
                    *scoring_reasons.get(instrument_id, []),
                    *portfolio_reasons.get(instrument_id, []),
                ]
            )
        )
        reasons_by_instrument[instrument_id] = reasons
        if not reasons:
            continue
        first_reason_counts[reasons[0]] = first_reason_counts.get(reasons[0], 0) + 1
        for reason in reasons:
            all_reason_counts[reason] = all_reason_counts.get(reason, 0) + 1

    candidate_id_set = set(candidate_ids)
    blocked_ids = {
        instrument_id for instrument_id, reasons in reasons_by_instrument.items() if reasons
    }
    accounted_ids = selected_ids.union(blocked_ids).intersection(candidate_id_set)
    reconciliation = {
        "candidate_count": len(candidate_ids),
        "selected_candidate_count": len(selected_ids.intersection(candidate_id_set)),
        "blocked_candidate_count": len(blocked_ids),
        "selected_blocked_overlap_count": len(selected_ids.intersection(blocked_ids)),
        "accounted_candidate_count": len(accounted_ids),
        "unaccounted_candidate_count": len(candidate_id_set.difference(accounted_ids)),
        "all_blocked_reason_assignment_count": sum(all_reason_counts.values()),
        "first_blocked_reason_assignment_count": sum(first_reason_counts.values()),
    }
    return (
        reasons_by_instrument,
        all_reason_counts,
        first_reason_counts,
        reconciliation,
    )


def _apply_ranking_v4(
    snapshots: list[WalkForwardSnapshot],
    *,
    observations: list[ResolvedRankingV4Observation],
    market_provider: ReplayMarketDataProvider,
) -> list[WalkForwardSnapshot]:
    updated: list[WalkForwardSnapshot] = []
    previous_v4_ids: list[str] = []
    for snapshot in snapshots:
        risk_pool, pairwise_correlations = _ranking_v4_risk_evidence(
            snapshot,
            market_provider=market_provider,
        )
        candidates = [
            _ranking_v4_candidate_from_selection(
                item,
                decision_date=snapshot.decision_date,
                market_regime=snapshot.benchmark_trend_state,
                market_features_complete=(snapshot.ranking_v4_market_features_complete),
                incumbent=item.instrument_id in previous_v4_ids,
            )
            for item in risk_pool
        ]
        decision = score_ranking_v4_candidates(
            candidates,
            observations,
            decision_date=snapshot.decision_date,
        )
        portfolio = select_ranking_v4_portfolio(
            decision,
            candidates,
            pairwise_correlations=pairwise_correlations,
        )
        baseline_decision = _ranking_v4_baseline_decision(
            candidates,
            decision_date=snapshot.decision_date,
        )
        baseline_portfolio = select_ranking_v4_portfolio(
            baseline_decision,
            candidates,
            pairwise_correlations=pairwise_correlations,
        )
        source_by_instrument = {item.instrument_id: item for item in risk_pool}
        score_by_instrument = {item.instrument_id: item for item in decision.candidates}
        selected = [
            _selection_with_ranking_v4_score(
                source_by_instrument[item.instrument_id],
                score_by_instrument[item.instrument_id],
                position=item.position,
            )
            for item in portfolio.selected
        ]
        baseline = [
            source_by_instrument[item.instrument_id] for item in baseline_portfolio.selected
        ]
        previous_v4_ids = [item.instrument_id for item in selected]
        (
            blocked_reasons_by_instrument,
            blocked_reason_counts,
            first_blocked_reason_counts,
            gate_reconciliation,
        ) = _ranking_v4_gate_decomposition(
            candidate_ids=[item.instrument_id for item in risk_pool],
            selected_ids={item.instrument_id for item in portfolio.selected},
            scoring_reasons={
                item.instrument_id: list(item.blocked_reasons) for item in decision.candidates
            },
            portfolio_reasons={
                item.instrument_id: list(item.reasons) for item in portfolio.blocked
            },
        )
        audited_risk_pool = []
        for item in risk_pool:
            score = score_by_instrument.get(item.instrument_id)
            audited = (
                _selection_with_ranking_v4_score(
                    item,
                    score,
                    position=score.v4_position,
                )
                if score is not None
                else item
            )
            reasons = blocked_reasons_by_instrument.get(item.instrument_id, [])
            audited_risk_pool.append(
                audited.model_copy(
                    update={
                        "ranking_v4_blocked_reasons": reasons,
                        "ranking_v4_first_blocked_reason": (reasons[0] if reasons else None),
                    }
                )
            )

        def coverage_count(predicate) -> float:
            return float(sum(predicate(item) for item in risk_pool))

        evidence_coverage = _ranking_v4_evidence_coverage(
            [snapshot.model_copy(update={"ranking_v4_candidate_pool": risk_pool})]
        )
        evidence_coverage.update(
            {
                "temporal_complete_count": coverage_count(
                    lambda item: item.ranking_v4_temporal_evidence_complete
                ),
                "constraint_complete_count": coverage_count(
                    lambda item: item.ranking_v4_constraint_data_complete
                ),
                "point_in_time_metadata_count": coverage_count(
                    lambda item: (
                        item.ranking_v4_constraint_evidence_mode == "point_in_time_metadata"
                    )
                ),
                "return_risk_proxy_count": coverage_count(
                    lambda item: item.ranking_v4_constraint_evidence_mode == "return_risk_proxy"
                ),
                "raw_metadata_count": coverage_count(
                    lambda item: item.ranking_v4_raw_metadata_complete
                ),
                "return_risk_count": coverage_count(
                    lambda item: item.ranking_v4_return_risk_evidence_complete
                ),
                "combined_constraint_count": coverage_count(
                    lambda item: item.ranking_v4_combined_constraint_evidence_complete
                ),
                "industry_evidence_count": coverage_count(
                    lambda item: item.ranking_v4_industry_evidence_complete
                ),
                "underlying_evidence_count": coverage_count(
                    lambda item: item.ranking_v4_underlying_evidence_complete
                ),
                "beta_evidence_count": coverage_count(
                    lambda item: item.ranking_v4_beta is not None
                ),
                "pairwise_correlation_count": float(len(pairwise_correlations)),
                "feature_adjusted_candidate_count": float(
                    sum(item.feature_adjustment_count > 0 for item in decision.candidates)
                ),
                "ranking_eligible_count": float(decision.eligible_position_count),
                "selected_count": float(portfolio.selected_count),
            }
        )
        updated.append(
            snapshot.model_copy(
                update={
                    "ranking_v4_candidate_pool": audited_risk_pool,
                    "ranking_v4_constraint_matched_baseline_top_5": baseline,
                    "ranking_v4_top_5": selected,
                    "ranking_v4_training_cutoff_date": (decision.training_cutoff_date),
                    "ranking_v4_training_observation_count": (decision.training_observation_count),
                    "ranking_v4_training_date_count": (decision.training_date_count),
                    "ranking_v4_model_ready": decision.model_ready,
                    "ranking_v4_cash_slot_count": portfolio.cash_slot_count,
                    "ranking_v4_constraint_blocked_count": len(portfolio.blocked),
                    "ranking_v4_portfolio": portfolio,
                    "ranking_v4_pairwise_correlations": {
                        _ranking_v4_pair_key(left, right): value
                        for (left, right), value in sorted(pairwise_correlations.items())
                    },
                    "ranking_v4_evidence_coverage": evidence_coverage,
                    "ranking_v4_blocked_reason_counts": (blocked_reason_counts),
                    "ranking_v4_first_blocked_reason_counts": (first_blocked_reason_counts),
                    "ranking_v4_gate_reconciliation": gate_reconciliation,
                }
            )
        )
    return updated


def _ranking_v4_pair_key(left: str, right: str) -> str:
    return "|".join(sorted((left, right)))


def _ranking_v4_pairwise_correlations(
    snapshot: WalkForwardSnapshot,
) -> dict[tuple[str, str], float]:
    result: dict[tuple[str, str], float] = {}
    for key, value in snapshot.ranking_v4_pairwise_correlations.items():
        parts = key.split("|", maxsplit=1)
        if len(parts) != 2:
            continue
        result[(parts[0], parts[1])] = value
    return result


def _ranking_v4_baseline_decision(
    candidates: list[RankingV4Candidate],
    *,
    decision_date: date,
) -> RankingV4Decision:
    ordered = sorted(
        candidates,
        key=lambda item: (-item.baseline_rank_score, item.instrument_id),
    )
    scores: list[RankingV4CandidateScore] = []
    for position, candidate in enumerate(ordered, start=1):
        costs_complete = all(
            value is not None
            for value in (
                candidate.replacement_cost_pct,
                candidate.benchmark_opportunity_cost_pct,
                candidate.liquidity_penalty_pct,
                candidate.tail_risk_penalty_pct,
            )
        )
        blocked = []
        if (
            candidate.decision_date != decision_date
            or candidate.feature_as_of is None
            or candidate.feature_as_of > decision_date
            or candidate.market_regime_as_of is None
            or candidate.market_regime_as_of > decision_date
            or candidate.constraint_as_of is None
            or candidate.constraint_as_of > decision_date
            or candidate.cost_as_of is None
            or candidate.cost_as_of > decision_date
        ):
            blocked.append("temporal_evidence_incomplete")
        if not candidate.point_in_time_evidence_complete:
            blocked.append("point_in_time_evidence_incomplete")
        if not candidate.market_regime_features_complete:
            blocked.append("market_regime_evidence_incomplete")
        if not candidate.constraint_data_complete:
            blocked.append("constraint_evidence_incomplete")
        if candidate.features.data_completeness < 0.68:
            blocked.append("data_incomplete")
        if not costs_complete:
            blocked.append("cost_evidence_incomplete")
        baseline_utility = max(0.0, float(candidate.baseline_rank_score))
        scores.append(
            RankingV4CandidateScore(
                instrument_id=candidate.instrument_id,
                baseline_position=position,
                v4_position=position,
                baseline_rank_score=round(
                    float(candidate.baseline_rank_score),
                    8,
                ),
                expected_utility_pct=baseline_utility,
                expected_utility_lower_bound_pct=baseline_utility,
                eligible_for_position=not blocked and baseline_utility > 0,
                blocked_reasons=blocked,
                reason=("constraint_matched_baseline" if not blocked else ",".join(blocked)),
            )
        )
    eligible_count = sum(item.eligible_for_position for item in scores)
    return RankingV4Decision(
        decision_date=decision_date,
        training_observation_count=0,
        training_triggered_observation_count=0,
        training_date_count=0,
        model_ready=False,
        eligible_position_count=eligible_count,
        cash_slot_count=max(5 - min(5, eligible_count), 0),
        candidates=scores,
    )


def _selection_with_ranking_v4_score(
    selection: WalkForwardSelection,
    score: RankingV4CandidateScore,
    *,
    position: int,
) -> WalkForwardSelection:
    return selection.model_copy(
        update={
            "ranking_v4_score": score.expected_utility_lower_bound_pct,
            "ranking_v4_position": position,
            "ranking_v4_training_dates": score.evidence_date_count,
            "ranking_v4_trigger_probability": score.trigger_probability,
            "ranking_v4_expected_net_excess_pct": (score.expected_triggered_net_excess_return_pct),
            "ranking_v4_net_excess_lower_bound_pct": (
                score.expected_triggered_net_excess_lower_bound_pct
            ),
            "ranking_v4_expected_utility_pct": (score.expected_utility_pct),
            "ranking_v4_utility_lower_bound_pct": (score.expected_utility_lower_bound_pct),
            "ranking_v4_blocked_reasons": list(score.blocked_reasons),
            "ranking_v4_reason": score.reason,
            "ranking_v4_replacement_cost_pct": (score.replacement_cost_pct),
            "ranking_v4_benchmark_opportunity_cost_pct": (score.benchmark_opportunity_cost_pct),
            "ranking_v4_liquidity_penalty_pct": (score.liquidity_penalty_pct),
            "ranking_v4_tail_risk_penalty_pct": (score.tail_risk_penalty_pct),
        }
    )


def _build_ranking_v3_observations(
    snapshots: list[WalkForwardSnapshot],
    *,
    ledger: CandidateOutcomeLedgerResult,
    market_provider: ReplayMarketDataProvider,
    start: date,
    end: date,
) -> list[ResolvedRankingV3Observation]:
    selection_by_key = {
        (snapshot.decision_date, item.instrument_id): item
        for snapshot in snapshots
        for item in snapshot.candidate_pool
    }
    regime_by_date = {
        snapshot.decision_date: snapshot.benchmark_trend_state for snapshot in snapshots
    }
    benchmark_series = _benchmark_price_series(
        market_provider,
        start=start,
        end=end,
    )
    observations: list[ResolvedRankingV3Observation] = []
    for outcome in ledger.outcomes:
        selection = selection_by_key.get((outcome.signal_date, outcome.instrument_id))
        if selection is None:
            continue
        valid_outcome_return = _ranking_v3_valid_outcome_return(outcome)
        if valid_outcome_return is None:
            continue
        if outcome.resolved_at is None:
            continue
        benchmark_return = None
        net_excess = None
        available_at = outcome.resolved_at
        if outcome.status == CandidateOutcomeStatus.RESOLVED:
            if (
                outcome.entry_date is None
                or outcome.exit_date is None
                or outcome.return_pct is None
            ):
                continue
            benchmark_return = _composite_benchmark_return(
                benchmark_series,
                start=outcome.entry_date,
                end=outcome.exit_date,
            )
            if benchmark_return is None:
                continue
            net_excess = round(outcome.return_pct - benchmark_return, 4)
        else:
            maturity_date = trading_day_offset(
                outcome.signal_date,
                RANKING_V3_ENTRY_WAIT_SESSIONS + RANKING_V3_HOLDING_SESSIONS,
            )
            benchmark_return = _composite_benchmark_return(
                benchmark_series,
                start=outcome.signal_date,
                end=maturity_date,
            )
            if benchmark_return is None:
                continue
            available_at = max(available_at, maturity_date)
            net_excess = round(-benchmark_return, 4)
        semantic_status = (
            "resolved" if outcome.status == CandidateOutcomeStatus.RESOLVED else "not_triggered"
        )
        observations.append(
            ResolvedRankingV3Observation(
                instrument_id=outcome.instrument_id,
                signal_date=outcome.signal_date,
                available_at=available_at,
                outcome_status=semantic_status,
                triggered=outcome.status == CandidateOutcomeStatus.RESOLVED,
                return_pct=valid_outcome_return,
                benchmark_return_pct=benchmark_return,
                net_excess_return_pct=net_excess,
                primary_strategy_id=outcome.strategy_id,
                factor_signals=selection.factor_signals,
                market_regime=regime_by_date.get(outcome.signal_date, "unknown"),
                asset_type=selection.asset_type,
                features=selection.ranking_features,
            )
        )
    return observations


def _apply_ranking_v3(
    snapshots: list[WalkForwardSnapshot],
    *,
    observations: list[ResolvedRankingV3Observation],
) -> list[WalkForwardSnapshot]:
    protocol = build_ranking_v3_protocol()
    updated: list[WalkForwardSnapshot] = []
    previous_v3_ids: list[str] = []
    previous_window_key: str | None = None
    for snapshot in snapshots:
        window_key, scoped_observations, evidence_cutoff_date = _ranking_v3_training_scope(
            protocol,
            observations,
            decision_date=snapshot.decision_date,
        )
        if window_key is None:
            previous_v3_ids = []
            previous_window_key = None
            updated.append(
                snapshot.model_copy(
                    update={
                        "constraint_matched_baseline_top_5": [],
                        "ranking_v3_top_5": [],
                        "ranking_v3_training_cutoff_date": None,
                        "ranking_v3_training_observation_count": 0,
                        "ranking_v3_training_date_count": 0,
                        "ranking_v3_model_ready": False,
                        "ranking_v3_constraint_blocked_count": 0,
                    }
                )
            )
            continue
        if window_key != previous_window_key:
            previous_v3_ids = []
        previous_window_key = window_key
        source_by_instrument = {item.instrument_id: item for item in snapshot.candidate_pool}
        baseline = _select_constraint_matched_baseline(
            snapshot.candidate_pool,
            limit=5,
            strategy_limit=RANKING_V3_MAX_PER_STRATEGY,
        )
        decision = score_ranking_v3_candidates(
            [
                RankingV3Candidate(
                    instrument_id=item.instrument_id,
                    baseline_rank_score=float(item.rank_score),
                    primary_strategy_id=item.primary_strategy_id,
                    factor_signals=item.factor_signals,
                    market_regime=snapshot.benchmark_trend_state,
                    asset_type=item.asset_type,
                    industry=item.industry,
                    index_memberships=item.index_memberships,
                    features=item.ranking_features,
                    incumbent=item.instrument_id in previous_v3_ids,
                )
                for item in snapshot.candidate_pool
            ],
            scoped_observations,
            decision_date=snapshot.decision_date,
            evidence_cutoff_date=evidence_cutoff_date,
        )
        selected_scores, constraint_blocked = _select_ranking_v3_scores(
            decision,
            source_by_instrument=source_by_instrument,
            limit=5,
            strategy_limit=RANKING_V3_MAX_PER_STRATEGY,
        )
        if not snapshot.market_entry_allowed:
            baseline = []
            selected_scores = []
        selections = [
            source_by_instrument[score.instrument_id].model_copy(
                update={
                    "ranking_v3_score": score.v3_score,
                    "ranking_v3_position": score.v3_position,
                    "ranking_v3_training_dates": score.training_date_count,
                    "ranking_v3_expected_net_excess_pct": (score.expected_net_excess_return_pct),
                    "ranking_v3_net_excess_lower_bound_pct": (
                        score.expected_net_excess_lower_bound_pct
                    ),
                    "ranking_v3_trigger_probability": score.trigger_probability,
                    "ranking_v3_reason": score.reason,
                }
            )
            for score in selected_scores
        ]
        previous_v3_ids = [item.instrument_id for item in selections]
        updated.append(
            snapshot.model_copy(
                update={
                    "constraint_matched_baseline_top_5": baseline,
                    "ranking_v3_top_5": selections,
                    "ranking_v3_training_cutoff_date": (decision.training_cutoff_date),
                    "ranking_v3_training_observation_count": (decision.training_observation_count),
                    "ranking_v3_training_date_count": decision.training_date_count,
                    "ranking_v3_model_ready": decision.model_ready,
                    "ranking_v3_constraint_blocked_count": constraint_blocked,
                }
            )
        )
    return updated


def _ranking_v3_training_scope(
    protocol: RankingV3Protocol,
    observations: list[ResolvedRankingV3Observation],
    *,
    decision_date: date,
) -> tuple[str | None, list[ResolvedRankingV3Observation], date | None]:
    windows = {item.key: item for item in protocol.windows}
    active_window = next(
        (
            item
            for item in protocol.windows
            if item.start_date <= decision_date
            and (item.end_date is None or decision_date <= item.end_date)
        ),
        None,
    )
    if active_window is None:
        return None, [], None

    allowed_window_keys: tuple[str, ...]
    evidence_cutoff_date: date | None
    if active_window.key == "train":
        allowed_window_keys = ("train",)
        evidence_cutoff_date = None
    elif active_window.key == "validation":
        allowed_window_keys = ("train",)
        evidence_cutoff_date = active_window.start_date
    elif active_window.key == "historical_reused_oos":
        allowed_window_keys = ("train", "validation")
        evidence_cutoff_date = active_window.start_date
    elif active_window.key == "prospective_shadow":
        allowed_window_keys = (
            "train",
            "validation",
            "historical_reused_oos",
        )
        evidence_cutoff_date = active_window.start_date
    else:
        return None, [], None

    allowed_windows = [windows[key] for key in allowed_window_keys]
    scoped = [
        observation
        for observation in observations
        if any(
            window.start_date <= observation.signal_date
            and (window.end_date is None or observation.signal_date <= window.end_date)
            for window in allowed_windows
        )
    ]
    return active_window.key, scoped, evidence_cutoff_date


def _ranking_v3_common_return_observations(
    snapshots: list[WalkForwardSnapshot],
    *,
    ledger: CandidateOutcomeLedgerResult,
) -> tuple[
    list[RankingV3ReturnObservation],
    list[RankingV3ReturnObservation],
    int,
    dict[str, int | float],
]:
    """Build paired rows without converting invalid or censored outcomes to cash."""

    outcome_by_key = {
        (outcome.signal_date, outcome.instrument_id): outcome for outcome in ledger.outcomes
    }
    baseline_rows: list[RankingV3ReturnObservation] = []
    challenger_rows: list[RankingV3ReturnObservation] = []
    completed_challenger_trades = 0
    selected_outcome_count = 0
    valid_outcome_count = 0
    invalid_outcome_count = 0
    excluded_rebalance_date_count = 0
    considered_rebalance_date_count = 0
    retained_rebalance_date_count = 0

    for snapshot in snapshots:
        if not snapshot.market_entry_allowed:
            considered_rebalance_date_count += 1
            retained_rebalance_date_count += 1
            baseline_rows.extend(
                RankingV3ReturnObservation(
                    rebalance_date=snapshot.decision_date,
                    net_return_pct=0.0,
                )
                for _ in range(RANKING_V3_MAX_POSITIONS)
            )
            challenger_rows.extend(
                RankingV3ReturnObservation(
                    rebalance_date=snapshot.decision_date,
                    net_return_pct=0.0,
                )
                for _ in range(RANKING_V3_MAX_POSITIONS)
            )
            continue
        if not (snapshot.constraint_matched_baseline_top_5 or snapshot.ranking_v3_top_5):
            continue
        considered_rebalance_date_count += 1
        paired_returns: list[tuple[list[float], int]] = []
        date_is_valid = True
        for selections, count_completed in (
            (
                snapshot.constraint_matched_baseline_top_5,
                False,
            ),
            (snapshot.ranking_v3_top_5, True),
        ):
            returns: list[float] = []
            completed_on_date = 0
            for selection in selections[:RANKING_V3_MAX_POSITIONS]:
                selected_outcome_count += 1
                outcome = outcome_by_key.get((snapshot.decision_date, selection.instrument_id))
                outcome_return = _ranking_v3_valid_outcome_return(outcome)
                if outcome_return is None:
                    invalid_outcome_count += 1
                    date_is_valid = False
                    continue
                valid_outcome_count += 1
                returns.append(outcome_return)
                if (
                    count_completed
                    and outcome is not None
                    and outcome.status == CandidateOutcomeStatus.RESOLVED
                ):
                    completed_on_date += 1
            returns.extend([0.0] * (RANKING_V3_MAX_POSITIONS - len(returns)))
            paired_returns.append((returns, completed_on_date))
        if not date_is_valid:
            excluded_rebalance_date_count += 1
            continue
        retained_rebalance_date_count += 1
        baseline_returns, _ = paired_returns[0]
        challenger_returns, challenger_completed = paired_returns[1]
        completed_challenger_trades += challenger_completed
        baseline_rows.extend(
            RankingV3ReturnObservation(
                rebalance_date=snapshot.decision_date,
                net_return_pct=value,
            )
            for value in baseline_returns
        )
        challenger_rows.extend(
            RankingV3ReturnObservation(
                rebalance_date=snapshot.decision_date,
                net_return_pct=value,
            )
            for value in challenger_returns
        )
    coverage_ratio = valid_outcome_count / selected_outcome_count if selected_outcome_count else 0.0
    paired_date_coverage_ratio = (
        retained_rebalance_date_count / considered_rebalance_date_count
        if considered_rebalance_date_count
        else 0.0
    )
    return (
        baseline_rows,
        challenger_rows,
        completed_challenger_trades,
        {
            "selected_outcome_count": selected_outcome_count,
            "valid_outcome_count": valid_outcome_count,
            "invalid_outcome_count": invalid_outcome_count,
            "excluded_rebalance_date_count": excluded_rebalance_date_count,
            "valid_outcome_coverage_ratio": round(coverage_ratio, 6),
            "considered_rebalance_date_count": considered_rebalance_date_count,
            "retained_rebalance_date_count": retained_rebalance_date_count,
            "paired_rebalance_date_coverage_ratio": round(
                paired_date_coverage_ratio,
                6,
            ),
        },
    )


def _ranking_v3_valid_outcome_return(outcome) -> float | None:
    if outcome is None or outcome.resolved_at is None:
        return None
    if (
        outcome.status == CandidateOutcomeStatus.RESOLVED
        and outcome.return_pct is not None
        and math.isfinite(float(outcome.return_pct))
    ):
        return float(outcome.return_pct)
    if (
        outcome.status == CandidateOutcomeStatus.NOT_TRIGGERED_OR_UNFILLABLE
        and outcome.status_detail in RANKING_V3_VALID_CASH_DETAILS
    ):
        return 0.0
    return None


def _ranking_v3_candidate_outcome_coverage(
    ledger: CandidateOutcomeLedgerResult,
) -> tuple[int, int, float]:
    total = len(ledger.outcomes)
    valid = sum(
        _ranking_v3_valid_outcome_return(outcome) is not None for outcome in ledger.outcomes
    )
    return total, valid, round(valid / total, 6) if total else 0.0


def _ranking_v3_stratified_outcome_coverage(
    snapshots: list[WalkForwardSnapshot],
    *,
    ledger: CandidateOutcomeLedgerResult,
    protocol: RankingV3Protocol,
) -> list[dict[str, object]]:
    selection_by_key: dict[tuple[date, str], tuple[WalkForwardSelection, str]] = {}
    for snapshot in snapshots:
        ordered = sorted(
            snapshot.candidate_pool,
            key=lambda item: (-item.rank_score, item.instrument_id),
        )
        for index, selection in enumerate(ordered, start=1):
            score_bucket = "top_10" if index <= 10 else "top_25" if index <= 25 else "rest"
            selection_by_key[(snapshot.decision_date, selection.instrument_id)] = (
                selection,
                score_bucket,
            )

    groups: dict[tuple[str, str], list[bool]] = {}
    for outcome in ledger.outcomes:
        matched = selection_by_key.get((outcome.signal_date, outcome.instrument_id))
        if matched is None:
            continue
        selection, score_bucket = matched
        window = next(
            (
                item.key
                for item in protocol.windows
                if item.start_date <= outcome.signal_date
                and (item.end_date is None or outcome.signal_date <= item.end_date)
            ),
            "outside_protocol_windows",
        )
        dimensions = (
            ("window", window),
            ("strategy", outcome.strategy_id or "unknown"),
            ("asset_type", selection.asset_type or "unknown"),
            ("industry", selection.industry or "unknown"),
            ("score_bucket", score_bucket),
        )
        is_valid = _ranking_v3_valid_outcome_return(outcome) is not None
        for dimension, value in dimensions:
            groups.setdefault((dimension, value), []).append(is_valid)

    minimum_size = protocol.thresholds.minimum_stratified_coverage_group_size
    results = []
    for (dimension, value), validity in sorted(groups.items()):
        if len(validity) < minimum_size:
            continue
        valid_count = sum(validity)
        results.append(
            {
                "dimension": dimension,
                "value": value,
                "total_count": len(validity),
                "valid_count": valid_count,
                "coverage_ratio": round(valid_count / len(validity), 6),
            }
        )
    return results


def _ranking_v3_pbo_evidence(
    snapshots: list[WalkForwardSnapshot],
    *,
    ledger: CandidateOutcomeLedgerResult,
    protocol: RankingV3Protocol,
) -> dict[str, object]:
    """Build CSCV/PBO evidence from genuine common-date candidate outcomes."""

    outcome_by_key = {
        (outcome.signal_date, outcome.instrument_id): outcome for outcome in ledger.outcomes
    }
    model_ids = tuple(item.model_id for item in protocol.statistics_definition.pbo_model_family)
    matrix: dict[str, list[RankingV3DatedModelReturn]] = {model_id: [] for model_id in model_ids}
    considered_date_count = 0
    retained_date_count = 0
    invalid_selected_outcome_count = 0

    for snapshot in snapshots:
        considered_date_count += 1
        if not snapshot.market_entry_allowed:
            retained_date_count += 1
            for model_id in model_ids:
                matrix[model_id].append(
                    RankingV3DatedModelReturn(
                        rebalance_date=snapshot.decision_date,
                        net_return=0.0,
                    )
                )
            continue
        if not snapshot.candidate_pool:
            continue
        selections_by_model = _ranking_v3_pbo_model_selections(
            snapshot,
            protocol=protocol,
        )
        returns_by_model: dict[str, float] = {}
        date_is_valid = True
        for model_id in model_ids:
            selected_returns: list[float] = []
            for selection in selections_by_model[model_id][: protocol.max_positions]:
                outcome = outcome_by_key.get((snapshot.decision_date, selection.instrument_id))
                outcome_return = _ranking_v3_valid_outcome_return(outcome)
                if outcome_return is None:
                    invalid_selected_outcome_count += 1
                    date_is_valid = False
                    continue
                selected_returns.append(outcome_return)
            selected_returns.extend([0.0] * (protocol.max_positions - len(selected_returns)))
            returns_by_model[model_id] = math.fsum(selected_returns) / protocol.max_positions
        if not date_is_valid:
            continue
        retained_date_count += 1
        for model_id in model_ids:
            matrix[model_id].append(
                RankingV3DatedModelReturn(
                    rebalance_date=snapshot.decision_date,
                    net_return=returns_by_model[model_id],
                )
            )

    evidence = evaluate_ranking_v3_cscv_pbo(
        matrix,
        block_count=protocol.statistics_definition.pbo_block_count,
        purge_rebalance_cohorts=(protocol.statistics_definition.pbo_purge_rebalance_cohorts),
    )
    coverage_ratio = retained_date_count / considered_date_count if considered_date_count else 0.0
    evidence.update(
        {
            "model_ids": list(model_ids),
            "model_return_matrix": {
                model_id: [
                    {
                        "rebalance_date": item.rebalance_date.isoformat(),
                        "net_return": item.net_return,
                    }
                    for item in matrix[model_id]
                ]
                for model_id in model_ids
            },
            "considered_date_count": considered_date_count,
            "retained_date_count": retained_date_count,
            "common_date_coverage_ratio": round(coverage_ratio, 6),
            "invalid_selected_outcome_count": invalid_selected_outcome_count,
            "matrix_return_semantics": (
                "mean_net_candidate_return_across_five_fixed_slots_valid_not_triggered_is_cash"
            ),
        }
    )
    required_coverage = protocol.statistics_definition.pbo_date_coverage_threshold
    if coverage_ratio < required_coverage:
        evidence.update(
            {
                "probability": None,
                "combination_count": 0,
                "fold_count": 0,
                "selected_model_frequencies": {},
                "relative_rank_logits": [],
                "rejection_reason": (
                    "common-date model matrix coverage "
                    f"{coverage_ratio:.2%} is below required {required_coverage:.0%}"
                ),
            }
        )
    return evidence


def _ranking_v4_valid_outcome_return(outcome) -> float | None:
    if outcome is None or outcome.resolved_at is None:
        return None
    if (
        outcome.status == CandidateOutcomeStatus.RESOLVED
        and outcome.return_pct is not None
        and math.isfinite(float(outcome.return_pct))
    ):
        return float(outcome.return_pct)
    if (
        outcome.status == CandidateOutcomeStatus.NOT_TRIGGERED_OR_UNFILLABLE
        and outcome.status_detail in RANKING_V4_VALID_CASH_DETAILS
    ):
        return 0.0
    return None


def _ranking_v4_model_return_matrix(
    snapshots: list[WalkForwardSnapshot],
    *,
    portfolios: dict[str, PortfolioBacktestResult],
) -> dict[str, list[RankingV4DatedModelReturn]]:
    expected_ids = set(RANKING_V4_FROZEN_PBO_MODEL_IDS)
    if set(portfolios) != expected_ids:
        raise ValueError("Ranking V4 model portfolios must match the frozen family")
    rebalance_dates = [snapshot.decision_date for snapshot in snapshots]
    returns_by_model = {
        model_id: _portfolio_rebalance_returns(
            portfolio,
            rebalance_dates=rebalance_dates,
        )
        for model_id, portfolio in portfolios.items()
    }
    # The frozen protocol defines a cash return for a registered model that
    # has no position on an otherwise valid rebalance date. Keep the full
    # preregistered calendar; outcome-coverage gates separately fail closed
    # when a selected candidate lacks valid evidence.
    common_dates = sorted(set(rebalance_dates))
    matrix = {model_id: [] for model_id in RANKING_V4_FROZEN_PBO_MODEL_IDS}
    for rebalance_date in common_dates:
        for model_id in RANKING_V4_FROZEN_PBO_MODEL_IDS:
            matrix[model_id].append(
                RankingV4DatedModelReturn(
                    rebalance_date=rebalance_date,
                    net_return=returns_by_model[model_id].get(rebalance_date, 0.0),
                )
            )
    return matrix


def _portfolio_rebalance_returns(
    portfolio: PortfolioBacktestResult,
    *,
    rebalance_dates: list[date],
) -> dict[date, float]:
    """Return capital-constrained period returns from one realized equity curve."""

    equity_by_date = {
        point.date: Decimal(point.equity)
        for point in sorted(portfolio.equity_curve, key=lambda item: item.date)
    }
    curve_dates = sorted(equity_by_date)
    if not curve_dates:
        return {}

    def equity_at_or_before(target: date) -> Decimal | None:
        index = bisect_right(curve_dates, target) - 1
        if index < 0:
            return None
        return equity_by_date[curve_dates[index]]

    ordered_dates = sorted(set(rebalance_dates))
    result: dict[date, float] = {}
    for index, rebalance_date in enumerate(ordered_dates):
        period_end = (
            ordered_dates[index + 1] if index + 1 < len(ordered_dates) else portfolio.summary.end
        )
        if period_end <= rebalance_date:
            continue
        starting_equity = equity_at_or_before(rebalance_date)
        ending_equity = equity_at_or_before(period_end)
        if starting_equity is None or ending_equity is None or starting_equity <= 0:
            continue
        value = float((ending_equity / starting_equity - Decimal("1")) * Decimal("100"))
        if math.isfinite(value):
            result[rebalance_date] = round(value, 8)
    return result


def _ranking_v4_model_selections(
    snapshot: WalkForwardSnapshot,
) -> dict[str, list[WalkForwardSelection]]:
    result = {
        "constraint_matched_baseline": list(snapshot.ranking_v4_constraint_matched_baseline_top_5),
        RANKING_V4_FULL_MODEL_ID: list(snapshot.ranking_v4_top_5),
    }
    source_by_instrument = {item.instrument_id: item for item in snapshot.ranking_v4_candidate_pool}
    candidates = [
        _ranking_v4_candidate_from_selection(
            item,
            decision_date=snapshot.decision_date,
            market_regime=snapshot.benchmark_trend_state,
            market_features_complete=(snapshot.ranking_v4_market_features_complete),
        )
        for item in snapshot.ranking_v4_candidate_pool
    ]
    correlations = _ranking_v4_pairwise_correlations(snapshot)
    for channel in (
        "baseline",
        "trend",
        "breakout",
        "quality_value",
        "defensive_low_vol",
        "etf_industry",
    ):
        model_id = f"channel_{channel}"
        channel_candidates = [
            candidate
            for candidate in candidates
            if channel in source_by_instrument[candidate.instrument_id].ranking_v4_channel_scores
        ]
        channel_score_by_id = {
            candidate.instrument_id: source_by_instrument[
                candidate.instrument_id
            ].ranking_v4_channel_scores[channel]
            for candidate in channel_candidates
        }
        decision = _ranking_v4_static_decision(
            channel_candidates,
            objective_score_by_instrument=channel_score_by_id,
            decision_date=snapshot.decision_date,
        )
        portfolio = select_ranking_v4_portfolio(
            decision,
            channel_candidates,
            pairwise_correlations=correlations,
        )
        result[model_id] = [source_by_instrument[item.instrument_id] for item in portfolio.selected]
    return result


def _ranking_v4_static_decision(
    candidates: list[RankingV4Candidate],
    *,
    objective_score_by_instrument: dict[str, float],
    decision_date: date,
) -> RankingV4Decision:
    objective_candidates = [
        candidate.model_copy(
            update={"baseline_rank_score": objective_score_by_instrument[candidate.instrument_id]}
        )
        for candidate in candidates
    ]
    return _ranking_v4_baseline_decision(
        objective_candidates,
        decision_date=decision_date,
    )


def _ranking_v4_selected_return_observations(
    snapshots: list[WalkForwardSnapshot],
    *,
    normal_ledger: CandidateOutcomeLedgerResult,
    stress_ledger: CandidateOutcomeLedgerResult,
    baseline_portfolio: PortfolioBacktestResult,
    challenger_portfolio: PortfolioBacktestResult,
    stress_portfolio: PortfolioBacktestResult,
    model_selections_by_date: dict[
        date,
        dict[str, list[WalkForwardSelection]],
    ]
    | None = None,
) -> tuple[
    list[RankingV4ReturnObservation],
    list[RankingV4ReturnObservation],
    int,
    int,
    int,
]:
    normal_by_key = {
        (outcome.signal_date, outcome.instrument_id): outcome for outcome in normal_ledger.outcomes
    }
    stress_by_key = {
        (outcome.signal_date, outcome.instrument_id): outcome for outcome in stress_ledger.outcomes
    }
    rebalance_dates = [snapshot.decision_date for snapshot in snapshots]
    baseline_portfolio_returns = _portfolio_rebalance_returns(
        baseline_portfolio,
        rebalance_dates=rebalance_dates,
    )
    challenger_portfolio_returns = _portfolio_rebalance_returns(
        challenger_portfolio,
        rebalance_dates=rebalance_dates,
    )
    stress_portfolio_returns = _portfolio_rebalance_returns(
        stress_portfolio,
        rebalance_dates=rebalance_dates,
    )
    baseline_rows: list[RankingV4ReturnObservation] = []
    challenger_rows: list[RankingV4ReturnObservation] = []
    completed_trade_count = len(
        {
            (
                trade.instrument_id,
                trade.signal_date,
                trade.entry_date,
                trade.exit_date,
            )
            for trade in challenger_portfolio.trades
        }
    )
    valid_outcome_count = 0
    expected_outcome_count = 0
    for snapshot in snapshots:
        model_selections = (
            model_selections_by_date.get(snapshot.decision_date)
            if model_selections_by_date is not None
            else None
        )
        if model_selections is None:
            model_selections = _ranking_v4_model_selections(snapshot)
        snapshot_expected = 0
        snapshot_valid = 0
        for selections in model_selections.values():
            _, _, model_valid = _ranking_v4_fixed_slot_return(
                selections,
                decision_date=snapshot.decision_date,
                outcome_by_key=normal_by_key,
            )
            snapshot_expected += len(selections[:5])
            snapshot_valid += model_valid
        _, _, stress_valid = _ranking_v4_fixed_slot_return(
            snapshot.ranking_v4_top_5,
            decision_date=snapshot.decision_date,
            outcome_by_key=stress_by_key,
        )
        snapshot_expected += len(snapshot.ranking_v4_top_5[:5])
        snapshot_valid += stress_valid
        expected_outcome_count += snapshot_expected
        valid_outcome_count += snapshot_valid
        baseline_rows.append(
            RankingV4ReturnObservation(
                rebalance_date=snapshot.decision_date,
                net_return_pct=baseline_portfolio_returns.get(snapshot.decision_date, 0.0),
            )
        )
        challenger_rows.append(
            RankingV4ReturnObservation(
                rebalance_date=snapshot.decision_date,
                net_return_pct=challenger_portfolio_returns.get(snapshot.decision_date, 0.0),
                stress_net_return_pct=stress_portfolio_returns.get(
                    snapshot.decision_date,
                    0.0,
                ),
            )
        )
    return (
        baseline_rows,
        challenger_rows,
        completed_trade_count,
        valid_outcome_count,
        expected_outcome_count,
    )


def _ranking_v4_fixed_slot_return(
    selections: list[WalkForwardSelection],
    *,
    decision_date: date,
    outcome_by_key: dict[tuple[date, str], object],
) -> tuple[float, int, int]:
    values = []
    completed = 0
    valid = 0
    for selection in selections[:5]:
        outcome = outcome_by_key.get((decision_date, selection.instrument_id))
        value = _ranking_v4_valid_outcome_return(outcome)
        if value is not None:
            valid += 1
        else:
            value = 0.0
        if outcome is not None and outcome.status == CandidateOutcomeStatus.RESOLVED:
            completed += 1
        values.append(value)
    values.extend([0.0] * (5 - len(values)))
    return math.fsum(values) / 5, completed, valid


def _ranking_v3_pbo_model_selections(
    snapshot: WalkForwardSnapshot,
    *,
    protocol: RankingV3Protocol,
) -> dict[str, list[WalkForwardSelection]]:
    selections: dict[str, list[WalkForwardSelection]] = {}
    source_by_instrument = {item.instrument_id: item for item in snapshot.candidate_pool}
    for model in protocol.statistics_definition.pbo_model_family:
        if model.model_id == "constraint_matched_baseline":
            selections[model.model_id] = list(snapshot.constraint_matched_baseline_top_5)
            continue
        if model.model_id == "ranking_v3_full":
            selections[model.model_id] = list(snapshot.ranking_v3_top_5)
            continue
        ordered = sorted(
            snapshot.candidate_pool,
            key=lambda item: (
                -_ranking_v3_static_model_score(
                    item,
                    stock_weights=model.stock_feature_weights,
                    etf_weights=model.etf_feature_weights,
                    protocol=protocol,
                ),
                -float(item.rank_score),
                item.instrument_id,
            ),
        )
        selected: list[WalkForwardSelection] = []
        for item in ordered:
            trial = [*selected, item]
            if not _walk_forward_selection_constraints_hold(
                trial,
                source_by_instrument=source_by_instrument,
                strategy_limit=protocol.max_per_strategy,
            ):
                continue
            selected = trial
            if len(selected) >= protocol.max_positions:
                break
        selections[model.model_id] = selected
    return selections


def _ranking_v3_static_model_score(
    selection: WalkForwardSelection,
    *,
    stock_weights: dict[str, float],
    etf_weights: dict[str, float],
    protocol: RankingV3Protocol,
) -> float:
    features = selection.ranking_features
    asset_type = selection.asset_type.lower()
    weights = (
        etf_weights if asset_type in protocol.ranking_definition.etf_asset_types else stock_weights
    )
    raw = math.fsum(float(getattr(features, name)) * weight for name, weight in weights.items())
    score = (
        raw
        - features.execution_penalty * protocol.ranking_definition.execution_penalty_weight
        - (1.0 - features.data_completeness)
        * protocol.ranking_definition.missing_data_penalty_weight
    )
    return min(
        protocol.ranking_definition.score_maximum,
        max(protocol.ranking_definition.score_minimum, score),
    )


def _ranking_v3_historical_audit_last_decision_date(
    start: date,
    end: date,
) -> date:
    audit_start = max(start, RANKING_V3_HISTORICAL_AUDIT_START)
    audit_end = min(end, RANKING_V3_HISTORICAL_AUDIT_END)
    sessions = trading_sessions_in_range(audit_start, audit_end)
    if len(sessions) <= RANKING_V3_EMBARGO_SESSIONS:
        return audit_start
    return sessions[-(RANKING_V3_EMBARGO_SESSIONS + 1)]


def _build_ranking_v3_evaluation(
    *,
    snapshots: list[WalkForwardSnapshot],
    ledger: CandidateOutcomeLedgerResult,
    constraint_matched_baseline_portfolio: PortfolioBacktestResult,
    constraint_matched_baseline_metrics: WalkForwardPortfolioMetrics,
    portfolio: PortfolioBacktestResult,
    metrics: WalkForwardPortfolioMetrics,
    stress_metrics: WalkForwardPortfolioMetrics,
    historical_validation: RankingV3ValidationEvaluation,
    audit_start: date,
    audit_end: date,
    benchmark_return_pct: float | None,
    benchmark_coverage: _EqualWeightBenchmarkResult,
    audit_last_decision_date: date,
    validation_sample_quality: dict[str, int | float],
    pbo_evidence: dict[str, object],
    forward_scoring_artifact: RankingV3FrozenScoringArtifact,
) -> WalkForwardRankingV3Evaluation:
    protocol = build_ranking_v3_protocol()
    thresholds = protocol.thresholds
    turnover_reduction = (
        round(
            (constraint_matched_baseline_metrics.turnover_pct - metrics.turnover_pct)
            / constraint_matched_baseline_metrics.turnover_pct
            * 100,
            4,
        )
        if constraint_matched_baseline_metrics.turnover_pct > 0
        else None
    )
    drawdown_degradation = round(
        max(
            0.0,
            constraint_matched_baseline_metrics.max_drawdown_pct - metrics.max_drawdown_pct,
        ),
        4,
    )
    benchmark_excess = (
        round(metrics.total_return_pct - benchmark_return_pct, 4)
        if benchmark_return_pct is not None
        else None
    )
    profit_factor = portfolio.summary.profit_factor
    statistical_status = historical_validation.statistical_gate_status
    selected_outcome_coverage_ratio = float(
        validation_sample_quality["valid_outcome_coverage_ratio"]
    )
    selected_outcome_count = int(validation_sample_quality["selected_outcome_count"])
    (
        candidate_outcome_count,
        valid_candidate_outcome_count,
        candidate_outcome_coverage_ratio,
    ) = _ranking_v3_candidate_outcome_coverage(ledger)
    paired_rebalance_date_coverage_ratio = float(
        validation_sample_quality["paired_rebalance_date_coverage_ratio"]
    )
    considered_rebalance_date_count = int(
        validation_sample_quality["considered_rebalance_date_count"]
    )
    stratified_coverage = _ranking_v3_stratified_outcome_coverage(
        snapshots,
        ledger=ledger,
        protocol=protocol,
    )
    stratified_failures = [
        item
        for item in stratified_coverage
        if float(item["coverage_ratio"]) < thresholds.minimum_stratified_outcome_coverage_ratio
    ]
    worst_stratified_coverage = min(
        (float(item["coverage_ratio"]) for item in stratified_coverage),
        default=None,
    )
    criteria = [
        _gate_criterion(
            key="valid_outcome_coverage",
            label="候选账本有效结果覆盖率",
            ready=(
                candidate_outcome_coverage_ratio >= thresholds.minimum_valid_outcome_coverage_ratio
            ),
            insufficient=candidate_outcome_count == 0,
            value=f"{candidate_outcome_coverage_ratio:.1%}",
            requirement=(
                "全部候选账本中，有效成交或明确未触发现金样本占比 >= "
                f"{thresholds.minimum_valid_outcome_coverage_ratio:.0%}"
            ),
        ),
        _gate_criterion(
            key="paired_rebalance_date_coverage",
            label="完整配对调仓日覆盖率",
            ready=(
                paired_rebalance_date_coverage_ratio
                >= thresholds.minimum_paired_rebalance_date_coverage_ratio
            ),
            insufficient=considered_rebalance_date_count == 0,
            value=f"{paired_rebalance_date_coverage_ratio:.1%}",
            requirement=(
                "基线与挑战者均有完整有效结果的调仓日占比 >= "
                f"{thresholds.minimum_paired_rebalance_date_coverage_ratio:.0%}"
            ),
        ),
        _gate_criterion(
            key="stratified_outcome_coverage",
            label="分层候选结果覆盖率",
            ready=bool(stratified_coverage) and not stratified_failures,
            insufficient=not stratified_coverage,
            value=(
                f"最差 {worst_stratified_coverage:.1%}"
                if worst_stratified_coverage is not None
                else "无达到最小样本量的分层"
            ),
            requirement=(
                "窗口、策略、资产、行业和分数层中，样本数 >= "
                f"{thresholds.minimum_stratified_coverage_group_size} 的每一层覆盖率均 >= "
                f"{thresholds.minimum_stratified_outcome_coverage_ratio:.0%}"
            ),
        ),
        _gate_criterion(
            key="historical_statistical_evidence",
            label="历史统计证据",
            ready=statistical_status == "pass",
            insufficient=statistical_status == "insufficient",
            value=(
                f"{historical_validation.common_rebalance_date_count} 个共同调仓日，"
                f"配对均值 {historical_validation.paired_mean_net_excess_pct}"
            ),
            requirement="共同日历、样本数、置信下界、Holm、DSR 和 5 段稳定性全部通过",
        ),
        _gate_criterion(
            key="positive_audit_return",
            label="历史审计净收益",
            ready=metrics.total_return_pct > 0,
            insufficient=metrics.trade_count == 0,
            value=f"{metrics.total_return_pct:.2f}%",
            requirement="历史审计期净收益 > 0%",
        ),
        _gate_criterion(
            key="positive_benchmark_excess",
            label="跑赢全市场等权基准",
            ready=benchmark_excess is not None and benchmark_excess > 0,
            insufficient=benchmark_excess is None,
            value=(f"{benchmark_excess:.2f}%" if benchmark_excess is not None else "基准缺失"),
            requirement="审计期超额收益 > 0%",
        ),
        _gate_criterion(
            key="benchmark_member_coverage",
            label="基准成分价格覆盖率",
            ready=(
                benchmark_coverage.member_coverage_ratio
                >= thresholds.minimum_benchmark_member_coverage_ratio
            ),
            insufficient=benchmark_coverage.expected_member_observations == 0,
            value=f"{benchmark_coverage.member_coverage_ratio:.1%}",
            requirement=(
                "等权可交易池基准的成分区间收益覆盖率 >= "
                f"{thresholds.minimum_benchmark_member_coverage_ratio:.0%}"
            ),
        ),
        _gate_criterion(
            key="positive_stress_return",
            label="压力成本后仍盈利",
            ready=stress_metrics.total_return_pct > 0,
            insufficient=stress_metrics.trade_count == 0,
            value=f"{stress_metrics.total_return_pct:.2f}%",
            requirement="15bps 滑点、1.5 倍手续费后收益 > 0%",
        ),
        _gate_criterion(
            key="minimum_profit_factor",
            label="盈亏效率",
            ready=(profit_factor is not None and profit_factor >= thresholds.minimum_profit_factor),
            insufficient=profit_factor is None,
            value=f"{profit_factor:.2f}" if profit_factor is not None else "暂无",
            requirement=f"Profit Factor >= {thresholds.minimum_profit_factor:.2f}",
        ),
        _gate_criterion(
            key="maximum_drawdown",
            label="最大回撤",
            ready=metrics.max_drawdown_pct >= thresholds.maximum_drawdown_pct,
            insufficient=metrics.trade_count == 0,
            value=f"{metrics.max_drawdown_pct:.2f}%",
            requirement=f"最大回撤不低于 {thresholds.maximum_drawdown_pct:.2f}%",
        ),
        _gate_criterion(
            key="drawdown_degradation",
            label="相对基线回撤恶化",
            ready=(drawdown_degradation <= thresholds.maximum_drawdown_degradation_pct),
            insufficient=False,
            value=f"{drawdown_degradation:.2f}%",
            requirement=(
                f"相对同约束基线恶化不超过 {thresholds.maximum_drawdown_degradation_pct:.2f}%"
            ),
        ),
        _gate_criterion(
            key="turnover_reduction",
            label="换手下降",
            ready=(
                turnover_reduction is not None
                and turnover_reduction >= thresholds.minimum_turnover_reduction_pct
            ),
            insufficient=turnover_reduction is None,
            value=(
                f"{turnover_reduction:.2f}%" if turnover_reduction is not None else "基线无换手"
            ),
            requirement=(f"相对同约束基线下降 >= {thresholds.minimum_turnover_reduction_pct:.0f}%"),
        ),
    ]
    criteria.extend(
        [
            WalkForwardGateCriterion(
                key="pbo",
                label="回测过拟合概率",
                status=(
                    "pass"
                    if historical_validation.pbo_status == "pass"
                    else "fail"
                    if historical_validation.pbo_status == "fail"
                    else "insufficient"
                ),
                value=(
                    f"{historical_validation.pbo_probability:.2%}"
                    if historical_validation.pbo_probability is not None
                    else "证据不足"
                ),
                requirement=(
                    "真实共同调仓日多模型收益矩阵 CSCV/PBO <= "
                    f"{thresholds.maximum_probability_of_backtest_overfit:.0%}"
                ),
            ),
            WalkForwardGateCriterion(
                key="prospective_shadow",
                label="独立前向影子验证",
                status="insufficient",
                value=f"从 {protocol.prospective_shadow_start.isoformat()} 开始",
                requirement=(
                    f"{thresholds.minimum_forward_shadow_sessions}-"
                    f"{thresholds.maximum_forward_shadow_sessions} 个交易日，"
                    f"至少 {thresholds.minimum_forward_shadow_trades} 笔完成交易"
                ),
            ),
        ]
    )
    all_statuses = {item.status for item in criteria}
    observable_statuses = {
        item.status for item in criteria if item.key not in {"pbo", "prospective_shadow"}
    }
    if "fail" in observable_statuses:
        status = "rejected"
        headline = "V3 未通过历史审计，继续保持影子隔离"
    elif "insufficient" in observable_statuses:
        status = "insufficient"
        headline = "V3 历史证据不足，不进入模拟盘"
    elif "fail" in all_statuses:
        status = "rejected"
        headline = "V3 发布门禁失败，不进入模拟盘"
    elif "insufficient" in all_statuses:
        status = "forward_validation_pending"
        headline = "V3 历史门禁通过，等待 PBO 与独立前向影子验证"
    else:
        status = "shadow_candidate"
        headline = "V3 全部门禁通过，等待权威发布证明"
    status_counts = ledger.status_counts
    invalid_count = sum(
        _ranking_v3_valid_outcome_return(outcome) is None for outcome in ledger.outcomes
    )
    valid_untriggered_count = sum(
        outcome.status == CandidateOutcomeStatus.NOT_TRIGGERED_OR_UNFILLABLE
        and outcome.status_detail in RANKING_V3_VALID_CASH_DETAILS
        for outcome in ledger.outcomes
    )
    changed_snapshots = sum(
        [item.instrument_id for item in snapshot.ranking_v3_top_5]
        != [item.instrument_id for item in snapshot.constraint_matched_baseline_top_5]
        for snapshot in snapshots
    )
    return WalkForwardRankingV3Evaluation(
        status=status,
        headline=headline,
        leakage_guard=(
            "候选结果仅在退出日严格早于决策日时可训练；历史审计期冻结在"
            f" {RANKING_V3_HISTORICAL_AUDIT_START.isoformat()} 之前的证据，"
            f"末端保留 {RANKING_V3_EMBARGO_SESSIONS} 个交易日避免未成熟结果；"
            "旧样本外仅用于审计，不能作为正式上线证明。"
        ),
        protocol=protocol,
        candidate_pool_signal_count=len(ledger.outcomes),
        resolved_candidate_count=status_counts.get(
            CandidateOutcomeStatus.RESOLVED.value,
            0,
        ),
        untriggered_candidate_count=valid_untriggered_count,
        invalid_candidate_count=invalid_count,
        valid_candidate_outcome_count=valid_candidate_outcome_count,
        candidate_outcome_coverage_ratio=round(
            candidate_outcome_coverage_ratio,
            6,
        ),
        validation_selected_outcome_count=selected_outcome_count,
        validation_valid_outcome_count=int(validation_sample_quality["valid_outcome_count"]),
        validation_invalid_outcome_count=int(validation_sample_quality["invalid_outcome_count"]),
        validation_excluded_rebalance_date_count=int(
            validation_sample_quality["excluded_rebalance_date_count"]
        ),
        validation_valid_outcome_coverage_ratio=selected_outcome_coverage_ratio,
        validation_paired_rebalance_date_coverage_ratio=(paired_rebalance_date_coverage_ratio),
        stratified_coverage_group_count=len(stratified_coverage),
        stratified_coverage_failure_count=len(stratified_failures),
        worst_stratified_outcome_coverage_ratio=worst_stratified_coverage,
        stratified_outcome_coverage=stratified_coverage,
        changed_snapshot_count=changed_snapshots,
        maximum_training_observation_count=max(
            (snapshot.ranking_v3_training_observation_count for snapshot in snapshots),
            default=0,
        ),
        maximum_training_date_count=max(
            (snapshot.ranking_v3_training_date_count for snapshot in snapshots),
            default=0,
        ),
        historical_audit_start=audit_start,
        historical_audit_end=audit_end,
        historical_audit_last_decision_date=audit_last_decision_date,
        benchmark_id=ELIGIBLE_UNIVERSE_BENCHMARK_ID,
        benchmark_status=("ready" if benchmark_return_pct is not None else "missing"),
        benchmark_return_pct=benchmark_return_pct,
        benchmark_member_coverage_ratio=benchmark_coverage.member_coverage_ratio,
        benchmark_expected_member_observations=(benchmark_coverage.expected_member_observations),
        benchmark_priced_member_observations=(benchmark_coverage.priced_member_observations),
        benchmark_excess_return_pct=benchmark_excess,
        turnover_reduction_pct=turnover_reduction,
        max_drawdown_degradation_pct=drawdown_degradation,
        constraint_matched_baseline_portfolio=(constraint_matched_baseline_portfolio),
        constraint_matched_baseline_metrics=(constraint_matched_baseline_metrics),
        portfolio=portfolio,
        metrics=metrics,
        stress_metrics=stress_metrics,
        historical_validation=historical_validation,
        pbo_evidence=pbo_evidence,
        forward_scoring_artifact=forward_scoring_artifact,
        forward_scoring_artifact_digest=forward_scoring_artifact.stable_digest,
        criteria=criteria,
    )


def _select_constraint_matched_baseline(
    candidates: list[WalkForwardSelection],
    *,
    limit: int,
    strategy_limit: int,
) -> list[WalkForwardSelection]:
    source_by_instrument = {item.instrument_id: item for item in candidates}
    selected: list[WalkForwardSelection] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (-item.rank_score, item.instrument_id),
    ):
        trial = [*selected, candidate]
        if not _walk_forward_selection_constraints_hold(
            trial,
            source_by_instrument=source_by_instrument,
            strategy_limit=strategy_limit,
        ):
            continue
        selected = trial
        if len(selected) >= limit:
            break
    return selected


def _select_ranking_v3_scores(
    decision: RankingV3Decision,
    *,
    source_by_instrument: dict[str, WalkForwardSelection],
    limit: int,
    strategy_limit: int,
) -> tuple[list[RankingV3CandidateScore], int]:
    selected: list[RankingV3CandidateScore] = []
    blocked = 0
    for score in decision.candidates:
        trial = [*selected, score]
        if not _walk_forward_selection_constraints_hold(
            trial,
            source_by_instrument=source_by_instrument,
            strategy_limit=strategy_limit,
        ):
            blocked += 1
            continue
        selected = trial
        if len(selected) >= limit:
            break
    return selected, blocked


def _walk_forward_selection_constraints_hold(
    items,
    *,
    source_by_instrument: dict[str, WalkForwardSelection],
    strategy_limit: int,
) -> bool:
    return _dynamic_selection_constraints_hold(
        items,
        source_by_instrument=source_by_instrument,
        strategy_limit=strategy_limit,
    )


def _apply_baseline_challenger(
    snapshots: list[WalkForwardSnapshot],
    *,
    top_10_portfolio: PortfolioBacktestResult,
    market_provider: ReplayMarketDataProvider,
    start: date,
    end: date,
) -> tuple[list[WalkForwardSnapshot], list[ResolvedBaselineObservation]]:
    selection_by_key = {
        (snapshot.decision_date, item.instrument_id): item
        for snapshot in snapshots
        for item in snapshot.top_10
    }
    regime_by_date = {
        snapshot.decision_date: snapshot.benchmark_trend_state for snapshot in snapshots
    }
    benchmark_series = _benchmark_price_series(
        market_provider,
        start=start,
        end=end,
    )
    observations = []
    for trade in top_10_portfolio.trades:
        selection = selection_by_key.get((trade.signal_date, trade.instrument_id))
        if selection is None:
            continue
        benchmark_return = _composite_benchmark_return(
            benchmark_series,
            start=trade.entry_date,
            end=trade.exit_date,
        )
        if benchmark_return is None:
            continue
        observations.append(
            ResolvedBaselineObservation(
                instrument_id=trade.instrument_id,
                signal_date=trade.signal_date,
                exit_date=trade.exit_date,
                return_pct=trade.return_pct,
                benchmark_return_pct=benchmark_return,
                net_excess_return_pct=round(trade.return_pct - benchmark_return, 4),
                primary_strategy_id=trade.strategy_id,
                factor_signals=selection.factor_signals,
                market_regime=regime_by_date.get(trade.signal_date, "unknown"),
                industry=selection.industry,
                asset_type=selection.asset_type,
                exit_reason=trade.exit_reason,
                holding_days=trade.holding_days,
            )
        )

    updated = []
    previous_instrument_ids: list[str] = []
    for snapshot in snapshots:
        source_by_instrument = {item.instrument_id: item for item in snapshot.top_10}
        decision = score_baseline_candidates(
            [
                BaselineCandidate(
                    instrument_id=item.instrument_id,
                    baseline_rank_score=float(item.rank_score),
                    primary_strategy_id=item.primary_strategy_id,
                    factor_signals=item.factor_signals,
                    market_regime=snapshot.benchmark_trend_state,
                    industry=item.industry,
                    asset_type=item.asset_type,
                )
                for item in snapshot.top_10
            ],
            observations,
            decision_date=snapshot.decision_date,
        )
        (
            selected_scores,
            evidence_blocked,
            hysteresis_blocked,
            constraint_blocked,
        ) = _select_baseline_challenger_scores(
            decision.candidates,
            source_by_instrument=source_by_instrument,
            previous_instrument_ids=previous_instrument_ids,
            baseline_instrument_ids=[item.instrument_id for item in snapshot.top_5],
            model_ready=decision.model_ready,
            market_entry_allowed=snapshot.market_entry_allowed,
            limit=5,
            strategy_limit=snapshot.strategy_diversification_limit,
        )
        selections = [source_by_instrument[item.instrument_id] for item in selected_scores]
        selected_ids = [item.instrument_id for item in selections]
        retained_count = len(set(selected_ids).intersection(previous_instrument_ids))
        previous_instrument_ids = selected_ids
        updated.append(
            snapshot.model_copy(
                update={
                    "baseline_challenger_top_5": selections,
                    "baseline_challenger_training_cutoff_date": (decision.training_cutoff_date),
                    "baseline_challenger_training_sample_count": (decision.training_sample_count),
                    "baseline_challenger_model_ready": decision.model_ready,
                    "baseline_challenger_cash_slots": max(0, 5 - len(selections)),
                    "baseline_challenger_retained_count": retained_count,
                    "baseline_challenger_evidence_blocked_count": evidence_blocked,
                    "baseline_challenger_hysteresis_blocked_count": (hysteresis_blocked),
                    "baseline_challenger_constraint_blocked_count": (constraint_blocked),
                }
            )
        )
    return updated, observations


def _select_baseline_challenger_scores(
    scores: list[BaselineCandidateScore],
    *,
    source_by_instrument: dict[str, WalkForwardSelection],
    previous_instrument_ids: list[str],
    baseline_instrument_ids: list[str],
    model_ready: bool,
    market_entry_allowed: bool,
    limit: int,
    strategy_limit: int,
) -> tuple[list[BaselineCandidateScore], int, int, int]:
    score_by_instrument = {item.instrument_id: item for item in scores}
    if not market_entry_allowed:
        return [], 0, 0, 0
    if not model_ready:
        return (
            [
                score_by_instrument[instrument_id]
                for instrument_id in baseline_instrument_ids
                if instrument_id in score_by_instrument
            ][:limit],
            0,
            0,
            0,
        )

    evidence_blocked = sum(not item.selection_eligible for item in scores)
    hysteresis_blocked = 0
    constraint_blocked = 0
    selected = [
        score_by_instrument[instrument_id]
        for instrument_id in previous_instrument_ids
        if instrument_id in score_by_instrument
        and _baseline_hold_eligible(score_by_instrument[instrument_id])
    ][:limit]
    selected = _drop_constraint_violations(
        selected,
        source_by_instrument=source_by_instrument,
        strategy_limit=strategy_limit,
    )

    for challenger in scores:
        if any(item.instrument_id == challenger.instrument_id for item in selected):
            continue
        if len(selected) < limit:
            if not _baseline_hold_eligible(challenger):
                continue
            trial = [*selected, challenger]
            if _baseline_constraints_hold(
                trial,
                source_by_instrument=source_by_instrument,
                strategy_limit=strategy_limit,
            ):
                selected = trial
            else:
                constraint_blocked += 1
            continue

        if not challenger.selection_eligible:
            continue
        incumbents = sorted(
            selected,
            key=lambda item: (
                item.challenger_score,
                item.expected_excess_return_pct
                if item.expected_excess_return_pct is not None
                else -999.0,
                -item.baseline_position,
                item.instrument_id,
            ),
        )
        replaced = False
        material_candidate = False
        for incumbent in incumbents:
            incumbent_excess = incumbent.expected_excess_return_pct or 0.0
            challenger_excess = challenger.expected_excess_return_pct or 0.0
            if (
                challenger.challenger_score
                < incumbent.challenger_score + BASELINE_REPLACEMENT_SCORE_MARGIN
                or challenger_excess < incumbent_excess + BASELINE_REPLACEMENT_EXCESS_MARGIN_PCT
            ):
                continue
            material_candidate = True
            trial = [
                challenger if item.instrument_id == incumbent.instrument_id else item
                for item in selected
            ]
            if not _baseline_constraints_hold(
                trial,
                source_by_instrument=source_by_instrument,
                strategy_limit=strategy_limit,
            ):
                continue
            selected = trial
            replaced = True
            break
        if not replaced:
            if material_candidate:
                constraint_blocked += 1
            else:
                hysteresis_blocked += 1

    return (
        sorted(
            selected,
            key=lambda item: (
                -item.challenger_score,
                item.baseline_position,
                item.instrument_id,
            ),
        ),
        evidence_blocked,
        hysteresis_blocked,
        constraint_blocked,
    )


def _baseline_hold_eligible(score: BaselineCandidateScore) -> bool:
    """Keep incumbents unless point-in-time evidence identifies a bad segment."""

    if score.negative_segment or score.expected_excess_return_pct is None:
        return False
    return score.expected_excess_return_pct >= -0.75 and (
        score.expected_excess_lower_bound_pct is None
        or score.expected_excess_lower_bound_pct >= -2.00
    )


def _drop_constraint_violations(
    scores: list[BaselineCandidateScore],
    *,
    source_by_instrument: dict[str, WalkForwardSelection],
    strategy_limit: int,
) -> list[BaselineCandidateScore]:
    selected: list[BaselineCandidateScore] = []
    for score in sorted(
        scores,
        key=lambda item: (-item.challenger_score, item.instrument_id),
    ):
        trial = [*selected, score]
        if _baseline_constraints_hold(
            trial,
            source_by_instrument=source_by_instrument,
            strategy_limit=strategy_limit,
        ):
            selected = trial
    return selected


def _baseline_constraints_hold(
    scores: list[BaselineCandidateScore],
    *,
    source_by_instrument: dict[str, WalkForwardSelection],
    strategy_limit: int,
) -> bool:
    return _dynamic_selection_constraints_hold(
        scores,  # type: ignore[arg-type]
        source_by_instrument=source_by_instrument,
        strategy_limit=strategy_limit,
    )


def _benchmark_price_series(
    provider: ReplayMarketDataProvider,
    *,
    start: date,
    end: date,
) -> dict[str, tuple[list[date], list[float]]]:
    bars = provider.get_daily_bars(list(RANKING_V3_CANDIDATE_BENCHMARK_IDS), start, end)
    result: dict[str, tuple[list[date], list[float]]] = {}
    if bars.empty:
        return result
    for instrument_id, frame in bars.groupby("instrument_id", sort=False):
        ordered = frame.sort_values("trade_date")
        closes = (
            ordered["adjusted_close"] if "adjusted_close" in ordered.columns else ordered["close"]
        )
        result[str(instrument_id)] = (
            ordered["trade_date"].tolist(),
            closes.astype(float).tolist(),
        )
    return result


def _composite_benchmark_return(
    series_by_instrument: dict[str, tuple[list[date], list[float]]],
    *,
    start: date,
    end: date,
) -> float | None:
    returns = []
    for benchmark_id in RANKING_V3_CANDIDATE_BENCHMARK_IDS:
        series = series_by_instrument.get(benchmark_id)
        if series is None:
            return None
        dates, closes = series
        first_index = bisect_left(dates, start)
        final_index = bisect_right(dates, end) - 1
        if first_index < 0 or final_index < first_index or final_index >= len(closes):
            continue
        first = closes[first_index]
        last = closes[final_index]
        if first > 0:
            returns.append((last / first - 1) * 100)
    if len(returns) != len(RANKING_V3_CANDIDATE_BENCHMARK_IDS):
        return None
    return round(statistics.median(returns), 4)


def _signals(
    snapshots: list[WalkForwardSnapshot],
    *,
    size: int,
    selection_source: str = "baseline",
    ranking_v4_model_selections_by_date: dict[
        date,
        dict[str, list[WalkForwardSelection]],
    ]
    | None = None,
) -> list[BacktestSignal]:
    result = []
    for snapshot in snapshots:
        if selection_source == "dynamic":
            selections = snapshot.dynamic_top_5
        elif selection_source == "baseline_challenger":
            selections = snapshot.baseline_challenger_top_5
        elif selection_source == "candidate_pool":
            selections = snapshot.candidate_pool if snapshot.market_entry_allowed else []
        elif selection_source == "constraint_matched_baseline":
            selections = snapshot.constraint_matched_baseline_top_5
        elif selection_source == "ranking_v3":
            selections = snapshot.ranking_v3_top_5
        elif selection_source == "ranking_v4_candidate_pool":
            selections = snapshot.ranking_v4_candidate_pool
        elif selection_source == "ranking_v4_constraint_matched_baseline":
            selections = snapshot.ranking_v4_constraint_matched_baseline_top_5
        elif selection_source == "ranking_v4":
            selections = snapshot.ranking_v4_top_5
        elif selection_source.startswith("ranking_v4_channel_"):
            model_id = selection_source.removeprefix("ranking_v4_")
            cached = (
                ranking_v4_model_selections_by_date.get(snapshot.decision_date)
                if ranking_v4_model_selections_by_date is not None
                else None
            )
            selections = (
                cached if cached is not None else _ranking_v4_model_selections(snapshot)
            ).get(model_id, [])
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


def _build_ranking_v4_evaluation(
    *,
    snapshots: list[WalkForwardSnapshot],
    ledger: CandidateOutcomeLedgerResult,
    constraint_matched_baseline_portfolio: PortfolioBacktestResult,
    constraint_matched_baseline_metrics: WalkForwardPortfolioMetrics,
    portfolio: PortfolioBacktestResult,
    metrics: WalkForwardPortfolioMetrics,
    stress_metrics: WalkForwardPortfolioMetrics,
    historical_validation: RankingV4HistoricalValidationEvaluation,
    pbo_evidence: dict[str, object],
    trial_ledger: RankingV4TrialLedgerEvidence,
) -> WalkForwardRankingV4Evaluation:
    protocol = build_ranking_v4_protocol()
    total_outcomes = len(ledger.outcomes)
    valid_outcomes = sum(
        _ranking_v4_valid_outcome_return(outcome) is not None for outcome in ledger.outcomes
    )
    if historical_validation.status == "pass":
        status = "forward_validation_pending"
        headline = f"{protocol.model_version} 历史开发门禁通过，等待冻结后的独立前向验证"
    elif historical_validation.status == "fail":
        status = "rejected"
        headline = f"{protocol.model_version} 历史门禁失败，继续保持影子隔离"
    else:
        status = "insufficient"
        headline = f"{protocol.model_version} 历史证据不足，不进入正式模拟盘"
    criteria = [
        WalkForwardGateCriterion(
            key=gate.key,
            label=gate.key,
            status=gate.status,
            value=gate.observed,
            requirement=gate.required,
        )
        for gate in historical_validation.gates
    ]
    evidence_coverage = _ranking_v4_evidence_coverage(snapshots)
    blocked_reason_counts: dict[str, int] = {}
    first_blocked_reason_counts: dict[str, int] = {}
    gate_reconciliation: dict[str, int] = {}
    additive_coverage_keys = (
        "temporal_complete_count",
        "constraint_complete_count",
        "point_in_time_metadata_count",
        "return_risk_proxy_count",
        "raw_metadata_count",
        "return_risk_count",
        "combined_constraint_count",
        "industry_evidence_count",
        "underlying_evidence_count",
        "beta_evidence_count",
        "pairwise_correlation_count",
        "feature_adjusted_candidate_count",
        "ranking_eligible_count",
        "selected_count",
    )
    for snapshot in snapshots:
        for key in additive_coverage_keys:
            evidence_coverage[key] = evidence_coverage.get(key, 0.0) + float(
                snapshot.ranking_v4_evidence_coverage.get(key, 0.0)
            )
        for key, value in snapshot.ranking_v4_blocked_reason_counts.items():
            blocked_reason_counts[key] = blocked_reason_counts.get(key, 0) + value
        for key, value in snapshot.ranking_v4_first_blocked_reason_counts.items():
            first_blocked_reason_counts[key] = first_blocked_reason_counts.get(key, 0) + value
        for key, value in snapshot.ranking_v4_gate_reconciliation.items():
            gate_reconciliation[key] = gate_reconciliation.get(key, 0) + value
    candidate_count = evidence_coverage.get("candidate_count", 0.0)
    for source_key, ratio_key in (
        ("temporal_complete_count", "temporal_complete_ratio"),
        ("constraint_complete_count", "constraint_complete_ratio"),
        ("point_in_time_metadata_count", "point_in_time_metadata_ratio"),
        ("return_risk_proxy_count", "return_risk_proxy_ratio"),
        ("raw_metadata_count", "raw_metadata_ratio"),
        ("return_risk_count", "return_risk_ratio"),
        ("combined_constraint_count", "combined_constraint_ratio"),
        ("industry_evidence_count", "industry_evidence_ratio"),
        ("underlying_evidence_count", "underlying_evidence_ratio"),
        ("beta_evidence_count", "beta_evidence_ratio"),
        ("feature_adjusted_candidate_count", "feature_adjusted_candidate_ratio"),
        ("ranking_eligible_count", "ranking_eligible_ratio"),
        ("selected_count", "selected_ratio"),
    ):
        evidence_coverage[ratio_key] = (
            round(evidence_coverage.get(source_key, 0.0) / candidate_count, 8)
            if candidate_count
            else 0.0
        )
    return WalkForwardRankingV4Evaluation(
        model_version=protocol.model_version,
        status=status,
        headline=headline,
        leakage_guard=(
            "特征、市场状态、约束和成本证据均不得晚于决策日；"
            "训练结果必须在训练截止日前成熟；候选收益使用复权历史和真实"
            "A股执行约束，缺失证据一律持有现金。历史开发结果不能直接发布。"
        ),
        protocol=protocol,
        candidate_pool_signal_count=total_outcomes,
        valid_candidate_outcome_count=valid_outcomes,
        candidate_outcome_coverage_ratio=(
            round(valid_outcomes / total_outcomes, 6) if total_outcomes else 0.0
        ),
        changed_snapshot_count=sum(
            [item.instrument_id for item in snapshot.ranking_v4_top_5]
            != [
                item.instrument_id for item in snapshot.ranking_v4_constraint_matched_baseline_top_5
            ]
            for snapshot in snapshots
        ),
        maximum_training_observation_count=max(
            (snapshot.ranking_v4_training_observation_count for snapshot in snapshots),
            default=0,
        ),
        maximum_training_date_count=max(
            (snapshot.ranking_v4_training_date_count for snapshot in snapshots),
            default=0,
        ),
        constraint_matched_baseline_portfolio=(constraint_matched_baseline_portfolio),
        constraint_matched_baseline_metrics=(constraint_matched_baseline_metrics),
        portfolio=portfolio,
        metrics=metrics,
        stress_metrics=stress_metrics,
        historical_validation=historical_validation,
        pbo_evidence=pbo_evidence,
        trial_ledger=trial_ledger,
        evidence_coverage=evidence_coverage,
        blocked_reason_counts=blocked_reason_counts,
        first_blocked_reason_counts=first_blocked_reason_counts,
        gate_reconciliation=gate_reconciliation,
        criteria=criteria,
    )


def _selection_digest(
    *,
    provider_mode: str,
    revision: int,
    start: date,
    end: date,
    rebalance_step_sessions: int,
    lookback_days: int,
    snapshots: list[WalkForwardSnapshot],
    top_5_portfolio: PortfolioBacktestResult,
    top_10_portfolio: PortfolioBacktestResult,
    top_5_metrics: WalkForwardPortfolioMetrics,
    top_10_metrics: WalkForwardPortfolioMetrics,
    top_5_temporal_validation: TemporalValidationResult,
    top_10_temporal_validation: TemporalValidationResult,
    benchmarks: list[WalkForwardBenchmarkComparison],
    cost_sensitivity: list[WalkForwardCostScenario],
    strategy_validation: WalkForwardValidationCenter,
    dynamic_rerank: WalkForwardRerankEvaluation,
    baseline_challenger: WalkForwardBaselineChallengerEvaluation,
    execution_challenger: WalkForwardExecutionChallengerEvaluation,
    ranking_v3: WalkForwardRankingV3Evaluation | None,
    ranking_v4: WalkForwardRankingV4Evaluation | None,
    experiment_manifest: WalkForwardExperimentManifest,
) -> str:
    payload = {
        "digest_schema": WALK_FORWARD_RESULT_DIGEST_SCHEMA,
        "execution_plan": {
            "provider_mode": provider_mode,
            "dataset_revision": revision,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "rebalance_step_sessions": rebalance_step_sessions,
            "lookback_days": lookback_days,
            "experiment_manifest": experiment_manifest.model_dump(
                mode="json",
                exclude={"created_at"},
            ),
        },
        "dataset_revision": revision,
        "snapshots": [item.model_dump(mode="json") for item in snapshots],
        "top_5_portfolio": top_5_portfolio.model_dump(mode="json"),
        "top_10_portfolio": top_10_portfolio.model_dump(mode="json"),
        "top_5_metrics": top_5_metrics.model_dump(mode="json"),
        "top_10_metrics": top_10_metrics.model_dump(mode="json"),
        "top_5_temporal_validation": top_5_temporal_validation.model_dump(mode="json"),
        "top_10_temporal_validation": top_10_temporal_validation.model_dump(mode="json"),
        "benchmarks": [item.model_dump(mode="json") for item in benchmarks],
        "cost_sensitivity": [item.model_dump(mode="json") for item in cost_sensitivity],
        "strategy_validation": strategy_validation.model_dump(mode="json"),
        "dynamic_rerank": dynamic_rerank.model_dump(mode="json"),
        "baseline_challenger": baseline_challenger.model_dump(mode="json"),
        "execution_challenger": execution_challenger.model_dump(mode="json"),
        "ranking_v3": ranking_v3.model_dump(mode="json"),
        "ranking_v4": ranking_v4.model_dump(mode="json"),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"{WALK_FORWARD_RESULT_DIGEST_PREFIX}{digest[2:]}"


def walk_forward_selection_result_digest_is_valid(
    payload: dict[str, object],
) -> bool:
    if payload.get("result_digest_schema") != WALK_FORWARD_RESULT_DIGEST_SCHEMA:
        return False
    try:
        result = WalkForwardSelectionResult.model_validate(payload)
    except (TypeError, ValueError):
        return False
    serialized_expected = _selection_digest_from_serialized_result(payload)
    if serialized_expected is not None and hmac.compare_digest(
        result.reproducibility_digest,
        serialized_expected,
    ):
        return True
    expected = _selection_digest(
        provider_mode=result.provider_mode,
        revision=result.dataset_revision,
        start=result.start_date,
        end=result.end_date,
        rebalance_step_sessions=result.rebalance_step_sessions,
        lookback_days=result.experiment_manifest.lookback_days,
        snapshots=result.snapshots,
        top_5_portfolio=result.top_5_portfolio,
        top_10_portfolio=result.top_10_portfolio,
        top_5_metrics=result.top_5_metrics,
        top_10_metrics=result.top_10_metrics,
        top_5_temporal_validation=result.top_5_temporal_validation,
        top_10_temporal_validation=result.top_10_temporal_validation,
        benchmarks=result.benchmarks,
        cost_sensitivity=result.cost_sensitivity,
        strategy_validation=result.strategy_validation,
        dynamic_rerank=result.dynamic_rerank,
        baseline_challenger=result.baseline_challenger,
        execution_challenger=result.execution_challenger,
        ranking_v3=result.ranking_v3,
        ranking_v4=result.ranking_v4,
        experiment_manifest=result.experiment_manifest,
    )
    return hmac.compare_digest(result.reproducibility_digest, expected)


def _selection_digest_from_serialized_result(
    payload: dict[str, object],
) -> str | None:
    """Verify old results without injecting defaults added by newer schemas."""

    manifest = payload.get("experiment_manifest")
    if not isinstance(manifest, dict):
        return None
    required_fields = (
        "provider_mode",
        "dataset_revision",
        "start_date",
        "end_date",
        "rebalance_step_sessions",
        "snapshots",
        "top_5_portfolio",
        "top_10_portfolio",
        "top_5_metrics",
        "top_10_metrics",
        "top_5_temporal_validation",
        "top_10_temporal_validation",
        "benchmarks",
        "cost_sensitivity",
        "strategy_validation",
        "dynamic_rerank",
        "baseline_challenger",
        "execution_challenger",
        "ranking_v3",
        "ranking_v4",
    )
    if "lookback_days" not in manifest or any(field not in payload for field in required_fields):
        return None
    signed_manifest = {key: value for key, value in manifest.items() if key != "created_at"}
    signed_payload = {
        "digest_schema": WALK_FORWARD_RESULT_DIGEST_SCHEMA,
        "execution_plan": {
            "provider_mode": payload["provider_mode"],
            "dataset_revision": payload["dataset_revision"],
            "start_date": payload["start_date"],
            "end_date": payload["end_date"],
            "rebalance_step_sessions": payload["rebalance_step_sessions"],
            "lookback_days": manifest["lookback_days"],
            "experiment_manifest": signed_manifest,
        },
        "dataset_revision": payload["dataset_revision"],
        "snapshots": payload["snapshots"],
        "top_5_portfolio": payload["top_5_portfolio"],
        "top_10_portfolio": payload["top_10_portfolio"],
        "top_5_metrics": payload["top_5_metrics"],
        "top_10_metrics": payload["top_10_metrics"],
        "top_5_temporal_validation": payload["top_5_temporal_validation"],
        "top_10_temporal_validation": payload["top_10_temporal_validation"],
        "benchmarks": payload["benchmarks"],
        "cost_sensitivity": payload["cost_sensitivity"],
        "strategy_validation": payload["strategy_validation"],
        "dynamic_rerank": payload["dynamic_rerank"],
        "baseline_challenger": payload["baseline_challenger"],
        "execution_challenger": payload["execution_challenger"],
        "ranking_v3": payload["ranking_v3"],
        "ranking_v4": payload["ranking_v4"],
    }
    try:
        encoded = json.dumps(
            signed_payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    digest = hashlib.sha256(encoded).hexdigest()
    return f"{WALK_FORWARD_RESULT_DIGEST_PREFIX}{digest[2:]}"


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
    baseline_challenger_signals: list[BacktestSignal],
    top_5_portfolio: PortfolioBacktestResult,
    top_10_portfolio: PortfolioBacktestResult,
    dynamic_top_5_portfolio: PortfolioBacktestResult,
    baseline_challenger_portfolio: PortfolioBacktestResult,
    execution_challenger_portfolio: PortfolioBacktestResult,
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
            baseline_challenger = baseline_challenger_portfolio
            execution_challenger = execution_challenger_portfolio
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
                instrument_ids=sorted({item.instrument_id for item in dynamic_top_5_signals}),
                provider=market_provider,
                start=start,
                end=end,
                max_positions=5,
                slippage_bps=slippage_bps,
                fee_multiplier=fee_multiplier,
                execution_rule_resolver=execution_resolver,
            )
            baseline_challenger = run_signal_portfolio_backtest(
                signals=baseline_challenger_signals,
                instrument_ids=sorted({item.instrument_id for item in baseline_challenger_signals}),
                provider=market_provider,
                start=start,
                end=end,
                max_positions=5,
                slippage_bps=slippage_bps,
                fee_multiplier=fee_multiplier,
                execution_rule_resolver=execution_resolver,
            )
            execution_challenger = run_signal_portfolio_backtest(
                signals=top_5_signals,
                instrument_ids=sorted({item.instrument_id for item in top_5_signals}),
                provider=market_provider,
                start=start,
                end=end,
                max_positions=5,
                slippage_bps=slippage_bps,
                fee_multiplier=fee_multiplier,
                execution_rule_resolver=execution_resolver,
                execution_profile=ADAPTIVE_CONFIRMATION_EXECUTION_PROFILE,
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
                dynamic_top_5_max_drawdown_pct=(dynamic_top_5.summary.max_drawdown_pct),
                dynamic_top_5_total_costs=sum(
                    (item.costs for item in dynamic_top_5.trades),
                    Decimal("0"),
                ),
                baseline_challenger_return_pct=(baseline_challenger.summary.total_return_pct),
                baseline_challenger_max_drawdown_pct=(baseline_challenger.summary.max_drawdown_pct),
                baseline_challenger_total_costs=sum(
                    (item.costs for item in baseline_challenger.trades),
                    Decimal("0"),
                ),
                execution_challenger_return_pct=(execution_challenger.summary.total_return_pct),
                execution_challenger_max_drawdown_pct=(
                    execution_challenger.summary.max_drawdown_pct
                ),
                execution_challenger_total_costs=sum(
                    (item.costs for item in execution_challenger.trades),
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
    baseline_challenger_return: float,
    execution_challenger_return: float,
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
                    baseline_challenger_excess_return_pct=None,
                    execution_challenger_excess_return_pct=None,
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
                baseline_challenger_excess_return_pct=(
                    round(baseline_challenger_return - benchmark_return, 4)
                    if benchmark_return is not None
                    else None
                ),
                execution_challenger_excess_return_pct=(
                    round(execution_challenger_return - benchmark_return, 4)
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
            baseline_challenger_excess_return_pct=(
                round(baseline_challenger_return - equal_weight_return, 4)
                if equal_weight_return is not None
                else None
            ),
            execution_challenger_excess_return_pct=(
                round(execution_challenger_return - equal_weight_return, 4)
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
    return _equal_weight_eligible_return_with_coverage(
        provider,
        eligible_universes,
        end=end,
    ).return_pct


def _equal_weight_eligible_return_with_coverage(
    provider: ReplayMarketDataProvider,
    eligible_universes: list[tuple[date, list[str]]],
    *,
    end: date,
) -> _EqualWeightBenchmarkResult:
    instrument_ids = sorted(
        {instrument_id for _, members in eligible_universes for instrument_id in members}
    )
    if not instrument_ids:
        return _EqualWeightBenchmarkResult(
            return_pct=None,
            expected_member_observations=0,
            priced_member_observations=0,
            member_coverage_ratio=0.0,
        )
    first_date = eligible_universes[0][0]
    stream_loader = getattr(provider, "iter_adjusted_closes", None)
    if callable(stream_loader):
        return _equal_weight_eligible_return_from_stream_with_coverage(
            stream_loader(instrument_ids, first_date, end),
            eligible_universes,
            end=end,
        )
    bars = provider.get_daily_bars(instrument_ids, first_date, end)
    if bars.empty:
        expected = sum(
            len(set(members))
            for decision_date, members in eligible_universes
            if decision_date < end
        )
        return _EqualWeightBenchmarkResult(
            return_pct=None,
            expected_member_observations=expected,
            priced_member_observations=0,
            member_coverage_ratio=0.0,
        )
    price_series = {}
    for instrument_id, frame in bars.groupby("instrument_id", sort=False):
        ordered = frame.sort_values("trade_date")
        price_series[instrument_id] = (
            ordered["trade_date"].tolist(),
            ordered["adjusted_close"].astype(float).tolist(),
        )
    compounded = 1.0
    completed_periods = 0
    expected_member_observations = 0
    priced_member_observations = 0
    for index, (decision_date, members) in enumerate(eligible_universes):
        period_end = (
            eligible_universes[index + 1][0] if index + 1 < len(eligible_universes) else end
        )
        if period_end <= decision_date:
            continue
        expected_member_observations += len(set(members))
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
        priced_member_observations += len(returns)
        compounded *= 1 + statistics.mean(returns)
        completed_periods += 1
    return _EqualWeightBenchmarkResult(
        return_pct=round((compounded - 1) * 100, 4) if completed_periods else None,
        expected_member_observations=expected_member_observations,
        priced_member_observations=priced_member_observations,
        member_coverage_ratio=(
            round(priced_member_observations / expected_member_observations, 6)
            if expected_member_observations
            else 0.0
        ),
    )


def _equal_weight_eligible_return_from_stream(
    rows,
    eligible_universes: list[tuple[date, list[str]]],
    *,
    end: date,
) -> float | None:
    return _equal_weight_eligible_return_from_stream_with_coverage(
        rows,
        eligible_universes,
        end=end,
    ).return_pct


def _equal_weight_eligible_return_from_stream_with_coverage(
    rows,
    eligible_universes: list[tuple[date, list[str]]],
    *,
    end: date,
) -> _EqualWeightBenchmarkResult:
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
    expected_member_observations = sum(
        len(member_set)
        for decision_date, member_set in zip(
            decision_dates,
            member_sets,
            strict=True,
        )
        if decision_date < end
    )
    priced_member_observations = sum(return_counts)
    return _EqualWeightBenchmarkResult(
        return_pct=round((compounded - 1) * 100, 4) if completed_periods else None,
        expected_member_observations=expected_member_observations,
        priced_member_observations=priced_member_observations,
        member_coverage_ratio=(
            round(priced_member_observations / expected_member_observations, 6)
            if expected_member_observations
            else 0.0
        ),
    )
