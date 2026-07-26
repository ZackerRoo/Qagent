from __future__ import annotations

import hashlib
import json
import math
from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict, Field

from qagent.backtesting.ranking_v3_experiment_registry import (
    RankingV3ExperimentRegistry,
    build_ranking_v3_experiment_registry,
)
from qagent.backtesting.ranking_v3_pbo import (
    CSCV_PBO_METHOD,
    PBO_SCOPE_FROZEN_SIX_MODEL_FAMILY,
    RANKING_V3_FROZEN_PBO_MODEL_IDS,
)
from qagent.market.calendars import trading_day_offset, trading_sessions_in_range


RANKING_V3_PROTOCOL_SCHEMA_VERSION = "ranking-v3-protocol-v4"
RANKING_V3_PROTOCOL_ID = "QAGENT-RANK-V3.2-20260726"
RANKING_V3_MODEL_VERSION = "point-in-time-net-excess-v3.2-frozen-benchmarks"
RANKING_V3_CANDIDATE_LEDGER_IMPLEMENTATION_VERSION = (
    "independent-candidate-outcome-ledger-v3-all-candidate-coverage-invalid-trigger-excluded"
)
RANKING_V3_STATISTICS_IMPLEMENTATION_VERSION = (
    "rebalance-date-block-dependent-validation-v8-full-label-span-matrix-dsr-pbo"
)
RANKING_V3_PURGE_EMBARGO_IMPLEMENTATION_VERSION = "trading-session-window-boundary-purge-embargo-v1"
RANKING_V3_NOT_TRIGGERED_SEMANTICS_VERSION = (
    "cash-not-triggered-v2-invalid-censored-excluded-benchmark-opportunity-cost"
)
RANKING_V3_CANDIDATE_POOL_LIMIT = 50
RANKING_V3_MAX_POSITIONS = 5
RANKING_V3_MAX_PER_STRATEGY = 2
RANKING_V3_MAX_PER_INDUSTRY = 2
RANKING_V3_MAX_ETF_INDEX_OVERLAP = 1
RANKING_V3_EMBARGO_SESSIONS = 25
RANKING_V3_ENTRY_WAIT_SESSIONS = 5
RANKING_V3_HOLDING_SESSIONS = 20
RANKING_V3_REBALANCE_STEP_SESSIONS = 10
RANKING_V3_CANDIDATE_BENCHMARK_IDS = (
    "CN:000300.IDX",
    "CN:000905.IDX",
    "CN:399006.IDX",
    "CN:000688.IDX",
)
RANKING_V3_HISTORICAL_PORTFOLIO_BENCHMARK_ID = "CN:EQUAL_WEIGHT_ELIGIBLE"
RANKING_V3_FORWARD_BENCHMARK_ID = "CN:000300.IDX"
RANKING_V3_TRAINING_START = date(2021, 11, 1)
RANKING_V3_TRAINING_END = date(2023, 6, 30)
RANKING_V3_VALIDATION_START = date(2023, 8, 7)
RANKING_V3_VALIDATION_END = date(2024, 6, 28)
RANKING_V3_HISTORICAL_AUDIT_START = date(2024, 8, 5)
RANKING_V3_HISTORICAL_AUDIT_END = date(2025, 12, 31)
RANKING_V3_PROSPECTIVE_SHADOW_START = date(2026, 7, 27)


class RankingV3ProtocolError(RuntimeError):
    """Raised when a frozen protocol is internally inconsistent."""


