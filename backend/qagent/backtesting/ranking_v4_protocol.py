from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from qagent.backtesting.ranking_v4_experiment_registry import (
    RANKING_V41_EXPERIMENT_REGISTRY_SCHEMA_VERSION,
    RANKING_V42_EXPERIMENT_REGISTRY_SCHEMA_VERSION,
    RANKING_V43_EXPERIMENT_REGISTRY_SCHEMA_VERSION,
    RankingV4ExperimentRegistry,
    build_ranking_v4_experiment_registry,
)


RANKING_V41_PROTOCOL_SCHEMA_VERSION = "ranking-v4.1-preregistered-protocol-v1"
RANKING_V41_PROTOCOL_ID = "QAGENT-RANK-V4.1-PREREGISTERED-20260728"
RANKING_V41_MODEL_VERSION = "two-stage-feature-aware-net-excess-v4.1-preregistered"
RANKING_V42_PROTOCOL_SCHEMA_VERSION = "ranking-v4.2-preregistered-protocol-v1"
RANKING_V42_PROTOCOL_ID = "QAGENT-RANK-V4.2-PREREGISTERED-20260729"
RANKING_V42_MODEL_VERSION = "two-stage-feature-aware-net-excess-v4.2-preregistered"
RANKING_V43_PROTOCOL_SCHEMA_VERSION = "ranking-v4.3-preregistered-protocol-v1"
RANKING_V43_PROTOCOL_ID = "QAGENT-RANK-V4.3-PREREGISTERED-20260729"
RANKING_V43_MODEL_VERSION = "asset-stratified-net-excess-v4.3-preregistered"
RANKING_V4_PROTOCOL_SCHEMA_VERSION = RANKING_V43_PROTOCOL_SCHEMA_VERSION
RANKING_V4_PROTOCOL_ID = RANKING_V43_PROTOCOL_ID
RANKING_V4_MODEL_VERSION = RANKING_V43_MODEL_VERSION
RANKING_V4_DEVELOPMENT_START = date(2021, 11, 1)
RANKING_V4_DEVELOPMENT_END = date(2025, 12, 31)
RANKING_V4_DEVELOPMENT_LOOKBACK_DAYS = 400
RANKING_V4_CANDIDATE_POOL_LIMIT = 50

_CANDIDATE_CHANNEL_QUOTAS = (
    ("baseline", 10),
    ("trend", 8),
    ("breakout", 8),
    ("quality_value", 8),
    ("defensive_low_vol", 8),
    ("etf_industry", 8),
)
_HIERARCHICAL_SHRINKAGE_LEVELS = (
    "global",
    "asset",
    "strategy",
    "strategy_x_market_regime",
)
RANKING_V41_FEATURE_EFFECT_NAMES = (
    "strategy_score",
    "factor_score",
    "valuation",
    "size",
    "quality",
    "momentum",
    "trend_quality",
    "breakout_quality",
    "liquidity",
    "low_risk",
    "risk_filter",
    "reversal",
    "industry_strength",
    "capacity",
    "tail_risk",
    "execution_penalty",
    "data_completeness",
)
RANKING_V41_FEATURE_EFFECT_BUCKET_EDGES = (
    Decimal("0"),
    Decimal("0.333333"),
    Decimal("0.666667"),
    Decimal("1"),
)
_UTILITY_PENALTIES = (
    "not_triggered_benchmark_opportunity_cost",
    "turnover_cost",
    "liquidity_penalty",
    "tail_risk_penalty",
)
_UTILITY_FORMULA = (
    "trigger_probability*triggered_cost_adjusted_net_excess"
    "-(1-trigger_probability)*not_triggered_benchmark_opportunity_cost"
    "-turnover_cost-liquidity_penalty-tail_risk_penalty"
)
_MARKET_REGIME_FEATURES = (
    "market_breadth",
    "benchmark_slope",
    "realized_volatility",
    "cross_sectional_dispersion",
)
_V41_PBO_MODEL_IDS = (
    "constraint_matched_baseline",
    "ranking_v41_full",
    "channel_baseline",
    "channel_trend",
    "channel_breakout",
    "channel_quality_value",
    "channel_defensive_low_vol",
    "channel_etf_industry",
)
_V42_PBO_MODEL_IDS = (
    "constraint_matched_baseline",
    "ranking_v42_full",
    "channel_baseline",
    "channel_trend",
    "channel_breakout",
    "channel_quality_value",
    "channel_defensive_low_vol",
    "channel_etf_industry",
)
_V43_PBO_MODEL_IDS = (
    "constraint_matched_baseline",
    "ranking_v43_full",
    "channel_baseline",
    "channel_trend",
    "channel_breakout",
    "channel_quality_value",
    "channel_defensive_low_vol",
    "channel_etf_industry",
)
_V41_REGISTERED_MODEL_RULES = (
    ("constraint_matched_baseline", "baseline_rank_score_desc"),
    ("ranking_v41_full", "ranking_v41_feature_adjusted_utility_lower_bound_desc"),
    ("channel_baseline", "channel_baseline_score_desc"),
    ("channel_trend", "channel_trend_score_desc"),
    ("channel_breakout", "channel_breakout_score_desc"),
    ("channel_quality_value", "channel_quality_value_score_desc"),
    ("channel_defensive_low_vol", "channel_defensive_low_vol_score_desc"),
    ("channel_etf_industry", "channel_etf_industry_score_desc"),
)
_V42_REGISTERED_MODEL_RULES = (
    ("constraint_matched_baseline", "baseline_rank_score_desc"),
    ("ranking_v42_full", "ranking_v42_direct_realized_utility_lower_bound_desc"),
    ("channel_baseline", "channel_baseline_score_desc"),
    ("channel_trend", "channel_trend_score_desc"),
    ("channel_breakout", "channel_breakout_score_desc"),
    ("channel_quality_value", "channel_quality_value_score_desc"),
    ("channel_defensive_low_vol", "channel_defensive_low_vol_score_desc"),
    ("channel_etf_industry", "channel_etf_industry_score_desc"),
)
_V43_REGISTERED_MODEL_RULES = (
    ("constraint_matched_baseline", "baseline_rank_score_desc"),
    ("ranking_v43_full", "ranking_v43_asset_realized_utility_lower_bound_desc"),
    ("channel_baseline", "channel_baseline_score_desc"),
    ("channel_trend", "channel_trend_score_desc"),
    ("channel_breakout", "channel_breakout_score_desc"),
    ("channel_quality_value", "channel_quality_value_score_desc"),
    ("channel_defensive_low_vol", "channel_defensive_low_vol_score_desc"),
    ("channel_etf_industry", "channel_etf_industry_score_desc"),
)


class RankingV4ProtocolError(RuntimeError):
    """Raised when the Ranking V4 preregistration is weakened or inconsistent."""


