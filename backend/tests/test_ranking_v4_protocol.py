from __future__ import annotations

import ast
import hashlib
import inspect
import json
from decimal import Decimal

import pytest

from qagent.backtesting.ranking_v4_experiment_registry import (
    RankingV4ExperimentRegistryError,
    build_ranking_v3_rejected_summary,
    build_ranking_v4_rejected_summary,
    build_ranking_v4_experiment_registry,
    ranking_v4_experiment_registry_digest_is_valid,
)
from qagent.backtesting.ranking_v4_protocol import (
    build_ranking_v4_protocol,
    ranking_v4_protocol_digest_is_valid,
)

FROZEN_V41_PROTOCOL_DIGEST = "8d95996fbccc99c4df2d458220ead0147e51f3a2b2032628e59572e580eea6e3"
FROZEN_V41_REGISTRY_DIGEST = "63ae2333f8ed1ef1d45c9551143f5fbffdc1ff0f4fb479e3e26139a49c597298"
FROZEN_V42_PROTOCOL_DIGEST = "68bcddae550c28b59c79a325f36bd4cab2676e47390f7e2842e2327ce59988f4"
FROZEN_V42_REGISTRY_DIGEST = "3a61e0dfc2dff46a0cbf7d92d68df091e4299090f14216e011ec9e234494b42d"
FROZEN_V3_REJECTED_SUMMARY_DIGEST = (
    "197feb4614d18cf182bc4dbe37cb2a1d3b8a94c847b738aad51835f488d7cf54"
)
FROZEN_V4_REJECTED_SUMMARY_DIGEST = (
    "75ff874d9a9d6cf258d20cf472be377a3dff36657c2f317a0bcd3f1ca81f7fd8"
)


def _rehashed_protocol(protocol, **updates):
    changed = protocol.model_copy(update=updates)
    payload = changed.model_dump(mode="json", exclude={"protocol_digest"})
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return changed.model_copy(update={"protocol_digest": digest})


def test_v4_protocol_is_independent_deterministic_and_covers_full_preregistration():
    first = build_ranking_v4_protocol()
    second = build_ranking_v4_protocol()

    assert first == second
    assert first.protocol_digest == second.protocol_digest
    assert first.protocol_digest == FROZEN_V42_PROTOCOL_DIGEST
    assert ranking_v4_protocol_digest_is_valid(first)
    protocol_source = inspect.getsource(
        __import__("qagent.backtesting.ranking_v4_protocol", fromlist=["ranking_v4_protocol"])
    )
    imported_modules = {
        alias.name
        for node in ast.walk(ast.parse(protocol_source))
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(ast.parse(protocol_source))
        if isinstance(node, ast.ImportFrom)
    }
    assert not any("ranking_v3" in module for module in imported_modules)

    candidate = first.candidate_definition
    assert tuple((item.key, item.quota) for item in candidate.channels) == (
        ("baseline", 10),
        ("trend", 8),
        ("breakout", 8),
        ("quality_value", 8),
        ("defensive_low_vol", 8),
        ("etf_industry", 8),
    )
    assert candidate.total_pool_limit == 50
    assert candidate.deduplication_key == "instrument_id"
    assert "best_eligible_channel_score" in candidate.deterministic_backfill_rule

    model = first.model_definition
    assert "hierarchical-shrinkage" in model.implementation_version
    assert "empirical-bayes" not in model.implementation_version
    assert model.stage_one_name == "trigger_probability"
    assert model.stage_two_name == "triggered_cost_adjusted_net_excess"
    assert model.hierarchical_shrinkage_levels == (
        "global",
        "asset",
        "strategy",
        "strategy_x_market_regime",
    )
    assert model.minimum_position_lower_bound == 0
    assert model.minimum_position_comparator == "strictly_greater_than"
    assert model.missing_market_regime_policy == "fail_closed_ineligible"
    assert "ranking_only" in model.feature_effect_aggregation
    assert "realized_utility_rebalance_date_blocks" in model.posterior_interval

    utility = first.utility_definition
    assert utility.penalty_terms == (
        "not_triggered_benchmark_opportunity_cost",
        "turnover_cost",
        "liquidity_penalty",
        "tail_risk_penalty",
    )
    assert utility.optimization_target == (
        "portfolio_cost_adjusted_net_excess_after_frozen_constraints"
    )
    assert first.temporal_definition.rebalance_step_sessions == 10
    assert first.temporal_definition.candidate_lookback_days == 400
    assert utility.cash_utility == 0