class RankingV3Window(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    start_date: date
    end_date: date | None = None
    role: str


class RankingV3GateThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    minimum_rebalance_dates: int = 24
    minimum_completed_trades: int = 60
    minimum_profit_factor: float = 1.10
    minimum_positive_subperiods: int = 4
    required_subperiods: int = 5
    maximum_drawdown_pct: float = -15.0
    maximum_drawdown_degradation_pct: float = 2.0
    minimum_turnover_reduction_pct: float = 25.0
    maximum_holm_adjusted_p_value: float = 0.05
    minimum_deflated_sharpe_probability: float = 0.95
    maximum_probability_of_backtest_overfit: float = 0.20
    minimum_valid_outcome_coverage_ratio: float = 0.95
    minimum_paired_rebalance_date_coverage_ratio: float = 0.95
    minimum_stratified_coverage_group_size: int = 20
    minimum_stratified_outcome_coverage_ratio: float = 0.95
    minimum_benchmark_member_coverage_ratio: float = 0.95
    maximum_invalid_outcome_ratio: float = 0.05
    minimum_forward_shadow_sessions: int = 20
    minimum_forward_shadow_trades: int = 10
    maximum_forward_shadow_sessions: int = 50


class RankingV3SortingDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    implementation_version: str = "ranking-v3-score-definition-v1"
    minimum_training_observations: int = 120
    minimum_training_dates: int = 24
    minimum_candidate_data_completeness: float = 0.68
    evidence_availability_operator: str = "available_at_strictly_before_cutoff"
    resolved_outcome_status: str = "resolved"
    trigger_outcome_statuses: tuple[str, ...] = (
        "resolved",
        "not_triggered",
    )
    feature_defaults: dict[str, float] = Field(
        default_factory=lambda: {
            "strategy_score": 0.5,
            "factor_score": 0.5,
            "valuation": 0.5,
            "size": 0.5,
            "quality": 0.5,
            "momentum": 0.5,
            "trend_quality": 0.5,
            "liquidity": 0.5,
            "low_risk": 0.5,
            "risk_filter": 0.5,
            "reversal": 0.5,
            "execution_penalty": 0.0,
            "data_completeness": 0.0,
        }
    )
    etf_asset_types: tuple[str, ...] = ("etf", "fund", "index_fund")
    etf_feature_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "trend_quality": 0.40,
            "momentum": 0.35,
            "low_risk": 0.15,
            "liquidity": 0.10,
        }
    )
    stock_feature_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "trend_quality": 0.22,
            "momentum": 0.20,
            "quality": 0.15,
            "valuation": 0.10,
            "low_risk": 0.15,
            "liquidity": 0.10,
            "risk_filter": 0.08,
        }
    )
    execution_penalty_weight: float = 0.15
    missing_data_penalty_weight: float = 0.10
    factor_segment_names: tuple[str, ...] = (
        "valuation",
        "quality",
        "momentum",
        "trend_quality",
        "liquidity",
        "low_risk",
        "risk_filter",
        "reversal",
    )
    factor_high_threshold: float = 0.67
    factor_low_threshold: float = 0.33
    evidence_segment_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "strategy": 0.50,
            "asset": 0.20,
            "factor": 0.30,
        }
    )
    prior_date_strength: float = 12.0
    recency_half_life_days: float = 365.0
    posterior_win_success_prior: float = 2.0
    posterior_win_total_prior: float = 4.0
    lower_confidence_z_score: float = 1.644854
    maximum_calibration_delta: float = 0.05
    calibration_alpha_scale_pct: float = 4.0
    calibration_alpha_weight: float = 0.70
    calibration_win_weight: float = 0.30
    calibration_win_center: float = 0.50
    calibration_win_scale: float = 2.0
    incumbent_turnover_bonus: float = 0.025
    trigger_probability_success_prior: float = 2.0
    trigger_probability_total_prior: float = 4.0
    trigger_penalty_probability_threshold: float = 0.45
    trigger_penalty_scale: float = 0.05
    maximum_trigger_penalty: float = 0.02
    score_minimum: float = 0.0
    score_maximum: float = 1.0
    initial_tie_break: tuple[str, ...] = (
        "baseline_rank_score_desc",
        "instrument_id_asc",
    )
    final_tie_break: tuple[str, ...] = (
        "v3_score_desc",
        "baseline_position_asc",
        "instrument_id_asc",
    )