class RankingV4CandidateChannel(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    quota: int


class RankingV4CandidateDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    implementation_version: str = "ranking-v4-multi-channel-union-v2"
    channels: tuple[RankingV4CandidateChannel, ...] = Field(
        default_factory=lambda: tuple(
            RankingV4CandidateChannel(key=key, quota=quota)
            for key, quota in _CANDIDATE_CHANNEL_QUOTAS
        )
    )
    total_pool_limit: int = RANKING_V4_CANDIDATE_POOL_LIMIT
    channel_rank_rule: str = (
        "point_in_time_channel_score_desc_then_baseline_score_desc_then_instrument_id_asc"
    )
    deduplication_key: str = "instrument_id"
    channel_precedence: tuple[str, ...] = tuple(key for key, _ in _CANDIDATE_CHANNEL_QUOTAS)
    duplicate_owner_rule: str = "first_channel_in_frozen_precedence"
    deterministic_backfill_rule: str = (
        "best_eligible_channel_score_desc_then_baseline_score_desc_then_instrument_id_asc"
    )
    industry_strength_formula: str = (
        "mean_point_in_time_factor_score_of_candidates_in_same_known_industry"
    )
    point_in_time_feature_provenance_required: bool = True
    point_in_time_cost_provenance_required: bool = True
    future_outcome_use: Literal["forbidden"] = "forbidden"


class RankingV4TwoStageModelDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    implementation_version: str = "ranking-v4.2-two-stage-feature-bin-hierarchical-shrinkage-v1"
    stage_one_name: str = "trigger_probability"
    stage_one_target: str = "entry_condition_triggers_within_frozen_entry_window"
    stage_two_name: str = "triggered_cost_adjusted_net_excess"
    stage_two_target: str = (
        "realized_net_return_after_fees_and_slippage_minus_point_in_time_benchmark_return"
    )
    stage_two_population: str = "valid_triggered_candidates_only"
    hierarchical_shrinkage_levels: tuple[str, ...] = _HIERARCHICAL_SHRINKAGE_LEVELS
    feature_effect_names: tuple[str, ...] = RANKING_V41_FEATURE_EFFECT_NAMES
    feature_bucket_edges: tuple[Decimal, ...] = RANKING_V41_FEATURE_EFFECT_BUCKET_EDGES
    feature_effect_prior_date_strength: Decimal = Decimal("18")
    feature_effect_aggregation: str = (
        "equal_weight_mean_of_available_preregistered_feature_bucket_residual_effects_"
        "for_interpretation_and_ranking_only"
    )
    feature_effect_target_isolation: str = (
        "trigger_features_fit_on_all_valid_candidates_and_return_features_fit_on_triggered_only"
    )
    posterior_interval: str = (
        "single_one_sided_95_lower_bound_from_realized_utility_rebalance_date_blocks"
    )
    minimum_position_lower_bound: Decimal = Decimal("0")
    minimum_position_comparator: Literal["strictly_greater_than"] = "strictly_greater_than"
    missing_market_regime_policy: Literal["fail_closed_ineligible"] = "fail_closed_ineligible"


class RankingV4UtilityDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    implementation_version: str = "ranking-v4.2-portfolio-aligned-utility-v3"
    formula: str = _UTILITY_FORMULA
    benefit_term: str = "trigger_probability_x_triggered_cost_adjusted_net_excess"
    penalty_terms: tuple[str, ...] = _UTILITY_PENALTIES
    replacement_cost_formula: str = (
        "proven_actual_replacement_cost_minus_stage2_embedded_cost_floor_zero;"
        "unproven_incremental_cost_zero"
    )
    benchmark_opportunity_cost_formula: str = "max_zero_benchmark_slope_minus_0.5_times_0.5_pct"
    liquidity_penalty_formula: str = "one_minus_liquidity_score_times_0.25_pct"
    tail_risk_penalty_formula: str = "tail_risk_score_times_0.25_pct"
    optimization_target: str = "portfolio_cost_adjusted_net_excess_after_frozen_constraints"
    cash_utility: Decimal = Decimal("0")


class RankingV4MarketRegimeDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    implementation_version: str = "ranking-v4-point-in-time-market-regime-v2"
    required_features: tuple[str, ...] = _MARKET_REGIME_FEATURES
    market_breadth_formula: str = "share_of_point_in_time_prefilter_factor_scores_gte_0.5"
    benchmark_slope_formula: str = (
        "share_of_required_benchmarks_above_point_in_time_50_session_average"
    )
    realized_volatility_formula: str = "mean_one_minus_point_in_time_low_risk_score"
    cross_sectional_dispersion_formula: str = (
        "min_one_population_stdev_point_in_time_factor_score_times_four"
    )
    minimum_cross_section_count: int = 30
    availability_rule: str = "published_and_available_at_or_before_decision_timestamp"
    missing_feature_policy: Literal["fail_closed_no_position"] = "fail_closed_no_position"
    unknown_regime_trading: Literal["forbidden"] = "forbidden"


class RankingV4PortfolioDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    implementation_version: str = "ranking-v4.2-zero-to-five-auditable-risk-proxy-v1"
    minimum_positions: int = 0
    maximum_positions: int = 5
    cash_allowed: bool = True
    maximum_per_strategy: int = 2
    maximum_per_industry: int = 2
    maximum_shared_etf_underlying_ids: int = 0
    maximum_shared_index_memberships: int = 0
    maximum_per_theme: int = 2
    maximum_per_factor: int = 3
    maximum_pairwise_correlation: Decimal = Decimal("0.8")
    maximum_portfolio_beta: Decimal = Decimal("1.2")
    minimum_liquidity_score: Decimal = Decimal("0.5")
    minimum_capacity_score: Decimal = Decimal("0.5")
    risk_benchmark_id: str = "CN:000300.IDX"
    risk_lookback_sessions: int = 120
    minimum_common_return_observations: int = 60
    candidate_price_rule: str = "point_in_time_adjusted_close_required"
    benchmark_price_rule: str = "adjusted_close_else_raw_index_close"
    missing_constraint_data_policy: Literal["fail_closed_ineligible"] = "fail_closed_ineligible"
    metadata_substitution_policy: str = (
        "missing_point_in_time_industry_or_etf_constituents_never_backfilled_from_production;"
        "known_asset_type_beta_and_minimum_return_history_may_prove_single_name_risk;"
        "every_additional_position_requires_pairwise_return_correlation_evidence"
    )
    etf_overlap_fallback_policy: str = (
        "point_in_time_constituent_overlap_when_available_else_mandatory_pairwise_correlation"
    )
    selection_rule: str = (
        "maximize_frozen_v42_direct_realized_utility_lower_bound_subject_to_constraints;"
        "feature_lower_bounds_rank_and_explain_only"
    )
    incumbent_policy: str = "compare_keep_vs_replace_using_actual_incremental_transaction_cost"
    fixed_incumbent_bonus: Decimal = Decimal("0")


class RankingV4ExecutionDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    implementation_version: str = "ranking-v4-a-share-execution-v1"
    signal_session: str = "D"
    earliest_entry_session: str = "D+1"
    settlement_rule: str = "T+1"
    price_limit_policy: Literal["enforced"] = "enforced"
    suspension_policy: Literal["enforced"] = "enforced"
    fee_policy: str = "all_applicable_fees_deducted"
    slippage_policy: str = "frozen_base_and_stress_slippage_deducted"
    same_day_ambiguous_path_policy: str = "adverse_path_first"
    unfillable_order_policy: str = "not_filled_never_impute_executable_price"


class RankingV4TemporalDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    implementation_version: str = "ranking-v4-point-in-time-isolation-v2"
    decision_feature_rule: str = (
        "economic_available_at_or_before_decision_timestamp_from_pre_run_frozen_dataset"
    )
    training_outcome_rule: str = "outcome_matured_strictly_before_training_cutoff"
    financial_statement_rule: str = "as_published_and_known_on_decision_date"
    index_constituent_rule: str = "point_in_time_membership_on_decision_date"
    historical_ingestion_rule: str = (
        "development_reconstruction_may_be_ingested_later_but_uses_original_economic_dates"
    )
    revision_backfill_rule: str = (
        "dataset_revision_frozen_before_run_and_never_advanced_within_experiment"
    )
    future_market_data_rule: Literal["forbidden"] = "forbidden"
    entry_wait_sessions: int = 5
    holding_sessions: int = 20
    rebalance_step_sessions: int = 10
    candidate_lookback_days: int = RANKING_V4_DEVELOPMENT_LOOKBACK_DAYS
    purge_sessions: int = 25
    embargo_sessions: int = 25
    label_dependency_rebalance_cohorts: int = 3
    pbo_purge_rebalance_cohorts: int = 2


class RankingV4RegisteredModelDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    model_id: str
    candidate_order_rule: str
    portfolio_constraint_rule: str = (
        "same_frozen_v4_constraints_and_exact_total_lower_utility_optimizer"
    )
    invalid_or_missing_date_rule: str = "cash_with_zero_return"


class RankingV4StatisticsDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    implementation_version: str = "ranking-v4.2-paired-block-statistics-v2"
    dependence_block_length: int = 3
    bootstrap_samples: int = 5000
    permutation_samples: int = 10000
    random_seed: int = 404
    pbo_model_ids: tuple[str, ...] = _V42_PBO_MODEL_IDS
    registered_models: tuple[RankingV4RegisteredModelDefinition, ...] = Field(
        default_factory=lambda: tuple(
            RankingV4RegisteredModelDefinition(
                model_id=model_id,
                candidate_order_rule=candidate_order_rule,
            )
            for model_id, candidate_order_rule in _V42_REGISTERED_MODEL_RULES
        )
    )
    pbo_method: str = (
        "cscv_8_contiguous_blocks_70_symmetric_half_splits_purged_overlap_"
        "minimum_24_dates_per_half_mean_return_rank_logit_v5"
    )
    pbo_scope: str = "frozen_eight_model_family_only_not_full_search_process"
    pbo_block_count: int = 8
    pbo_purge_rebalance_cohorts: int = 2
    pbo_date_coverage_threshold: Decimal = Decimal("0.95")
    multiple_testing_method: str = "holm_bonferroni_registered_family"
    deflated_sharpe_method: str = "bailey_lopez_de_prado_non_overlapping_rebalance_blocks"
    trial_ledger_requirement: str = (
        "immutable_registry_must_cover_all_known_research_attempts_before_release"
    )
    unknown_trial_count_policy: Literal["fail_closed_no_release"] = "fail_closed_no_release"


class RankingV4EvidenceWindow(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    start_date: date | None
    end_date: date | None
    role: str
    evidence_label: str
    eligible_for_release_gate: bool
    activation_rule: str | None = None


class RankingV4GateThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    minimum_rebalance_dates: int = 24
    minimum_completed_trades: int = 60
    minimum_profit_factor: Decimal = Decimal("1.10")
    minimum_positive_subperiods: int = 4
    required_subperiods: int = 5
    maximum_drawdown_floor_pct: Decimal = Decimal("-15")
    maximum_holm_adjusted_p_value: Decimal = Decimal("0.05")
    minimum_deflated_sharpe_probability: Decimal = Decimal("0.95")
    maximum_probability_of_backtest_overfit: Decimal = Decimal("0.20")
    minimum_valid_outcome_coverage_ratio: Decimal = Decimal("0.95")
    benchmark_excess_comparator: Literal["strictly_greater_than_zero"] = (
        "strictly_greater_than_zero"
    )
    stress_cost_adjusted_return_comparator: Literal["strictly_greater_than_zero"] = (
        "strictly_greater_than_zero"
    )
    minimum_confirmatory_forward_sessions: int = 20
    maximum_confirmatory_forward_sessions: int = 50
    minimum_confirmatory_forward_trades: int = 10
    unknown_gate_policy: Literal["fail_closed_not_passed"] = "fail_closed_not_passed"
    aggregation_rule: Literal["all_gates_must_pass"] = "all_gates_must_pass"


class RankingV4ConfirmatoryDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    implementation_version: str = "ranking-v4-post-freeze-forward-only-v1"
    protocol_freeze_required: bool = True
    code_freeze_required: bool = True
    code_revision_requirement: str = "full_lowercase_40_character_git_revision"
    start_rule: str = "first_a_share_session_strictly_after_protocol_and_code_freeze_attestation"
    historical_development_evidence_may_satisfy_forward_gate: bool = False
    release_state_before_forward_pass: Literal["shadow_only"] = "shadow_only"
    official_paper_admission_rule: str = (
        "signed_protocol_code_and_dataset_attestation_plus_all_historical_and_forward_gates"
    )


class RankingV4Protocol(BaseModel):
    model_config = ConfigDict(frozen=True)

    protocol_schema_version: str = RANKING_V4_PROTOCOL_SCHEMA_VERSION
    protocol_id: str = RANKING_V4_PROTOCOL_ID
    model_version: str = RANKING_V4_MODEL_VERSION
    preregistered_on: date = date(2026, 7, 29)
    registration_state: Literal["preregistered_code_not_yet_frozen"] = (
        "preregistered_code_not_yet_frozen"
    )
    protocol_digest: str
    experiment_registry: RankingV4ExperimentRegistry
    candidate_definition: RankingV4CandidateDefinition
    model_definition: RankingV4TwoStageModelDefinition
    utility_definition: RankingV4UtilityDefinition
    market_regime_definition: RankingV4MarketRegimeDefinition
    portfolio_definition: RankingV4PortfolioDefinition
    execution_definition: RankingV4ExecutionDefinition
    temporal_definition: RankingV4TemporalDefinition
    statistics_definition: RankingV4StatisticsDefinition
    thresholds: RankingV4GateThresholds
    confirmatory_definition: RankingV4ConfirmatoryDefinition
    evidence_windows: tuple[RankingV4EvidenceWindow, ...]
    predecessor_evidence_policy: str = (
        "ranking_v3_and_ranking_v4_rejections_are_immutable_and_never_reclassified_by_v4.3"
    )
    model_selection_policy: str = (
        "v4.3_is_a_new_preregistered_trial;v4.2_was_superseded_before_code_freeze;"
        "development_window_is_exploratory;"
        "all_post_inspection_changes_count_as_new_trials"
    )
    official_recommendation_isolation: Literal["shadow_only_until_every_gate_passes"] = (
        "shadow_only_until_every_gate_passes"
    )


def build_ranking_v4_protocol(
    *,
    experiment_registry: RankingV4ExperimentRegistry | None = None,
    version: Literal["4.1", "4.2", "4.3"] = "4.3",
) -> RankingV4Protocol:
    registry = experiment_registry or build_ranking_v4_experiment_registry(version=version)
    registry.require_valid()
    payload = _ranking_v4_protocol_payload(registry, version=version)
    protocol = RankingV4Protocol(
        **payload,
        protocol_digest=_digest(payload),
    )
    _validate_protocol_semantics(protocol)
    if not ranking_v4_protocol_digest_is_valid(protocol):
        raise RuntimeError("Ranking V4 protocol digest validation failed")
    return protocol


def ranking_v4_protocol_digest_is_valid(protocol: RankingV4Protocol) -> bool:
    try:
        _validate_protocol_semantics(protocol)
    except (RankingV4ProtocolError, RuntimeError, ValueError):
        return False
    payload = _protocol_stable_payload(protocol)
    return protocol.protocol_digest == _digest(payload)


def _ranking_v4_protocol_payload(
    experiment_registry: RankingV4ExperimentRegistry,
    *,
    version: Literal["4.1", "4.2", "4.3"],
) -> dict[str, object]:
    if version == "4.1":
        protocol_schema_version = RANKING_V41_PROTOCOL_SCHEMA_VERSION
        protocol_id = RANKING_V41_PROTOCOL_ID
        model_version = RANKING_V41_MODEL_VERSION
        preregistered_on = "2026-07-28"
        model_definition = RankingV4TwoStageModelDefinition(
            implementation_version="ranking-v4.1-two-stage-feature-bin-empirical-bayes-v1",
            feature_effect_aggregation=(
                "equal_weight_mean_of_available_preregistered_feature_bucket_residual_effects"
            ),
            posterior_interval="one_sided_lower_credible_bound",
        )
        utility_definition = RankingV4UtilityDefinition(
            implementation_version="ranking-v4-portfolio-aligned-utility-v2",
            replacement_cost_formula=(
                "zero_for_incumbent_else_frozen_candidate_replacement_cost_pct_0.15"
            ),
        )
        portfolio_definition = RankingV4PortfolioDefinition(
            implementation_version="ranking-v4.1-zero-to-five-auditable-risk-proxy-v1",
            selection_rule=(
                "maximize_frozen_v41_feature_adjusted_utility_subject_to_constraints_"
                "and_positive_lower_bound"
            ),
        )
        statistics_definition = RankingV4StatisticsDefinition(
            implementation_version="ranking-v4-paired-block-statistics-v1",
            pbo_model_ids=_V41_PBO_MODEL_IDS,
            registered_models=tuple(
                RankingV4RegisteredModelDefinition(
                    model_id=model_id,
                    candidate_order_rule=candidate_order_rule,
                )
                for model_id, candidate_order_rule in _V41_REGISTERED_MODEL_RULES
            ),
            pbo_method=(
                "cscv_contiguous_blocks_symmetric_half_split_purged_overlap_"
                "mean_return_rank_logit_v4"
            ),
            pbo_block_count=4,
        )
        predecessor_evidence_policy = (
            "ranking_v3_and_ranking_v4_rejections_are_immutable_and_never_"
            "reclassified_by_v4.1"
        )
        model_selection_policy = (
            "development_window_is_exploratory_and_all_post_inspection_changes_"
            "count_as_new_trials"
        )
    elif version == "4.2":
        protocol_schema_version = RANKING_V42_PROTOCOL_SCHEMA_VERSION
        protocol_id = RANKING_V42_PROTOCOL_ID
        model_version = RANKING_V42_MODEL_VERSION
        preregistered_on = "2026-07-29"
        model_definition = RankingV4TwoStageModelDefinition()
        utility_definition = RankingV4UtilityDefinition()
        portfolio_definition = RankingV4PortfolioDefinition()
        statistics_definition = RankingV4StatisticsDefinition()
        predecessor_evidence_policy = (
            "ranking_v3_and_ranking_v4_rejections_are_immutable_and_never_"
            "reclassified_by_v4.2"
        )
        model_selection_policy = (
            "v4.2_is_a_new_preregistered_trial;development_window_is_exploratory;"
            "all_post_inspection_changes_count_as_new_trials"
        )
        candidate_definition = RankingV4CandidateDefinition()
        market_regime_definition = RankingV4MarketRegimeDefinition()
    elif version == "4.3":
        protocol_schema_version = RANKING_V43_PROTOCOL_SCHEMA_VERSION
        protocol_id = RANKING_V43_PROTOCOL_ID
        model_version = RANKING_V43_MODEL_VERSION
        preregistered_on = "2026-07-29"
        candidate_definition = RankingV4CandidateDefinition(
            implementation_version=(
                "ranking-v4.3-asset-stratified-multi-channel-union-v1"
            ),
            deterministic_backfill_rule=(
                "fixed_stock_42_and_etf_8_asset_quotas_no_cross_asset_backfill"
            ),
        )
        model_definition = RankingV4TwoStageModelDefinition(
            implementation_version=(
                "ranking-v4.3-asset-stratified-feature-bin-hierarchical-shrinkage-v1"
            ),
            feature_effect_aggregation=(
                "equal_weight_mean_of_asset_stratified_feature_bucket_residual_effects_"
                "for_interpretation_and_ranking_only"
            ),
        )
        utility_definition = RankingV4UtilityDefinition(
            implementation_version="ranking-v4.3-portfolio-aligned-utility-v1"
        )
        market_regime_definition = RankingV4MarketRegimeDefinition(
            implementation_version="ranking-v4.3-point-in-time-market-regime-v1",
            market_breadth_formula=(
                "share_of_point_in_time_stock_prefilter_factor_scores_gte_0.5"
            ),
        )
        portfolio_definition = RankingV4PortfolioDefinition(
            implementation_version=(
                "ranking-v4.3-zero-to-five-asset-aware-risk-proxy-v1"
            ),
            etf_overlap_fallback_policy=(
                "known_index_membership_overlap_always_blocks;"
                "point_in_time_constituent_overlap_when_available_else_"
                "mandatory_pairwise_correlation"
            ),
            selection_rule=(
                "maximize_frozen_v43_asset_realized_utility_lower_bound_subject_to_"
                "constraints;feature_lower_bounds_rank_and_explain_only"
            ),
        )
        statistics_definition = RankingV4StatisticsDefinition(
            implementation_version="ranking-v4.3-paired-block-statistics-v1",
            pbo_model_ids=_V43_PBO_MODEL_IDS,
            registered_models=tuple(
                RankingV4RegisteredModelDefinition(
                    model_id=model_id,
                    candidate_order_rule=candidate_order_rule,
                )
                for model_id, candidate_order_rule in _V43_REGISTERED_MODEL_RULES
            ),
        )
        predecessor_evidence_policy = (
            "ranking_v3_and_ranking_v4_rejections_are_immutable_and_never_"
            "reclassified_by_v4.3"
        )
        model_selection_policy = (
            "v4.3_is_a_new_preregistered_trial;v4.2_was_superseded_before_code_freeze;"
            "development_window_is_exploratory;"
            "all_post_inspection_changes_count_as_new_trials"
        )
    else:
        raise ValueError("unsupported Ranking V4 protocol version")
    if version in {"4.1", "4.2"}:
        candidate_definition = RankingV4CandidateDefinition()
        market_regime_definition = RankingV4MarketRegimeDefinition()
    return {
        "protocol_schema_version": protocol_schema_version,
        "protocol_id": protocol_id,
        "model_version": model_version,
        "preregistered_on": preregistered_on,
        "registration_state": "preregistered_code_not_yet_frozen",
        "experiment_registry": _protocol_registry_payload(experiment_registry),
        "candidate_definition": candidate_definition.model_dump(mode="json"),
        "model_definition": model_definition.model_dump(mode="json"),
        "utility_definition": utility_definition.model_dump(mode="json"),
        "market_regime_definition": market_regime_definition.model_dump(mode="json"),
        "portfolio_definition": portfolio_definition.model_dump(mode="json"),
        "execution_definition": RankingV4ExecutionDefinition().model_dump(mode="json"),
        "temporal_definition": RankingV4TemporalDefinition().model_dump(mode="json"),
        "statistics_definition": statistics_definition.model_dump(mode="json"),
        "thresholds": RankingV4GateThresholds().model_dump(mode="json"),
        "confirmatory_definition": RankingV4ConfirmatoryDefinition().model_dump(mode="json"),
        "evidence_windows": [
            {
                "key": "development",
                "start_date": RANKING_V4_DEVELOPMENT_START.isoformat(),
                "end_date": RANKING_V4_DEVELOPMENT_END.isoformat(),
                "role": "model_development_and_selection",
                "evidence_label": "exploratory_development_evidence",
                "eligible_for_release_gate": False,
                "activation_rule": None,
            },
            {
                "key": "confirmatory_forward",
                "start_date": None,
                "end_date": None,
                "role": "post_freeze_confirmatory_forward_validation",
                "evidence_label": "confirmatory_forward_evidence",
                "eligible_for_release_gate": True,
                "activation_rule": (
                    "start_only_after_signed_protocol_and_full_code_revision_are_frozen"
                ),
            },
        ],
        "predecessor_evidence_policy": predecessor_evidence_policy,
        "model_selection_policy": model_selection_policy,
        "official_recommendation_isolation": "shadow_only_until_every_gate_passes",
    }


def _protocol_stable_payload(protocol: RankingV4Protocol) -> dict[str, object]:
    payload = protocol.model_dump(mode="json", exclude={"protocol_digest"})
    payload["experiment_registry"] = _protocol_registry_payload(
        protocol.experiment_registry
    )
    return payload


def _protocol_registry_payload(
    registry: RankingV4ExperimentRegistry,
) -> dict[str, object]:
    payload = registry.model_dump(mode="json")
    if registry.schema_version == RANKING_V41_EXPERIMENT_REGISTRY_SCHEMA_VERSION:
        for key in (
            "historical_trial_inventory_complete",
            "historical_trial_inventory_digest",
            "historical_trial_return_series_digests",
        ):
            payload.pop(key, None)
    return payload


def _validate_protocol_semantics(protocol: RankingV4Protocol) -> None:
    protocol.experiment_registry.require_valid()
    identity = (
        protocol.protocol_schema_version,
        protocol.protocol_id,
        protocol.model_version,
    )
    if identity == (
        RANKING_V41_PROTOCOL_SCHEMA_VERSION,
        RANKING_V41_PROTOCOL_ID,
        RANKING_V41_MODEL_VERSION,
    ):
        version: Literal["4.1", "4.2", "4.3"] = "4.1"
    elif identity == (
        RANKING_V42_PROTOCOL_SCHEMA_VERSION,
        RANKING_V42_PROTOCOL_ID,
        RANKING_V42_MODEL_VERSION,
    ):
        version = "4.2"
    elif identity == (
        RANKING_V43_PROTOCOL_SCHEMA_VERSION,
        RANKING_V43_PROTOCOL_ID,
        RANKING_V43_MODEL_VERSION,
    ):
        version = "4.3"
    else:
        raise RankingV4ProtocolError("Ranking V4 identity cannot be rewritten")
    expected_registry_schema = {
        "4.1": RANKING_V41_EXPERIMENT_REGISTRY_SCHEMA_VERSION,
        "4.2": RANKING_V42_EXPERIMENT_REGISTRY_SCHEMA_VERSION,
        "4.3": RANKING_V43_EXPERIMENT_REGISTRY_SCHEMA_VERSION,
    }[version]
    if protocol.experiment_registry.schema_version != expected_registry_schema:
        raise RankingV4ProtocolError("protocol and experiment registry versions disagree")
    expected_registration_date = (
        date(2026, 7, 28) if version == "4.1" else date(2026, 7, 29)
    )
    if protocol.preregistered_on != expected_registration_date:
        raise RankingV4ProtocolError("Ranking V4 preregistration date cannot be rewritten")
    if protocol.registration_state != "preregistered_code_not_yet_frozen":
        raise RankingV4ProtocolError("V4 cannot claim frozen code before a Git revision exists")

    candidate = protocol.candidate_definition
    quotas = tuple((item.key, item.quota) for item in candidate.channels)
    if quotas != _CANDIDATE_CHANNEL_QUOTAS:
        raise RankingV4ProtocolError("candidate channels and quotas must match preregistration")
    if candidate.channel_precedence != tuple(key for key, _ in _CANDIDATE_CHANNEL_QUOTAS):
        raise RankingV4ProtocolError("candidate channel precedence is not frozen")
    if sum(item.quota for item in candidate.channels) != RANKING_V4_CANDIDATE_POOL_LIMIT:
        raise RankingV4ProtocolError("candidate quotas must sum to the frozen pool limit")
    if candidate.total_pool_limit != RANKING_V4_CANDIDATE_POOL_LIMIT:
        raise RankingV4ProtocolError("candidate pool limit must remain 50")
    if candidate.future_outcome_use != "forbidden":
        raise RankingV4ProtocolError("candidate construction cannot use future outcomes")
    expected_candidate_implementation = (
        "ranking-v4.3-asset-stratified-multi-channel-union-v1"
        if version == "4.3"
        else "ranking-v4-multi-channel-union-v2"
    )
    expected_backfill = (
        "fixed_stock_42_and_etf_8_asset_quotas_no_cross_asset_backfill"
        if version == "4.3"
        else "best_eligible_channel_score_desc_then_baseline_score_desc_then_instrument_id_asc"
    )
    if (
        candidate.implementation_version != expected_candidate_implementation
        or candidate.channel_rank_rule
        != ("point_in_time_channel_score_desc_then_baseline_score_desc_then_instrument_id_asc")
        or candidate.deduplication_key != "instrument_id"
        or candidate.duplicate_owner_rule != "first_channel_in_frozen_precedence"
        or candidate.deterministic_backfill_rule != expected_backfill
        or candidate.industry_strength_formula
        != "mean_point_in_time_factor_score_of_candidates_in_same_known_industry"
        or not candidate.point_in_time_feature_provenance_required
        or not candidate.point_in_time_cost_provenance_required
    ):
        raise RankingV4ProtocolError("candidate ranking, de-duplication, or backfill was changed")

    model = protocol.model_definition
    expected_model_implementation = {
        "4.1": "ranking-v4.1-two-stage-feature-bin-empirical-bayes-v1",
        "4.2": "ranking-v4.2-two-stage-feature-bin-hierarchical-shrinkage-v1",
        "4.3": "ranking-v4.3-asset-stratified-feature-bin-hierarchical-shrinkage-v1",
    }[version]
    expected_feature_aggregation = {
        "4.1": (
            "equal_weight_mean_of_available_preregistered_feature_bucket_residual_effects"
        ),
        "4.2": (
            "equal_weight_mean_of_available_preregistered_feature_bucket_residual_effects_"
            "for_interpretation_and_ranking_only"
        ),
        "4.3": (
            "equal_weight_mean_of_asset_stratified_feature_bucket_residual_effects_"
            "for_interpretation_and_ranking_only"
        ),
    }[version]
    expected_interval = (
        "one_sided_lower_credible_bound"
        if version == "4.1"
        else "single_one_sided_95_lower_bound_from_realized_utility_rebalance_date_blocks"
    )
    if (
        model.implementation_version != expected_model_implementation
        or model.stage_one_name != "trigger_probability"
        or model.stage_one_target != "entry_condition_triggers_within_frozen_entry_window"
        or model.stage_two_name != "triggered_cost_adjusted_net_excess"
        or model.stage_two_target
        != ("realized_net_return_after_fees_and_slippage_minus_point_in_time_benchmark_return")
        or model.stage_two_population != "valid_triggered_candidates_only"
        or model.hierarchical_shrinkage_levels != _HIERARCHICAL_SHRINKAGE_LEVELS
        or model.feature_effect_names != RANKING_V41_FEATURE_EFFECT_NAMES
        or model.feature_bucket_edges != RANKING_V41_FEATURE_EFFECT_BUCKET_EDGES
        or model.feature_effect_prior_date_strength != Decimal("18")
        or model.feature_effect_aggregation != expected_feature_aggregation
        or model.feature_effect_target_isolation
        != (
            "trigger_features_fit_on_all_valid_candidates_and_return_features_fit_on_triggered_only"
        )
        or model.posterior_interval != expected_interval
    ):
        raise RankingV4ProtocolError("two-stage model or shrinkage hierarchy was changed")
    if (
        model.minimum_position_lower_bound != 0
        or model.minimum_position_comparator != "strictly_greater_than"
    ):
        raise RankingV4ProtocolError(
            "positions require a posterior net-excess lower bound above zero"
        )
    if model.missing_market_regime_policy != "fail_closed_ineligible":
        raise RankingV4ProtocolError("missing market regime must fail closed")

    utility = protocol.utility_definition
    expected_utility_implementation = {
        "4.1": "ranking-v4-portfolio-aligned-utility-v2",
        "4.2": "ranking-v4.2-portfolio-aligned-utility-v3",
        "4.3": "ranking-v4.3-portfolio-aligned-utility-v1",
    }[version]
    expected_replacement_cost = (
        "zero_for_incumbent_else_frozen_candidate_replacement_cost_pct_0.15"
        if version == "4.1"
        else (
            "proven_actual_replacement_cost_minus_stage2_embedded_cost_floor_zero;"
            "unproven_incremental_cost_zero"
        )
    )
    if (
        utility.implementation_version != expected_utility_implementation
        or utility.formula != _UTILITY_FORMULA
        or utility.benefit_term != "trigger_probability_x_triggered_cost_adjusted_net_excess"
        or utility.penalty_terms != _UTILITY_PENALTIES
        or utility.replacement_cost_formula != expected_replacement_cost
        or utility.benchmark_opportunity_cost_formula
        != "max_zero_benchmark_slope_minus_0.5_times_0.5_pct"
        or utility.liquidity_penalty_formula != "one_minus_liquidity_score_times_0.25_pct"
        or utility.tail_risk_penalty_formula != "tail_risk_score_times_0.25_pct"
        or utility.cash_utility != 0
    ):
        raise RankingV4ProtocolError("V4 utility penalties or cash baseline were changed")
    if utility.optimization_target != (
        "portfolio_cost_adjusted_net_excess_after_frozen_constraints"
    ):
        raise RankingV4ProtocolError("V4 must optimize the portfolio release objective")

    regime = protocol.market_regime_definition
    expected_regime_implementation = (
        "ranking-v4.3-point-in-time-market-regime-v1"
        if version == "4.3"
        else "ranking-v4-point-in-time-market-regime-v2"
    )
    expected_breadth_formula = (
        "share_of_point_in_time_stock_prefilter_factor_scores_gte_0.5"
        if version == "4.3"
        else "share_of_point_in_time_prefilter_factor_scores_gte_0.5"
    )
    if (
        regime.implementation_version != expected_regime_implementation
        or regime.required_features != _MARKET_REGIME_FEATURES
        or regime.market_breadth_formula != expected_breadth_formula
        or regime.benchmark_slope_formula
        != "share_of_required_benchmarks_above_point_in_time_50_session_average"
        or regime.realized_volatility_formula != "mean_one_minus_point_in_time_low_risk_score"
        or regime.cross_sectional_dispersion_formula
        != "min_one_population_stdev_point_in_time_factor_score_times_four"
        or regime.minimum_cross_section_count != 30
        or regime.availability_rule != "published_and_available_at_or_before_decision_timestamp"
        or regime.missing_feature_policy != "fail_closed_no_position"
        or regime.unknown_regime_trading != "forbidden"
    ):
        raise RankingV4ProtocolError("unknown market state cannot open a position")

    portfolio = protocol.portfolio_definition
    expected_constraints = (
        portfolio.minimum_positions,
        portfolio.maximum_positions,
        portfolio.maximum_per_strategy,
        portfolio.maximum_per_industry,
        portfolio.maximum_shared_etf_underlying_ids,
        portfolio.maximum_shared_index_memberships,
        portfolio.maximum_per_theme,
        portfolio.maximum_per_factor,
        portfolio.maximum_pairwise_correlation,
        portfolio.maximum_portfolio_beta,
        portfolio.minimum_liquidity_score,
        portfolio.minimum_capacity_score,
        portfolio.risk_benchmark_id,
        portfolio.risk_lookback_sessions,
        portfolio.minimum_common_return_observations,
        portfolio.candidate_price_rule,
        portfolio.benchmark_price_rule,
    )
    if expected_constraints != (
        0,
        5,
        2,
        2,
        0,
        0,
        2,
        3,
        Decimal("0.8"),
        Decimal("1.2"),
        Decimal("0.5"),
        Decimal("0.5"),
        "CN:000300.IDX",
        120,
        60,
        "point_in_time_adjusted_close_required",
        "adjusted_close_else_raw_index_close",
    ):
        raise RankingV4ProtocolError("portfolio constraints do not match preregistration")
    if not portfolio.cash_allowed or portfolio.fixed_incumbent_bonus != 0:
        raise RankingV4ProtocolError("V4 must allow cash and cannot use a fixed incumbent bonus")
    expected_portfolio_implementation = {
        "4.1": "ranking-v4.1-zero-to-five-auditable-risk-proxy-v1",
        "4.2": "ranking-v4.2-zero-to-five-auditable-risk-proxy-v1",
        "4.3": "ranking-v4.3-zero-to-five-asset-aware-risk-proxy-v1",
    }[version]
    expected_selection_rule = {
        "4.1": (
            "maximize_frozen_v41_feature_adjusted_utility_subject_to_constraints_"
            "and_positive_lower_bound"
        ),
        "4.2": (
            "maximize_frozen_v42_direct_realized_utility_lower_bound_subject_to_constraints;"
            "feature_lower_bounds_rank_and_explain_only"
        ),
        "4.3": (
            "maximize_frozen_v43_asset_realized_utility_lower_bound_subject_to_"
            "constraints;feature_lower_bounds_rank_and_explain_only"
        ),
    }[version]
    expected_etf_overlap_policy = (
        "known_index_membership_overlap_always_blocks;"
        "point_in_time_constituent_overlap_when_available_else_"
        "mandatory_pairwise_correlation"
        if version == "4.3"
        else "point_in_time_constituent_overlap_when_available_else_mandatory_pairwise_correlation"
    )
    if (
        portfolio.implementation_version != expected_portfolio_implementation
        or portfolio.missing_constraint_data_policy != "fail_closed_ineligible"
        or portfolio.selection_rule != expected_selection_rule
        or portfolio.incumbent_policy
        != "compare_keep_vs_replace_using_actual_incremental_transaction_cost"
        or portfolio.metadata_substitution_policy
        != (
            "missing_point_in_time_industry_or_etf_constituents_never_backfilled_from_production;"
            "known_asset_type_beta_and_minimum_return_history_may_prove_single_name_risk;"
            "every_additional_position_requires_pairwise_return_correlation_evidence"
        )
        or portfolio.etf_overlap_fallback_policy != expected_etf_overlap_policy
    ):
        raise RankingV4ProtocolError("portfolio selection or missing-data policy was changed")

    execution = protocol.execution_definition
    if (
        execution.signal_session != "D"
        or execution.earliest_entry_session != "D+1"
        or execution.settlement_rule != "T+1"
        or execution.price_limit_policy != "enforced"
        or execution.suspension_policy != "enforced"
        or execution.fee_policy != "all_applicable_fees_deducted"
        or execution.slippage_policy != "frozen_base_and_stress_slippage_deducted"
        or execution.same_day_ambiguous_path_policy != "adverse_path_first"
        or execution.unfillable_order_policy != "not_filled_never_impute_executable_price"
    ):
        raise RankingV4ProtocolError("A-share execution semantics were weakened")

    temporal = protocol.temporal_definition
    if (
        temporal.implementation_version != "ranking-v4-point-in-time-isolation-v2"
        or temporal.decision_feature_rule
        != ("economic_available_at_or_before_decision_timestamp_from_pre_run_frozen_dataset")
        or temporal.training_outcome_rule != "outcome_matured_strictly_before_training_cutoff"
        or temporal.financial_statement_rule != "as_published_and_known_on_decision_date"
        or temporal.index_constituent_rule != "point_in_time_membership_on_decision_date"
        or temporal.historical_ingestion_rule
        != ("development_reconstruction_may_be_ingested_later_but_uses_original_economic_dates")
        or temporal.revision_backfill_rule
        != "dataset_revision_frozen_before_run_and_never_advanced_within_experiment"
        or temporal.future_market_data_rule != "forbidden"
    ):
        raise RankingV4ProtocolError("point-in-time evidence isolation was weakened")
    if (
        temporal.entry_wait_sessions,
        temporal.holding_sessions,
        temporal.rebalance_step_sessions,
        temporal.candidate_lookback_days,
        temporal.purge_sessions,
        temporal.embargo_sessions,
        temporal.label_dependency_rebalance_cohorts,
        temporal.pbo_purge_rebalance_cohorts,
    ) != (5, 20, 10, RANKING_V4_DEVELOPMENT_LOOKBACK_DAYS, 25, 25, 3, 2):
        raise RankingV4ProtocolError("label span, purge, or embargo no longer matches protocol")

    statistics = protocol.statistics_definition
    expected_model_ids = {
        "4.1": _V41_PBO_MODEL_IDS,
        "4.2": _V42_PBO_MODEL_IDS,
        "4.3": _V43_PBO_MODEL_IDS,
    }[version]
    expected_model_rules = {
        "4.1": _V41_REGISTERED_MODEL_RULES,
        "4.2": _V42_REGISTERED_MODEL_RULES,
        "4.3": _V43_REGISTERED_MODEL_RULES,
    }[version]
    expected_statistics_implementation = {
        "4.1": "ranking-v4-paired-block-statistics-v1",
        "4.2": "ranking-v4.2-paired-block-statistics-v2",
        "4.3": "ranking-v4.3-paired-block-statistics-v1",
    }[version]
    expected_pbo_method = (
        "cscv_contiguous_blocks_symmetric_half_split_purged_overlap_"
        "mean_return_rank_logit_v4"
        if version == "4.1"
        else (
            "cscv_8_contiguous_blocks_70_symmetric_half_splits_purged_overlap_"
            "minimum_24_dates_per_half_mean_return_rank_logit_v5"
        )
    )
    expected_block_count = 4 if version == "4.1" else 8
    if (
        statistics.implementation_version != expected_statistics_implementation
        or statistics.dependence_block_length != 3
        or statistics.bootstrap_samples != 5000
        or statistics.permutation_samples != 10000
        or statistics.random_seed != 404
        or statistics.pbo_model_ids != expected_model_ids
        or tuple(
            (item.model_id, item.candidate_order_rule) for item in statistics.registered_models
        )
        != expected_model_rules
        or any(
            item.portfolio_constraint_rule
            != "same_frozen_v4_constraints_and_exact_total_lower_utility_optimizer"
            or item.invalid_or_missing_date_rule != "cash_with_zero_return"
            for item in statistics.registered_models
        )
        or statistics.pbo_method != expected_pbo_method
        or statistics.pbo_scope != "frozen_eight_model_family_only_not_full_search_process"
        or statistics.pbo_block_count != expected_block_count
        or statistics.pbo_purge_rebalance_cohorts != 2
        or statistics.pbo_date_coverage_threshold != Decimal("0.95")
        or statistics.multiple_testing_method != "holm_bonferroni_registered_family"
        or statistics.deflated_sharpe_method
        != "bailey_lopez_de_prado_non_overlapping_rebalance_blocks"
        or statistics.trial_ledger_requirement
        != "immutable_registry_must_cover_all_known_research_attempts_before_release"
        or statistics.unknown_trial_count_policy != "fail_closed_no_release"
    ):
        raise RankingV4ProtocolError("statistical family or inference method was changed")

    thresholds = protocol.thresholds
    if thresholds.minimum_rebalance_dates < 24:
        raise RankingV4ProtocolError("rebalance gate cannot be weaker than 24")
    if thresholds.minimum_completed_trades < 60:
        raise RankingV4ProtocolError("completed-trade gate cannot be weaker than 60")
    if thresholds.minimum_profit_factor < Decimal("1.10"):
        raise RankingV4ProtocolError("profit-factor gate cannot be weaker than 1.10")
    if (
        thresholds.required_subperiods != 5
        or thresholds.minimum_positive_subperiods < 4
        or thresholds.minimum_positive_subperiods > thresholds.required_subperiods
    ):
        raise RankingV4ProtocolError("subperiod gate cannot be weaker than four of five")
    if thresholds.maximum_drawdown_floor_pct < Decimal("-15"):
        raise RankingV4ProtocolError("maximum-drawdown gate cannot be weaker than -15%")
    if thresholds.maximum_holm_adjusted_p_value > Decimal("0.05"):
        raise RankingV4ProtocolError("Holm gate cannot be weaker than 0.05")
    if thresholds.minimum_deflated_sharpe_probability < Decimal("0.95"):
        raise RankingV4ProtocolError("Deflated Sharpe gate cannot be weaker than 0.95")
    if thresholds.maximum_probability_of_backtest_overfit > Decimal("0.20"):
        raise RankingV4ProtocolError("PBO gate cannot be weaker than 0.20")
    if thresholds.minimum_valid_outcome_coverage_ratio < Decimal("0.95"):
        raise RankingV4ProtocolError("coverage gate cannot be weaker than 0.95")
    if (
        thresholds.minimum_confirmatory_forward_sessions < 20
        or thresholds.maximum_confirmatory_forward_sessions > 50
        or thresholds.minimum_confirmatory_forward_sessions
        > thresholds.maximum_confirmatory_forward_sessions
        or thresholds.minimum_confirmatory_forward_trades < 10
    ):
        raise RankingV4ProtocolError("confirmatory forward gate must remain within 20-50 sessions")
    if (
        thresholds.benchmark_excess_comparator != "strictly_greater_than_zero"
        or thresholds.stress_cost_adjusted_return_comparator != "strictly_greater_than_zero"
        or thresholds.unknown_gate_policy != "fail_closed_not_passed"
        or thresholds.aggregation_rule != "all_gates_must_pass"
    ):
        raise RankingV4ProtocolError("unknown or failed gates cannot be bypassed")

    windows = {item.key: item for item in protocol.evidence_windows}
    if len(windows) != 2 or set(windows) != {"development", "confirmatory_forward"}:
        raise RankingV4ProtocolError("development and confirmatory windows must remain distinct")
    development = windows["development"]
    if (
        development.start_date != RANKING_V4_DEVELOPMENT_START
        or development.end_date != RANKING_V4_DEVELOPMENT_END
        or development.evidence_label != "exploratory_development_evidence"
        or development.eligible_for_release_gate
    ):
        raise RankingV4ProtocolError("2021-2025 evidence must remain exploratory")
    confirmatory = windows["confirmatory_forward"]
    if (
        confirmatory.start_date is not None
        or confirmatory.end_date is not None
        or confirmatory.evidence_label != "confirmatory_forward_evidence"
        or not confirmatory.eligible_for_release_gate
        or confirmatory.activation_rule
        != "start_only_after_signed_protocol_and_full_code_revision_are_frozen"
    ):
        raise RankingV4ProtocolError("confirmatory dates cannot be backfilled before code freeze")
    confirmation = protocol.confirmatory_definition
    if (
        not confirmation.protocol_freeze_required
        or not confirmation.code_freeze_required
        or confirmation.code_revision_requirement != "full_lowercase_40_character_git_revision"
        or confirmation.start_rule
        != "first_a_share_session_strictly_after_protocol_and_code_freeze_attestation"
        or confirmation.historical_development_evidence_may_satisfy_forward_gate
        or confirmation.release_state_before_forward_pass != "shadow_only"
        or confirmation.official_paper_admission_rule
        != ("signed_protocol_code_and_dataset_attestation_plus_all_historical_and_forward_gates")
    ):
        raise RankingV4ProtocolError("confirmatory forward evidence must start after both freezes")
    expected_predecessor_policy = {
        "4.1": (
            "ranking_v3_and_ranking_v4_rejections_are_immutable_and_never_"
            "reclassified_by_v4.1"
        ),
        "4.2": (
            "ranking_v3_and_ranking_v4_rejections_are_immutable_and_never_"
            "reclassified_by_v4.2"
        ),
        "4.3": (
            "ranking_v3_and_ranking_v4_rejections_are_immutable_and_never_"
            "reclassified_by_v4.3"
        ),
    }[version]
    if protocol.predecessor_evidence_policy != expected_predecessor_policy:
        raise RankingV4ProtocolError("V3 or V4 rejected evidence cannot be reclassified")
    expected_selection_policy = {
        "4.1": (
            "development_window_is_exploratory_and_all_post_inspection_changes_"
            "count_as_new_trials"
        ),
        "4.2": (
            "v4.2_is_a_new_preregistered_trial;development_window_is_exploratory;"
            "all_post_inspection_changes_count_as_new_trials"
        ),
        "4.3": (
            "v4.3_is_a_new_preregistered_trial;v4.2_was_superseded_before_code_freeze;"
            "development_window_is_exploratory;"
            "all_post_inspection_changes_count_as_new_trials"
        ),
    }[version]
    if protocol.model_selection_policy != expected_selection_policy:
        raise RankingV4ProtocolError("post-inspection V4 changes must count as new trials")
    if protocol.official_recommendation_isolation != "shadow_only_until_every_gate_passes":
        raise RankingV4ProtocolError("official paper trading must remain isolated")


def _digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