def test_v41_protocol_and_registry_digests_remain_exactly_reproducible():
    protocol = build_ranking_v4_protocol(version="4.1")
    registry = build_ranking_v4_experiment_registry(version="4.1")

    assert protocol.protocol_digest == FROZEN_V41_PROTOCOL_DIGEST
    assert registry.registry_digest == FROZEN_V41_REGISTRY_DIGEST
    assert protocol.model_version.endswith("v4.1-preregistered")
    assert protocol.statistics_definition.pbo_block_count == 4
    assert protocol.statistics_definition.pbo_model_ids[1] == "ranking_v41_full"
    assert (
        protocol.utility_definition.replacement_cost_formula
        == "zero_for_incumbent_else_frozen_candidate_replacement_cost_pct_0.15"
    )
    assert ranking_v4_protocol_digest_is_valid(protocol)


def test_v4_allows_cash_and_freezes_portfolio_and_execution_constraints():
    protocol = build_ranking_v4_protocol()
    portfolio = protocol.portfolio_definition

    assert portfolio.minimum_positions == 0
    assert portfolio.maximum_positions == 5
    assert portfolio.cash_allowed is True
    assert portfolio.maximum_per_strategy == 2
    assert portfolio.maximum_per_industry == 2
    assert portfolio.maximum_shared_etf_underlying_ids == 0
    assert portfolio.maximum_shared_index_memberships == 0
    assert portfolio.maximum_per_theme == 2
    assert portfolio.maximum_per_factor == 3
    assert portfolio.maximum_pairwise_correlation == Decimal("0.8")
    assert portfolio.maximum_portfolio_beta == Decimal("1.2")
    assert portfolio.minimum_liquidity_score == Decimal("0.5")
    assert portfolio.minimum_capacity_score == Decimal("0.5")
    assert portfolio.fixed_incumbent_bonus == 0

    execution = protocol.execution_definition
    assert execution.signal_session == "D"
    assert execution.earliest_entry_session == "D+1"
    assert execution.settlement_rule == "T+1"
    assert execution.price_limit_policy == "enforced"
    assert execution.suspension_policy == "enforced"
    assert "fees" in execution.fee_policy
    assert "slippage" in execution.slippage_policy
    assert execution.same_day_ambiguous_path_policy == "adverse_path_first"


def test_v4_gates_are_not_weaker_than_rejected_v3_and_unknowns_fail_closed():
    protocol = build_ranking_v4_protocol()
    thresholds = protocol.thresholds

    assert thresholds.minimum_rebalance_dates >= 24
    assert thresholds.minimum_completed_trades >= 60
    assert thresholds.minimum_profit_factor >= Decimal("1.10")
    assert thresholds.minimum_positive_subperiods >= 4
    assert thresholds.required_subperiods >= 5
    assert thresholds.maximum_drawdown_floor_pct >= Decimal("-15")
    assert thresholds.maximum_holm_adjusted_p_value <= Decimal("0.05")
    assert thresholds.minimum_deflated_sharpe_probability >= Decimal("0.95")
    assert thresholds.maximum_probability_of_backtest_overfit <= Decimal("0.20")
    assert thresholds.minimum_valid_outcome_coverage_ratio >= Decimal("0.95")
    assert thresholds.minimum_confirmatory_forward_sessions >= 20
    assert thresholds.maximum_confirmatory_forward_sessions <= 50
    assert thresholds.minimum_confirmatory_forward_trades >= 10
    assert thresholds.benchmark_excess_comparator == "strictly_greater_than_zero"
    assert thresholds.stress_cost_adjusted_return_comparator == "strictly_greater_than_zero"
    assert thresholds.unknown_gate_policy == "fail_closed_not_passed"
    assert thresholds.aggregation_rule == "all_gates_must_pass"
    statistics = protocol.statistics_definition
    assert statistics.pbo_model_ids == (
        "constraint_matched_baseline",
        "ranking_v42_full",
        "channel_baseline",
        "channel_trend",
        "channel_breakout",
        "channel_quality_value",
        "channel_defensive_low_vol",
        "channel_etf_industry",
    )
    assert statistics.pbo_scope == ("frozen_eight_model_family_only_not_full_search_process")
    assert statistics.dependence_block_length == 3
    assert statistics.pbo_purge_rebalance_cohorts == 2
    assert statistics.pbo_block_count == 8
    assert "minimum_24_dates_per_half" in statistics.pbo_method
    assert statistics.pbo_date_coverage_threshold == Decimal("0.95")
    assert statistics.multiple_testing_method == ("holm_bonferroni_registered_family")
    assert tuple(item.model_id for item in statistics.registered_models) == (
        statistics.pbo_model_ids
    )
    assert statistics.unknown_trial_count_policy == "fail_closed_no_release"


