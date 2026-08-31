from qagent.factors.research_contract import FEATURE_COLUMNS
import pytest

from qagent.research.factor_candidate_queue import (
    build_factor_candidate_queue,
    get_explicit_shadow_candidate,
)


def test_candidate_queue_enumerates_supported_families_without_execution_effects():
    queue = build_factor_candidate_queue()
    by_family = {candidate.family: candidate for candidate in queue.candidates}

    assert set(by_family) == {
        "quality",
        "profitability_improvement",
        "capital_strength",
        "trend_health",
        "valuation_growth_match",
        "catalyst",
    }
    assert len(queue.contract_available_candidates()) == 5
    assert by_family["catalyst"].state == "future_capability"
    assert by_family["catalyst"].missing_features == ("point_in_time_catalyst_event",)
    assert "experiment blocked" in by_family["catalyst"].availability_label
    assert "net flow" in by_family["capital_strength"].limitation
    assert "latest-only PEG" in by_family["valuation_growth_match"].limitation

    assert queue.scope == "research_shadow"
    assert queue.decision_weight is False
    assert queue.production_ranking_effect == "none"
    assert queue.paper_order_effect == "none"
    assert all(candidate.decision_weight is False for candidate in queue.candidates)
    assert all(candidate.production_ranking_effect == "none" for candidate in queue.candidates)
    assert all(candidate.paper_order_effect == "none" for candidate in queue.candidates)
    assert all(candidate.data_coverage_status == "unverified" for candidate in queue.candidates)
    assert all(candidate.experiment_start_allowed is False for candidate in queue.candidates)
    assert queue.data_health["factor_candidate_queue_paper_isolation"] == "true"
    assert queue.data_health["factor_candidate_queue_data_coverage_status"] == "unverified"
    assert queue.data_health["factor_candidate_queue_experiment_start_allowed"] == "false"


def test_candidate_queue_fails_closed_when_a_required_feature_is_not_available():
    available = set(FEATURE_COLUMNS) - {"gross_margin", "volume_ratio_5_20"}
    queue = build_factor_candidate_queue(available)
    by_family = {candidate.family: candidate for candidate in queue.candidates}

    assert by_family["quality"].state == "unavailable"
    assert by_family["quality"].missing_features == ("gross_margin",)
    assert by_family["capital_strength"].state == "unavailable"
    assert by_family["capital_strength"].missing_features == ("volume_ratio_5_20",)
    assert by_family["trend_health"].state == "contract_available_for_shadow_design"
    assert queue.data_health["factor_candidate_queue_contract_available"] == "3"
    assert queue.data_health["factor_candidate_queue_unavailable"] == "2"
    assert queue.data_health["factor_candidate_queue_future_capability"] == "1"


def test_candidate_queue_preregisters_existing_shadow_evidence_thresholds():
    queue = build_factor_candidate_queue()

    for candidate in queue.candidates:
        policy = candidate.evidence_policy
        assert policy.required_horizons == (5, 10, 20)
        assert policy.minimum_matured_runs_per_horizon == 20
        assert policy.minimum_outcome_coverage == 0.95
        assert policy.minimum_session_edge_rate == 0.55
        assert policy.median_session_net_excess_must_be_positive is True
        assert policy.challenger_rank_ic_must_exceed_baseline is True
        assert policy.minimum_selection_lift_rate == 0.55
        assert policy.median_selection_lift_must_be_positive is True
        assert policy.minimum_execution_head_paired_sessions == 20
        assert policy.execution_head_all_matured_sessions_must_be_filled is True
        assert policy.execution_head_max_industry_positions == 3
        assert policy.minimum_execution_head_lift_rate == 0.55
        assert policy.median_execution_head_lift_must_be_positive is True
        assert policy.gate_policy_completeness == "partial"
        assert policy.promotion_effect == "manual_review_only"


def test_future_capability_cannot_be_enabled_by_claiming_an_unknown_feature():
    queue = build_factor_candidate_queue(
        set(FEATURE_COLUMNS) | {"point_in_time_catalyst_event"}
    )
    catalyst = next(candidate for candidate in queue.candidates if candidate.family == "catalyst")

    assert catalyst.state == "future_capability"
    assert catalyst.available_features == ()
    assert catalyst.missing_features == ("point_in_time_catalyst_event",)
    assert catalyst.decision_weight is False


def test_explicit_shadow_lookup_allows_only_first_wave_candidates():
    trend = get_explicit_shadow_candidate("trend-health-composite-v1")
    turnover = get_explicit_shadow_candidate("turnover-volume-strength-v1")

    assert trend.required_features == (
        "momentum_20",
        "trend_slope_60",
        "trend_r2_60",
        "downside_risk_60",
        "max_drawdown_60",
    )
    assert turnover.required_features == ("turnover_log_20", "volume_ratio_5_20")
    with pytest.raises(ValueError, match="not approved"):
        get_explicit_shadow_candidate("point-in-time-catalyst-v1")
    with pytest.raises(ValueError, match="unknown"):
        get_explicit_shadow_candidate("unknown-candidate")