class RankingV3CostScenario(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    slippage_bps: str
    fee_multiplier: str


class RankingV3CostDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    implementation_version: str = "ranking-v3-cost-definition-v1"
    audit_stress: RankingV3CostScenario = Field(
        default_factory=lambda: RankingV3CostScenario(
            key="ranking_v3_audit_stress",
            slippage_bps="15",
            fee_multiplier="1.5",
        )
    )
    sensitivity_scenarios: tuple[RankingV3CostScenario, ...] = Field(
        default_factory=lambda: (
            RankingV3CostScenario(
                key="base",
                slippage_bps="5",
                fee_multiplier="1",
            ),
            RankingV3CostScenario(
                key="elevated",
                slippage_bps="10",
                fee_multiplier="1.5",
            ),
            RankingV3CostScenario(
                key="stress",
                slippage_bps="20",
                fee_multiplier="2",
            ),
        )
    )


class RankingV3PBOModelDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    model_id: str
    selection_rule: str
    stock_feature_weights: dict[str, float] = Field(default_factory=dict)
    etf_feature_weights: dict[str, float] = Field(default_factory=dict)


def _ranking_v3_pbo_model_family() -> tuple[RankingV3PBOModelDefinition, ...]:
    return (
        RankingV3PBOModelDefinition(
            model_id="constraint_matched_baseline",
            selection_rule="baseline_rank_score_with_frozen_portfolio_constraints",
        ),
        RankingV3PBOModelDefinition(
            model_id="ranking_v3_full",
            selection_rule="point_in_time_v3_score_with_frozen_portfolio_constraints",
        ),
        RankingV3PBOModelDefinition(
            model_id="static_balanced",
            selection_rule="static_feature_score_without_dynamic_calibration_or_incumbency",
            stock_feature_weights={
                "trend_quality": 0.22,
                "momentum": 0.20,
                "quality": 0.15,
                "valuation": 0.10,
                "low_risk": 0.15,
                "liquidity": 0.10,
                "risk_filter": 0.08,
            },
            etf_feature_weights={
                "trend_quality": 0.40,
                "momentum": 0.35,
                "low_risk": 0.15,
                "liquidity": 0.10,
            },
        ),
        RankingV3PBOModelDefinition(
            model_id="trend_momentum",
            selection_rule="frozen_trend_momentum_ablation",
            stock_feature_weights={
                "trend_quality": 0.35,
                "momentum": 0.35,
                "low_risk": 0.10,
                "liquidity": 0.10,
                "quality": 0.05,
                "risk_filter": 0.05,
            },
            etf_feature_weights={
                "trend_quality": 0.45,
                "momentum": 0.40,
                "low_risk": 0.10,
                "liquidity": 0.05,
            },
        ),
        RankingV3PBOModelDefinition(
            model_id="quality_value",
            selection_rule="frozen_quality_value_ablation",
            stock_feature_weights={
                "quality": 0.35,
                "valuation": 0.30,
                "low_risk": 0.15,
                "liquidity": 0.10,
                "trend_quality": 0.10,
            },
            etf_feature_weights={
                "trend_quality": 0.35,
                "momentum": 0.25,
                "low_risk": 0.20,
                "liquidity": 0.20,
            },
        ),
        RankingV3PBOModelDefinition(
            model_id="defensive_liquidity",
            selection_rule="frozen_defensive_liquidity_ablation",
            stock_feature_weights={
                "low_risk": 0.35,
                "liquidity": 0.25,
                "risk_filter": 0.20,
                "quality": 0.10,
                "trend_quality": 0.10,
            },
            etf_feature_weights={
                "low_risk": 0.35,
                "liquidity": 0.30,
                "trend_quality": 0.20,
                "momentum": 0.15,
            },
        ),
    )


class RankingV3StatisticalDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    implementation_version: str = RANKING_V3_STATISTICS_IMPLEMENTATION_VERSION
    entry_wait_sessions: int = RANKING_V3_ENTRY_WAIT_SESSIONS
    holding_sessions: int = RANKING_V3_HOLDING_SESSIONS
    rebalance_step_sessions: int = RANKING_V3_REBALANCE_STEP_SESSIONS
    dependence_block_length: int = 3
    bootstrap_samples: int = 5000
    permutation_samples: int = 10000
    random_seed: int = 42
    one_sided_lower_quantile: float = 0.05
    bootstrap_combination_rule: str = "minimum_of_moving_block_and_iid"
    sign_flip_combination_rule: str = "maximum_of_block_and_iid"
    permutation_plus_one_correction: bool = True
    deflated_sharpe_evidence_policy: str = (
        "frozen_common_date_model_matrix_with_full_registered_trial_penalty"
    )
    deflated_sharpe_trial_distribution_source: str = (
        "frozen_six_model_paired_excess_sharpes_and_registered_attempt_count"
    )
    holm_family_source: str = (
        "registered_prior_attempts_plus_current_missing_p_values_fail_closed_upper_bound"
    )
    pbo_policy: str = (
        "real_candidate_outcomes_common_rebalance_calendar_six_model_family_fail_closed"
    )
    pbo_method: str = CSCV_PBO_METHOD
    pbo_scope: str = PBO_SCOPE_FROZEN_SIX_MODEL_FAMILY
    pbo_is_full_search_process_estimate: bool = False
    pbo_block_count: int = 6
    pbo_purge_rebalance_cohorts: int = 2
    pbo_date_coverage_threshold: float = 0.95
    pbo_model_family: tuple[RankingV3PBOModelDefinition, ...] = Field(
        default_factory=_ranking_v3_pbo_model_family
    )


class RankingV3TemporalIsolationDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    implementation_version: str = RANKING_V3_PURGE_EMBARGO_IMPLEMENTATION_VERSION
    session_unit: str = "a_share_trading_session"
    purge_sessions: int = RANKING_V3_EMBARGO_SESSIONS
    embargo_sessions: int = RANKING_V3_EMBARGO_SESSIONS
    boundary_rule: str = "next_window_starts_on_session_26_after_25_complete_embargo_sessions"
    training_window_start: date = RANKING_V3_TRAINING_START
    training_window_end: date = RANKING_V3_TRAINING_END
    validation_window_start: date = RANKING_V3_VALIDATION_START
    validation_window_end: date = RANKING_V3_VALIDATION_END
    historical_audit_window_start: date = RANKING_V3_HISTORICAL_AUDIT_START
    historical_audit_window_end: date = RANKING_V3_HISTORICAL_AUDIT_END


class RankingV3OutcomeSemanticsDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    implementation_version: str = RANKING_V3_NOT_TRIGGERED_SEMANTICS_VERSION
    resolved_status: str = "resolved"
    not_triggered_status: str = "not_triggered"
    not_triggered_semantics: str = (
        "entry_condition_never_met_with_complete_market_data_zero_capital_deployed"
    )
    not_triggered_return_pct: float = 0.0
    invalid_or_censored_semantics: str = (
        "insufficient_invalid_unfillable_outside_range_or_missing_price_is_excluded"
    )
    invalid_or_censored_return_policy: str = "never_impute_zero_return"
    coverage_denominator: str = "all_candidate_outcome_ledger_rows"
    coverage_numerator: str = "resolved_plus_valid_not_triggered_rows"


class RankingV3BenchmarkDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    implementation_version: str = "ranking-v3-benchmark-definition-v1"
    candidate_outcome_benchmark_ids: tuple[str, ...] = RANKING_V3_CANDIDATE_BENCHMARK_IDS
    candidate_outcome_aggregation: str = "median_of_all_four_required_benchmarks"
    candidate_outcome_missing_policy: str = "fail_closed_if_any_benchmark_is_missing"
    completed_candidate_interval: str = "actual_entry_session_to_actual_exit_session_inclusive"
    not_triggered_candidate_interval: str = (
        "signal_session_to_entry_wait_plus_holding_maturity_session_inclusive"
    )
    price_field: str = "adjusted_close_fallback_close"
    historical_portfolio_benchmark_id: str = (
        RANKING_V3_HISTORICAL_PORTFOLIO_BENCHMARK_ID
    )
    historical_portfolio_benchmark_semantics: str = (
        "equal_weight_point_in_time_tradable_universe_with_coverage_gate"
    )
    forward_release_benchmark_id: str = RANKING_V3_FORWARD_BENCHMARK_ID
    forward_release_benchmark_semantics: str = (
        "hs300_adjusted_close_same_session_mark_to_market"
    )


class RankingV3Protocol(BaseModel):
    model_config = ConfigDict(frozen=True)

    protocol_schema_version: str = RANKING_V3_PROTOCOL_SCHEMA_VERSION
    protocol_id: str = RANKING_V3_PROTOCOL_ID
    model_version: str = RANKING_V3_MODEL_VERSION
    candidate_ledger_implementation_version: str = (
        RANKING_V3_CANDIDATE_LEDGER_IMPLEMENTATION_VERSION
    )
    statistics_implementation_version: str = RANKING_V3_STATISTICS_IMPLEMENTATION_VERSION
    protocol_digest: str
    frozen_on: date = date(2026, 7, 26)
    prospective_shadow_start: date = RANKING_V3_PROSPECTIVE_SHADOW_START
    candidate_pool_limit: int = RANKING_V3_CANDIDATE_POOL_LIMIT
    max_positions: int = RANKING_V3_MAX_POSITIONS
    max_per_strategy: int = RANKING_V3_MAX_PER_STRATEGY
    max_per_industry: int = RANKING_V3_MAX_PER_INDUSTRY
    max_etf_index_overlap: int = RANKING_V3_MAX_ETF_INDEX_OVERLAP
    embargo_sessions: int = RANKING_V3_EMBARGO_SESSIONS
    prior_experiment_count: int
    registered_holm_p_values: tuple[float, ...]
    experiment_registry: RankingV3ExperimentRegistry
    ranking_definition: RankingV3SortingDefinition
    cost_definition: RankingV3CostDefinition
    statistics_definition: RankingV3StatisticalDefinition
    temporal_isolation_definition: RankingV3TemporalIsolationDefinition
    outcome_semantics_definition: RankingV3OutcomeSemanticsDefinition
    benchmark_definition: RankingV3BenchmarkDefinition
    primary_metric: str = "paired_net_excess_return_vs_constraint_matched_baseline"
    training_evidence_rule: str = "outcome_available_at_strictly_before_decision_date"
    sample_unit: str = "three_rebalance_moving_block"
    historical_oos_label: str = "historical_reused_oos"
    official_recommendation_isolation: str = (
        "shadow_only_until_all_gates_and_forward_validation_pass"
    )
    windows: list[RankingV3Window] = Field(default_factory=list)
    thresholds: RankingV3GateThresholds = Field(default_factory=RankingV3GateThresholds)


def build_ranking_v3_protocol(
    *,
    experiment_registry: RankingV3ExperimentRegistry | None = None,
) -> RankingV3Protocol:
    registry = experiment_registry or build_ranking_v3_experiment_registry()
    registry.require_valid()
    stable_payload = _ranking_v3_protocol_payload(registry)
    protocol = RankingV3Protocol(
        **stable_payload,
        protocol_digest=_digest(stable_payload),
    )
    _validate_protocol_semantics(protocol)
    if not ranking_v3_protocol_digest_is_valid(protocol):
        raise RuntimeError("Ranking V3 protocol digest validation failed")
    return protocol


def ranking_v3_protocol_digest_is_valid(protocol: RankingV3Protocol) -> bool:
    try:
        _validate_protocol_semantics(protocol)
    except (RankingV3ProtocolError, RuntimeError, ValueError):
        return False
    payload = protocol.model_dump(mode="json", exclude={"protocol_digest"})
    return protocol.protocol_digest == _digest(payload)


def _ranking_v3_protocol_payload(
    experiment_registry: RankingV3ExperimentRegistry,
) -> dict[str, object]:
    return {
        "protocol_schema_version": RANKING_V3_PROTOCOL_SCHEMA_VERSION,
        "protocol_id": RANKING_V3_PROTOCOL_ID,
        "model_version": RANKING_V3_MODEL_VERSION,
        "candidate_ledger_implementation_version": (
            RANKING_V3_CANDIDATE_LEDGER_IMPLEMENTATION_VERSION
        ),
        "statistics_implementation_version": (RANKING_V3_STATISTICS_IMPLEMENTATION_VERSION),
        "frozen_on": "2026-07-26",
        "prospective_shadow_start": "2026-07-27",
        "candidate_pool_limit": RANKING_V3_CANDIDATE_POOL_LIMIT,
        "max_positions": RANKING_V3_MAX_POSITIONS,
        "max_per_strategy": RANKING_V3_MAX_PER_STRATEGY,
        "max_per_industry": RANKING_V3_MAX_PER_INDUSTRY,
        "max_etf_index_overlap": RANKING_V3_MAX_ETF_INDEX_OVERLAP,
        "embargo_sessions": RANKING_V3_EMBARGO_SESSIONS,
        "prior_experiment_count": experiment_registry.prior_attempt_count,
        "registered_holm_p_values": (experiment_registry.confirmatory_holm_p_values()),
        "experiment_registry": experiment_registry.model_dump(mode="json"),
        "ranking_definition": RankingV3SortingDefinition().model_dump(mode="json"),
        "cost_definition": RankingV3CostDefinition().model_dump(mode="json"),
        "statistics_definition": RankingV3StatisticalDefinition().model_dump(mode="json"),
        "temporal_isolation_definition": (
            RankingV3TemporalIsolationDefinition().model_dump(mode="json")
        ),
        "outcome_semantics_definition": (
            RankingV3OutcomeSemanticsDefinition().model_dump(mode="json")
        ),
        "benchmark_definition": RankingV3BenchmarkDefinition().model_dump(mode="json"),
        "primary_metric": "paired_net_excess_return_vs_constraint_matched_baseline",
        "training_evidence_rule": ("outcome_available_at_strictly_before_decision_date"),
        "sample_unit": "three_rebalance_moving_block",
        "historical_oos_label": "historical_reused_oos",
        "official_recommendation_isolation": (
            "shadow_only_until_all_gates_and_forward_validation_pass"
        ),
        "windows": [
            {
                "key": "train",
                "label": "训练期",
                "start_date": RANKING_V3_TRAINING_START.isoformat(),
                "end_date": RANKING_V3_TRAINING_END.isoformat(),
                "role": "model_development",
            },
            {
                "key": "validation",
                "label": "验证期",
                "start_date": RANKING_V3_VALIDATION_START.isoformat(),
                "end_date": RANKING_V3_VALIDATION_END.isoformat(),
                "role": "model_selection",
            },
            {
                "key": "historical_reused_oos",
                "label": "历史审计样本外",
                "start_date": RANKING_V3_HISTORICAL_AUDIT_START.isoformat(),
                "end_date": RANKING_V3_HISTORICAL_AUDIT_END.isoformat(),
                "role": "shadow_admission_only",
            },
            {
                "key": "prospective_shadow",
                "label": "前向影子验证",
                "start_date": RANKING_V3_PROSPECTIVE_SHADOW_START.isoformat(),
                "end_date": None,
                "role": "confirmatory_forward_validation",
            },
        ],
        "thresholds": RankingV3GateThresholds().model_dump(mode="json"),
    }


def _validate_protocol_semantics(protocol: RankingV3Protocol) -> None:
    protocol.experiment_registry.require_valid()
    if protocol.prior_experiment_count != protocol.experiment_registry.prior_attempt_count:
        raise RankingV3ProtocolError("protocol experiment count does not match its frozen registry")
    if (
        protocol.registered_holm_p_values
        != protocol.experiment_registry.confirmatory_holm_p_values()
    ):
        raise RankingV3ProtocolError(
            "protocol Holm family does not match observed registry p-values"
        )

    temporal = protocol.temporal_isolation_definition
    if temporal.purge_sessions != protocol.embargo_sessions:
        raise RankingV3ProtocolError("purge sessions do not match protocol embargo")
    if temporal.embargo_sessions != protocol.embargo_sessions:
        raise RankingV3ProtocolError("temporal embargo does not match protocol embargo")
    windows = {item.key: item for item in protocol.windows}
    if len(windows) != len(protocol.windows):
        raise RankingV3ProtocolError("protocol window keys must be unique")
    required_windows = {"train", "validation", "historical_reused_oos"}
    if not required_windows.issubset(windows):
        raise RankingV3ProtocolError("protocol is missing a frozen validation window")
    expected_boundaries = {
        "train": (temporal.training_window_start, temporal.training_window_end),
        "validation": (
            temporal.validation_window_start,
            temporal.validation_window_end,
        ),
        "historical_reused_oos": (
            temporal.historical_audit_window_start,
            temporal.historical_audit_window_end,
        ),
    }
    for key, (expected_start, expected_end) in expected_boundaries.items():
        if windows[key].start_date != expected_start or windows[key].end_date != expected_end:
            raise RankingV3ProtocolError(f"{key} dates do not match temporal isolation definition")
    _validate_strict_session_boundary(
        label="train_to_validation",
        previous_window_end=temporal.training_window_end,
        next_window_start=temporal.validation_window_start,
        required_gap_sessions=temporal.embargo_sessions,
    )
    _validate_strict_session_boundary(
        label="validation_to_historical_audit",
        previous_window_end=temporal.validation_window_end,
        next_window_start=temporal.historical_audit_window_start,
        required_gap_sessions=temporal.embargo_sessions,
    )

    thresholds = protocol.thresholds
    if not (
        0.0 < thresholds.minimum_valid_outcome_coverage_ratio <= 1.0
        and 0.0 < thresholds.minimum_paired_rebalance_date_coverage_ratio <= 1.0
        and 0.0 < thresholds.minimum_stratified_outcome_coverage_ratio <= 1.0
        and 0.0 < thresholds.minimum_benchmark_member_coverage_ratio <= 1.0
        and 0.0 <= thresholds.maximum_invalid_outcome_ratio < 1.0
    ):
        raise RankingV3ProtocolError("outcome coverage thresholds are out of range")
    if thresholds.minimum_stratified_coverage_group_size <= 0:
        raise RankingV3ProtocolError("stratified coverage group size must be positive")
    if not math.isclose(
        thresholds.minimum_valid_outcome_coverage_ratio,
        1.0 - thresholds.maximum_invalid_outcome_ratio,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise RankingV3ProtocolError("valid and invalid outcome coverage gates are inconsistent")

    outcomes = protocol.outcome_semantics_definition
    if outcomes.resolved_status == outcomes.not_triggered_status:
        raise RankingV3ProtocolError("resolved and not-triggered statuses must remain distinct")
    if outcomes.invalid_or_censored_return_policy != "never_impute_zero_return":
        raise RankingV3ProtocolError(
            "invalid or censored outcomes must not be assigned zero return"
        )

    sorting = protocol.ranking_definition
    for label, weights in (
        ("ETF feature", sorting.etf_feature_weights),
        ("stock feature", sorting.stock_feature_weights),
        ("evidence segment", sorting.evidence_segment_weights),
    ):
        if not math.isclose(
            math.fsum(weights.values()),
            1.0,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise RankingV3ProtocolError(f"{label} weights must sum to one")
    if not math.isclose(
        sorting.calibration_alpha_weight + sorting.calibration_win_weight,
        1.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise RankingV3ProtocolError("calibration weights must sum to one")
    if (
        protocol.statistics_definition.entry_wait_sessions <= 0
        or protocol.statistics_definition.holding_sessions <= 0
        or protocol.statistics_definition.rebalance_step_sessions <= 0
    ):
        raise RankingV3ProtocolError(
            "entry wait, holding and rebalance sessions must be positive"
        )
    minimum_forward_window = (
        protocol.thresholds.minimum_forward_shadow_sessions
        + protocol.statistics_definition.entry_wait_sessions
        + protocol.statistics_definition.holding_sessions
    )
    if protocol.thresholds.maximum_forward_shadow_sessions < minimum_forward_window:
        raise RankingV3ProtocolError(
            "forward maximum sessions must cover collection, entry wait and holding"
        )
    if protocol.statistics_definition.dependence_block_length <= 0:
        raise RankingV3ProtocolError("statistical block length must be positive")
    expected_block_length = math.ceil(
        (
            protocol.statistics_definition.entry_wait_sessions
            + protocol.statistics_definition.holding_sessions
        )
        / protocol.statistics_definition.rebalance_step_sessions
    )
    if protocol.statistics_definition.dependence_block_length != expected_block_length:
        raise RankingV3ProtocolError(
            "statistical block length must match full label-span/rebalance overlap"
        )
    statistics_definition = protocol.statistics_definition
    if (
        statistics_definition.deflated_sharpe_evidence_policy
        != "frozen_common_date_model_matrix_with_full_registered_trial_penalty"
        or statistics_definition.deflated_sharpe_trial_distribution_source
        != "frozen_six_model_paired_excess_sharpes_and_registered_attempt_count"
    ):
        raise RankingV3ProtocolError(
            "Deflated Sharpe must use the frozen common-date matrix and full trial count"
        )
    if statistics_definition.pbo_method != CSCV_PBO_METHOD:
        raise RankingV3ProtocolError("PBO method does not match the implemented CSCV method")
    if statistics_definition.pbo_scope != PBO_SCOPE_FROZEN_SIX_MODEL_FAMILY:
        raise RankingV3ProtocolError("PBO scope must disclose the frozen six-model family")
    if statistics_definition.pbo_is_full_search_process_estimate:
        raise RankingV3ProtocolError(
            "six-model-family PBO cannot claim full search-process coverage"
        )
    if statistics_definition.pbo_block_count < 4 or statistics_definition.pbo_block_count % 2:
        raise RankingV3ProtocolError("PBO block count must be an even integer >= 4")
    if statistics_definition.pbo_purge_rebalance_cohorts != expected_block_length - 1:
        raise RankingV3ProtocolError(
            "PBO purge must remove every overlapping adjacent rebalance cohort"
        )
    if not 0.0 < statistics_definition.pbo_date_coverage_threshold <= 1.0:
        raise RankingV3ProtocolError("PBO date coverage threshold is out of range")
    pbo_model_ids = [item.model_id for item in statistics_definition.pbo_model_family]
    if len(pbo_model_ids) < 3 or len(pbo_model_ids) != len(set(pbo_model_ids)):
        raise RankingV3ProtocolError("PBO model family must contain at least 3 unique models")
    if tuple(pbo_model_ids) != RANKING_V3_FROZEN_PBO_MODEL_IDS:
        raise RankingV3ProtocolError(
            "PBO model family must match the disclosed frozen six-model family"
        )
    for model in statistics_definition.pbo_model_family:
        for label, weights in (
            ("PBO stock feature", model.stock_feature_weights),
            ("PBO ETF feature", model.etf_feature_weights),
        ):
            if weights and not math.isclose(
                math.fsum(weights.values()),
                1.0,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise RankingV3ProtocolError(f"{label} weights must sum to one")

    benchmarks = protocol.benchmark_definition
    if (
        benchmarks.candidate_outcome_benchmark_ids
        != RANKING_V3_CANDIDATE_BENCHMARK_IDS
        or len(set(benchmarks.candidate_outcome_benchmark_ids))
        != len(RANKING_V3_CANDIDATE_BENCHMARK_IDS)
    ):
        raise RankingV3ProtocolError("candidate benchmark family does not match frozen protocol")
    if benchmarks.candidate_outcome_aggregation != "median_of_all_four_required_benchmarks":
        raise RankingV3ProtocolError("candidate benchmark aggregation is not frozen")
    if benchmarks.candidate_outcome_missing_policy != "fail_closed_if_any_benchmark_is_missing":
        raise RankingV3ProtocolError("candidate benchmark missing-data policy must fail closed")
    if (
        benchmarks.historical_portfolio_benchmark_id
        != RANKING_V3_HISTORICAL_PORTFOLIO_BENCHMARK_ID
        or benchmarks.forward_release_benchmark_id != RANKING_V3_FORWARD_BENCHMARK_ID
    ):
        raise RankingV3ProtocolError("portfolio or forward benchmark id is not frozen")

    scenario_keys = [item.key for item in protocol.cost_definition.sensitivity_scenarios]
    if len(scenario_keys) != len(set(scenario_keys)):
        raise RankingV3ProtocolError("cost scenario keys must be unique")


def _validate_strict_session_boundary(
    *,
    label: str,
    previous_window_end: date,
    next_window_start: date,
    required_gap_sessions: int,
) -> None:
    if required_gap_sessions <= 0:
        raise RankingV3ProtocolError(f"{label} requires a positive trading-session gap")
    if next_window_start <= previous_window_end:
        raise RankingV3ProtocolError(f"{label} windows overlap or are reversed")

    try:
        previous_end_session = trading_sessions_in_range(
            previous_window_end,
            previous_window_end,
        )
        next_start_session = trading_sessions_in_range(
            next_window_start,
            next_window_start,
        )
        gap_sessions = trading_sessions_in_range(
            previous_window_end + timedelta(days=1),
            next_window_start - timedelta(days=1),
        )
        expected_next_start = trading_day_offset(
            previous_window_end,
            required_gap_sessions + 1,
        )
    except Exception as exc:
        raise RankingV3ProtocolError(
            f"{label} A-share trading calendar calculation failed"
        ) from exc

    if previous_end_session != [previous_window_end]:
        raise RankingV3ProtocolError(
            f"{label} previous window end is not an A-share trading session"
        )
    if next_start_session != [next_window_start]:
        raise RankingV3ProtocolError(f"{label} next window start is not an A-share trading session")
    if len(gap_sessions) != required_gap_sessions:
        raise RankingV3ProtocolError(
            f"{label} has {len(gap_sessions)} complete gap sessions; "
            f"{required_gap_sessions} are required"
        )
    if next_window_start != expected_next_start:
        raise RankingV3ProtocolError(
            f"{label} must start on {expected_next_start.isoformat()}, "
            f"the session after {required_gap_sessions} complete embargo sessions"
        )


def _digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