@pytest.mark.parametrize(
    ("field", "weakened_value"),
    (
        ("minimum_rebalance_dates", 23),
        ("minimum_completed_trades", 59),
        ("minimum_profit_factor", Decimal("1.09")),
        ("minimum_positive_subperiods", 3),
        ("maximum_drawdown_floor_pct", Decimal("-15.01")),
        ("maximum_holm_adjusted_p_value", Decimal("0.051")),
        ("minimum_deflated_sharpe_probability", Decimal("0.949")),
        ("maximum_probability_of_backtest_overfit", Decimal("0.201")),
        ("minimum_valid_outcome_coverage_ratio", Decimal("0.949")),
        ("minimum_confirmatory_forward_sessions", 19),
        ("maximum_confirmatory_forward_sessions", 51),
        ("minimum_confirmatory_forward_trades", 9),
    ),
)
def test_v4_rejects_rehashed_attempts_to_weaken_any_gate(field, weakened_value):
    protocol = build_ranking_v4_protocol()
    weakened = protocol.thresholds.model_copy(update={field: weakened_value})
    forged = _rehashed_protocol(protocol, thresholds=weakened)

    assert not ranking_v4_protocol_digest_is_valid(forged)


def test_v4_development_evidence_is_exploratory_and_forward_is_post_freeze_only():
    protocol = build_ranking_v4_protocol()
    windows = {item.key: item for item in protocol.evidence_windows}

    development = windows["development"]
    assert development.start_date.isoformat() == "2021-11-01"
    assert development.end_date.isoformat() == "2025-12-31"
    assert development.evidence_label == "exploratory_development_evidence"
    assert development.eligible_for_release_gate is False

    forward = windows["confirmatory_forward"]
    assert forward.start_date is None
    assert forward.end_date is None
    assert forward.eligible_for_release_gate is True
    assert "signed_protocol" in forward.activation_rule
    assert protocol.confirmatory_definition.protocol_freeze_required is True
    assert protocol.confirmatory_definition.code_freeze_required is True
    assert (
        protocol.confirmatory_definition.historical_development_evidence_may_satisfy_forward_gate
        is False
    )
    assert protocol.confirmatory_definition.release_state_before_forward_pass == "shadow_only"


def test_v4_temporal_rules_forbid_future_data_and_revision_backfill():
    protocol = build_ranking_v4_protocol()
    temporal = protocol.temporal_definition

    assert temporal.decision_feature_rule == (
        "economic_available_at_or_before_decision_timestamp_from_pre_run_frozen_dataset"
    )
    assert temporal.training_outcome_rule == "outcome_matured_strictly_before_training_cutoff"
    assert temporal.financial_statement_rule == "as_published_and_known_on_decision_date"
    assert temporal.index_constituent_rule == "point_in_time_membership_on_decision_date"
    assert temporal.historical_ingestion_rule == (
        "development_reconstruction_may_be_ingested_later_but_uses_original_economic_dates"
    )
    assert temporal.revision_backfill_rule == (
        "dataset_revision_frozen_before_run_and_never_advanced_within_experiment"
    )
    assert temporal.future_market_data_rule == "forbidden"
    assert temporal.entry_wait_sessions == 5
    assert temporal.holding_sessions == 20
    assert temporal.purge_sessions == 25
    assert temporal.embargo_sessions == 25
    assert temporal.label_dependency_rebalance_cohorts == 3
    assert temporal.pbo_purge_rebalance_cohorts == 2
    assert protocol.market_regime_definition.missing_feature_policy == ("fail_closed_no_position")
    assert protocol.market_regime_definition.minimum_cross_section_count == 30
    assert (
        protocol.market_regime_definition.benchmark_slope_formula
        == "share_of_required_benchmarks_above_point_in_time_50_session_average"
    )
    assert (
        protocol.utility_definition.replacement_cost_formula
        == "proven_actual_replacement_cost_minus_stage2_embedded_cost_floor_zero;"
        "unproven_incremental_cost_zero"
    )


def test_v3_rejection_is_exactly_recorded_without_fabricated_statistics():
    summary = build_ranking_v3_rejected_summary()

    assert summary.experiment_id == "walk-forward-20260726164443-7fd44f0b"
    assert summary.source_revision == "dbd7fa0f6ec76990eca4de8325e14866dfbfe8e7"
    assert summary.dataset_revision == 8939
    assert summary.configured_snapshot_count == 102
    assert summary.completed_snapshot_count == 102
    assert summary.candidate_outcome_coverage_ratio == Decimal("0.986179")
    assert summary.historical_portfolio_benchmark_id == "CN:EQUAL_WEIGHT_ELIGIBLE"
    assert summary.historical_portfolio_benchmark_return_pct == Decimal("113.1521")
    assert summary.benchmark_excess_return_pct == Decimal("-107.3842")
    assert summary.official_paper_trade_count == 0
    assert summary.disposition == "rejected"
    assert summary.failed_gates == ("positive_benchmark_excess",)
    assert summary.confirmatory_holm_adjusted_p_value is None
    assert summary.deflated_sharpe_probability is None
    assert summary.probability_of_backtest_overfit is None
    assert summary.unknown_statistics_policy == "null_means_unobserved_never_zero_or_passed"


def test_v3_summary_and_v4_registry_have_stable_digests_and_detect_tampering():
    first = build_ranking_v4_experiment_registry()
    second = build_ranking_v4_experiment_registry()
    summary = first.predecessor_summaries[0]
    v4_summary = first.predecessor_summaries[1]

    assert first == second
    assert first.registry_digest == second.registry_digest
    assert first.registry_digest == FROZEN_V42_REGISTRY_DIGEST
    assert first.historical_trial_inventory_complete is False
    assert first.historical_trial_inventory_digest is None
    assert first.historical_trial_return_series_digests == ()
    assert summary.summary_digest == second.predecessor_summaries[0].summary_digest
    assert summary.summary_digest == FROZEN_V3_REJECTED_SUMMARY_DIGEST
    assert v4_summary == build_ranking_v4_rejected_summary()
    assert v4_summary.summary_digest == FROZEN_V4_REJECTED_SUMMARY_DIGEST
    assert v4_summary.completed_trade_count == 0
    assert v4_summary.probability_of_backtest_overfit == Decimal("0.833333")
    assert ranking_v4_experiment_registry_digest_is_valid(first)

    tampered_summary = summary.model_copy(update={"benchmark_excess_return_pct": Decimal("1")})
    tampered_registry = first.model_copy(
        update={"predecessor_summaries": (tampered_summary, v4_summary)}
    )
    assert not ranking_v4_experiment_registry_digest_is_valid(tampered_registry)

    with pytest.raises(
        RankingV4ExperimentRegistryError,
        match="cannot be rewritten|digest mismatch",
    ):
        build_ranking_v4_experiment_registry(predecessor_summaries=(tampered_summary, v4_summary))


def test_v4_protocol_digest_detects_tampering_even_when_payload_still_looks_valid():
    protocol = build_ranking_v4_protocol()
    changed = protocol.model_copy(
        update={
            "utility_definition": protocol.utility_definition.model_copy(
                update={"cash_utility": Decimal("0.01")}
            )
        }
    )

    assert not ranking_v4_protocol_digest_is_valid(changed)
    assert not ranking_v4_protocol_digest_is_valid(
        _rehashed_protocol(
            protocol,
            utility_definition=changed.utility_definition,
        )
    )


def test_v4_semantic_validation_rejects_rehashed_non_gate_protocol_changes():
    protocol = build_ranking_v4_protocol()
    changes = (
        {
            "candidate_definition": protocol.candidate_definition.model_copy(
                update={"deduplication_key": "ticker"}
            )
        },
        {
            "model_definition": protocol.model_definition.model_copy(
                update={"stage_two_population": "all_candidates"}
            )
        },
        {
            "market_regime_definition": protocol.market_regime_definition.model_copy(
                update={"unknown_regime_trading": "allowed"}
            )
        },
        {
            "portfolio_definition": protocol.portfolio_definition.model_copy(
                update={"maximum_pairwise_correlation": Decimal("0.81")}
            )
        },
        {
            "execution_definition": protocol.execution_definition.model_copy(
                update={"earliest_entry_session": "D"}
            )
        },
        {
            "temporal_definition": protocol.temporal_definition.model_copy(
                update={"revision_backfill_rule": "allowed"}
            )
        },
        {
            "statistics_definition": protocol.statistics_definition.model_copy(
                update={"pbo_scope": "selected_model_only"}
            )
        },
        {
            "confirmatory_definition": protocol.confirmatory_definition.model_copy(
                update={"code_freeze_required": False}
            )
        },
    )

    assert all(
        not ranking_v4_protocol_digest_is_valid(_rehashed_protocol(protocol, **change))
        for change in changes
    )
