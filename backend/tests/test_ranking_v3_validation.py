from datetime import date, timedelta
import random

import pytest

from qagent.backtesting.ranking_v3_validation import (
    RankingV3ReturnObservation,
    _iid_bootstrap_lower_bound,
    _iid_positive_sign_flip_p_value,
    evaluate_ranking_v3_validation,
    holm_bonferroni,
)
from qagent.backtesting.ranking_v3_pbo import (
    CSCV_PBO_METHOD,
    PBO_SCOPE_FROZEN_SIX_MODEL_FAMILY,
    PBO_SEARCH_PROCESS_COVERAGE,
    RANKING_V3_FROZEN_PBO_MODEL_IDS,
    RankingV3DatedModelReturn,
    evaluate_ranking_v3_cscv_pbo,
)


def _observations(
    values: list[float],
    *,
    rows_per_date: int = 1,
    start: date = date(2025, 1, 2),
) -> list[RankingV3ReturnObservation]:
    return [
        RankingV3ReturnObservation(
            rebalance_date=start + timedelta(days=index),
            net_return_pct=value,
        )
        for index, value in enumerate(values)
        for _ in range(rows_per_date)
    ]


def _evaluation(
    paired_excess: list[float],
    *,
    rows_per_date: int = 3,
    additional_p_values: list[float] | None = None,
    pbo_evidence: dict[str, object] | None = None,
    seed: int = 17,
):
    baseline = _observations(
        [0.0] * len(paired_excess),
        rows_per_date=rows_per_date,
    )
    challenger = _observations(
        paired_excess,
        rows_per_date=rows_per_date,
    )
    return evaluate_ranking_v3_validation(
        baseline,
        challenger,
        completed_trade_count=len(challenger),
        additional_hypothesis_p_values=additional_p_values or [],
        pbo_evidence=pbo_evidence,
        bootstrap_samples=1500,
        permutation_samples=5000,
        seed=seed,
    )


def _full_matrix_evidence(
    paired_excess: list[float],
    *,
    start: date = date(2025, 1, 2),
) -> dict[str, object]:
    dates = [start + timedelta(days=index) for index in range(len(paired_excess))]
    baseline = [0.0 for _ in paired_excess]
    series = {
        "constraint_matched_baseline": baseline,
        "ranking_v3_full": paired_excess,
        "static_balanced": [
            ((index % 5) - 2) * 0.08 for index in range(len(paired_excess))
        ],
        "trend_momentum": [
            ((index % 7) - 3) * 0.06 for index in range(len(paired_excess))
        ],
        "quality_value": [
            ((index % 4) - 1.5) * 0.07 for index in range(len(paired_excess))
        ],
        "defensive_liquidity": [
            0.04 if index % 2 else -0.04 for index in range(len(paired_excess))
        ],
    }
    matrix = {
        model_id: [
            RankingV3DatedModelReturn(
                rebalance_date=rebalance_date,
                net_return=value,
            )
            for rebalance_date, value in zip(dates, series[model_id], strict=True)
        ]
        for model_id in RANKING_V3_FROZEN_PBO_MODEL_IDS
    }
    evidence = evaluate_ranking_v3_cscv_pbo(
        matrix,
        block_count=6,
        purge_rebalance_cohorts=2,
    )
    evidence["model_return_matrix"] = {
        model_id: [
            {
                "rebalance_date": item.rebalance_date.isoformat(),
                "net_return": item.net_return,
            }
            for item in rows
        ]
        for model_id, rows in matrix.items()
    }
    return evidence


def test_clusters_multiple_rows_by_date_and_never_reports_official_pass():
    result = _evaluation([0.8 + (index % 5) * 0.05 for index in range(60)])

    assert result.baseline_row_count == 180
    assert result.challenger_row_count == 180
    assert result.common_rebalance_date_count == 60
    assert result.effective_independent_block_count == 20
    assert result.paired_mean_net_excess_pct == pytest.approx(0.9)
    assert result.statistical_gate_status == "insufficient"
    assert result.status == "insufficient"
    assert result.deployment_scope == "shadow_only"
    assert result.official_release_allowed is False
    assert result.pbo_status == "unavailable"
    assert result.pbo_probability is None
    assert "model-return matrix" in result.pbo_reason


def test_valid_low_pbo_evidence_completes_historical_validation_gate():
    result = _evaluation(
        [0.8 + (index % 5) * 0.05 for index in range(60)],
        pbo_evidence={
            "probability": 0.10,
            "matrix_digest": "a" * 64,
            "fold_count": 20,
            "model_count": 6,
            "date_count": 60,
            "method": CSCV_PBO_METHOD,
            "scope": PBO_SCOPE_FROZEN_SIX_MODEL_FAMILY,
            "search_process_coverage": PBO_SEARCH_PROCESS_COVERAGE,
            "rejection_reason": None,
        },
    )

    assert result.statistical_gate_status == "insufficient"
    assert result.pbo_status == "pass"
    assert result.pbo_probability == 0.10
    assert result.status == "insufficient"
    assert result.official_release_allowed is False
    assert "not a full search-process PBO" in result.pbo_reason


def test_high_pbo_evidence_fails_historical_validation():
    result = _evaluation(
        [0.8 + (index % 5) * 0.05 for index in range(60)],
        pbo_evidence={
            "probability": 0.35,
            "matrix_digest": "b" * 64,
            "fold_count": 20,
            "model_count": 6,
            "date_count": 60,
            "method": CSCV_PBO_METHOD,
            "scope": PBO_SCOPE_FROZEN_SIX_MODEL_FAMILY,
            "search_process_coverage": PBO_SEARCH_PROCESS_COVERAGE,
            "rejection_reason": None,
        },
    )

    assert result.statistical_gate_status == "insufficient"
    assert result.pbo_status == "fail"
    assert result.status == "fail"


def test_pbo_rejects_full_search_claim_or_undisclosed_model_family():
    result = _evaluation(
        [0.8 + (index % 5) * 0.05 for index in range(60)],
        pbo_evidence={
            "probability": 0.10,
            "matrix_digest": "c" * 64,
            "fold_count": 20,
            "model_count": 6,
            "date_count": 60,
            "method": CSCV_PBO_METHOD,
            "scope": "full_search_process",
            "search_process_coverage": "complete",
            "rejection_reason": None,
        },
    )

    assert result.pbo_status == "unavailable"
    assert result.pbo_probability is None
    assert "frozen six-model family" in result.pbo_reason


def test_same_date_rows_are_averaged_before_paired_inference():
    start = date(2025, 1, 2)
    baseline = [
        RankingV3ReturnObservation(
            rebalance_date=start + timedelta(days=index),
            net_return_pct=value,
        )
        for index in range(24)
        for value in (0.0, 2.0)
    ]
    challenger = [
        RankingV3ReturnObservation(
            rebalance_date=start + timedelta(days=index),
            net_return_pct=value,
        )
        for index in range(24)
        for value in (2.0, 4.0)
    ]

    result = evaluate_ranking_v3_validation(
        baseline,
        challenger,
        completed_trade_count=60,
        bootstrap_samples=300,
        permutation_samples=1000,
    )

    assert result.common_rebalance_date_count == 24
    assert result.paired_mean_net_excess_pct == pytest.approx(2.0)


def test_rejects_non_common_rebalance_dates_instead_of_using_intersection():
    baseline = _observations([0.0] * 24, rows_per_date=3)
    challenger = _observations(
        [1.0] * 24,
        rows_per_date=3,
        start=date(2025, 1, 3),
    )

    result = evaluate_ranking_v3_validation(
        baseline,
        challenger,
        completed_trade_count=72,
        bootstrap_samples=200,
        permutation_samples=500,
    )

    assert result.dates_are_common is False
    assert result.status == "fail"
    assert result.statistical_gate_status == "fail"
    assert result.paired_mean_net_excess_pct is None
    assert result.baseline_only_dates
    assert result.challenger_only_dates
    common_gate = next(gate for gate in result.gates if gate.key == "common_rebalance_calendar")
    assert common_gate.status == "fail"
    assert result.deployment_scope == "shadow_only"
    assert result.official_release_allowed is False
    assert result.pbo_status == "unavailable"


def test_distribution_concentration_cannot_inflate_independent_sample_count():
    result = _evaluation([1.0] * 5, rows_per_date=20)

    assert result.completed_trade_count == 100
    assert result.common_rebalance_date_count == 5
    assert result.statistical_gate_status != "pass"
    assert result.status != "pass"
    date_gate = next(gate for gate in result.gates if gate.key == "independent_rebalance_dates")
    assert date_gate.status == "insufficient"


def test_fewer_than_sixty_completed_trades_is_insufficient():
    result = _evaluation([1.0] * 24, rows_per_date=2)

    assert result.completed_trade_count == 48
    assert result.statistical_gate_status != "pass"
    trade_gate = next(gate for gate in result.gates if gate.key == "completed_trades")
    assert trade_gate.status == "insufficient"


def test_24_overlapping_dates_are_only_8_effective_blocks():
    result = evaluate_ranking_v3_validation(
        _observations([0.0] * 24),
        _observations([0.7 + (index % 3) * 0.1 for index in range(24)]),
        completed_trade_count=60,
        bootstrap_samples=500,
        permutation_samples=1000,
        seed=17,
    )

    date_gate = next(gate for gate in result.gates if gate.key == "independent_rebalance_dates")
    trade_gate = next(gate for gate in result.gates if gate.key == "completed_trades")
    assert result.dependence_block_length == 3
    assert result.effective_independent_block_count == 8
    assert date_gate.status == "insufficient"
    assert trade_gate.status == "pass"


def test_exact_72_date_and_60_trade_boundaries_pass_effective_sample_gates():
    result = evaluate_ranking_v3_validation(
        _observations([0.0] * 72),
        _observations([0.7 + (index % 3) * 0.1 for index in range(72)]),
        completed_trade_count=60,
        bootstrap_samples=500,
        permutation_samples=1000,
        seed=17,
    )

    date_gate = next(gate for gate in result.gates if gate.key == "independent_rebalance_dates")
    trade_gate = next(gate for gate in result.gates if gate.key == "completed_trades")
    assert result.effective_independent_block_count == 24
    assert date_gate.status == "pass"
    assert date_gate.observed == "24 effective blocks (72 rebalance dates)"
    assert trade_gate.status == "pass"


def test_71_date_and_59_trade_boundaries_are_insufficient():
    result = evaluate_ranking_v3_validation(
        _observations([0.0] * 71),
        _observations([1.0] * 71),
        completed_trade_count=59,
        bootstrap_samples=300,
        permutation_samples=500,
    )

    assert result.statistical_gate_status == "insufficient"
    assert {gate.key for gate in result.gates if gate.status == "insufficient"} >= {
        "independent_rebalance_dates",
        "completed_trades",
    }


def test_positive_mean_with_bootstrap_interval_crossing_zero_fails():
    values: list[float] = []
    for block in range(15):
        values.extend([3.2, 3.2] if block % 2 == 0 else [-3.0, -3.0])

    result = _evaluation(values)

    assert result.paired_mean_net_excess_pct is not None
    assert result.paired_mean_net_excess_pct > 0
    assert result.bootstrap_one_sided_95_lower_bound_pct is not None
    assert result.bootstrap_one_sided_95_lower_bound_pct <= 0
    assert result.statistical_gate_status == "fail"


def test_non_positive_paired_mean_is_rejected():
    result = _evaluation([-0.1] * 30, rows_per_date=2)

    assert result.paired_mean_net_excess_pct == pytest.approx(-0.1)
    assert result.statistical_gate_status == "fail"
    mean_gate = next(gate for gate in result.gates if gate.key == "positive_paired_mean")
    assert mean_gate.status == "fail"


def test_holm_can_reject_an_unadjusted_p_value_that_passes():
    adjusted = holm_bonferroni([0.04, 0.2, 0.3])

    assert 0.04 <= 0.05
    assert adjusted[0] == pytest.approx(0.12)
    assert adjusted[0] > 0.05


def test_validation_applies_holm_family_adjustment():
    centered = [(-2.3 + index * 0.1) for index in range(48)]
    values = [value + 0.5 for value in centered]
    result = _evaluation(
        values,
        additional_p_values=[0.2] * 15,
        seed=91,
    )

    assert result.positive_edge_p_value is not None
    assert result.positive_edge_p_value < 0.10
    assert result.holm_adjusted_positive_edge_p_value is not None
    assert result.holm_adjusted_positive_edge_p_value > result.positive_edge_p_value
    assert result.holm_adjusted_positive_edge_p_value > 0.05
    holm_gate = next(gate for gate in result.gates if gate.key == "holm_adjusted_positive_edge")
    assert holm_gate.status == "fail"
    assert result.holm_family_size == 16
    assert result.holm_observed_prior_p_value_count == 15
    assert result.holm_unobserved_prior_p_value_count == 0
    assert result.holm_adjustment_method == "exact_holm_bonferroni"
    assert result.holm_adjusted_positive_edge_p_value == pytest.approx(
        holm_bonferroni([result.positive_edge_p_value, *([0.2] * 15)])[0]
    )


def test_unmeasured_historical_explorations_reserve_all_holm_family_slots():
    centered = [(-2.3 + index * 0.1) for index in range(48)]
    values = [value + 0.5 for value in centered]

    result = _evaluation(values, seed=91)

    assert result.positive_edge_p_value is not None
    assert result.holm_family_size == 16
    assert result.holm_observed_prior_p_value_count == 0
    assert result.holm_unobserved_prior_p_value_count == 15
    assert result.holm_adjustment_method == "conservative_bonferroni_unknown_prior_p_values"
    assert result.holm_adjusted_positive_edge_p_value == pytest.approx(
        min(1.0, 16 * result.positive_edge_p_value)
    )
    assert "15 of 15 registered prior hypotheses" in result.holm_adjustment_reason


def test_partial_holm_evidence_remains_fail_closed_until_family_is_complete():
    centered = [(-2.3 + index * 0.1) for index in range(48)]
    values = [value + 0.5 for value in centered]
    result = _evaluation(
        values,
        additional_p_values=[0.2, 0.3],
        seed=91,
    )

    assert result.holm_family_size == 16
    assert result.holm_observed_prior_p_value_count == 2
    assert result.holm_unobserved_prior_p_value_count == 13
    assert result.holm_adjustment_method == "conservative_bonferroni_unknown_prior_p_values"
    assert result.holm_adjusted_positive_edge_p_value == pytest.approx(
        min(1.0, 16 * result.positive_edge_p_value)
    )


def test_observed_prior_p_values_cannot_exceed_registered_prior_count():
    with pytest.raises(
        ValueError,
        match="cannot exceed prior_experiment_count",
    ):
        evaluate_ranking_v3_validation(
            _observations([0.0] * 24),
            _observations([1.0] * 24),
            prior_experiment_count=1,
            additional_hypothesis_p_values=[0.1, 0.2],
            bootstrap_samples=100,
            permutation_samples=100,
        )


def test_five_contiguous_subperiod_gate_requires_four_positive_periods():
    values = [1.0] * 6 + [-1.0] * 12 + [1.0] * 12

    result = _evaluation(values)

    assert result.subperiod_count == 5
    assert result.positive_subperiod_count == 3
    assert result.statistical_gate_status == "fail"
    subperiod_gate = next(
        gate for gate in result.gates if gate.key == "positive_contiguous_subperiods"
    )
    assert subperiod_gate.status == "fail"


def test_trial_count_cannot_fabricate_deflated_sharpe_without_model_matrix():
    values = [0.05 + ((index % 7) - 3) * 0.1 for index in range(30)]
    few_trials = evaluate_ranking_v3_validation(
        _observations([0.0] * 30, rows_per_date=2),
        _observations(values, rows_per_date=2),
        completed_trade_count=60,
        prior_experiment_count=0,
        bootstrap_samples=500,
        permutation_samples=1000,
        seed=3,
    )
    many_trials = evaluate_ranking_v3_validation(
        _observations([0.0] * 30, rows_per_date=2),
        _observations(values, rows_per_date=2),
        completed_trade_count=60,
        prior_experiment_count=99,
        bootstrap_samples=500,
        permutation_samples=1000,
        seed=3,
    )

    assert few_trials.deflated_sharpe_status == "unavailable"
    assert many_trials.deflated_sharpe_status == "unavailable"
    assert few_trials.deflated_sharpe_probability is None
    assert many_trials.deflated_sharpe_probability is None
    assert "model-return matrix was not provided" in many_trials.deflated_sharpe_reason
    dsr_gate = next(gate for gate in many_trials.gates if gate.key == "deflated_sharpe_probability")
    assert dsr_gate.status == "insufficient"
    assert dsr_gate.reason == many_trials.deflated_sharpe_reason


def test_deflated_sharpe_uses_frozen_matrix_and_full_trial_penalty():
    values = [1.2 + ((index % 7) - 3) * 0.04 for index in range(60)]
    result = _evaluation(
        values,
        pbo_evidence=_full_matrix_evidence(values),
    )

    assert result.deflated_sharpe_probability is not None
    assert result.deflated_sharpe_status in {"pass", "fail"}
    assert "20 independent blocks" in result.deflated_sharpe_reason
    assert "16 registered trials" in result.deflated_sharpe_reason
    gate = next(
        item
        for item in result.gates
        if item.key == "deflated_sharpe_probability"
    )
    assert gate.status == result.deflated_sharpe_status


def test_deflated_sharpe_rejects_matrix_calendar_mismatch():
    values = [0.8 + ((index % 5) - 2) * 0.05 for index in range(60)]
    evidence = _full_matrix_evidence(
        values,
        start=date(2025, 1, 3),
    )

    result = _evaluation(values, pbo_evidence=evidence)

    assert result.deflated_sharpe_status == "unavailable"
    assert result.deflated_sharpe_probability is None
    assert "does not exactly match" in result.deflated_sharpe_reason


def test_zero_variance_dsr_is_unavailable_instead_of_bypassing_trial_penalty():
    result = evaluate_ranking_v3_validation(
        _observations([0.0] * 30, rows_per_date=2),
        _observations([1.0] * 30, rows_per_date=2),
        completed_trade_count=60,
        prior_experiment_count=999,
        bootstrap_samples=300,
        permutation_samples=1000,
    )

    assert result.deflated_sharpe_probability is None
    dsr_gate = next(gate for gate in result.gates if gate.key == "deflated_sharpe_probability")
    assert dsr_gate.status == "insufficient"
    assert result.status == "fail"
    holm_gate = next(gate for gate in result.gates if gate.key == "holm_adjusted_positive_edge")
    assert holm_gate.status == "fail"
    assert result.holm_family_size == 1000


def test_small_overlapping_sample_cannot_produce_dsr():
    result = evaluate_ranking_v3_validation(
        _observations([0.0] * 5),
        _observations([0.4, 0.7, 0.1, 0.8, 0.2]),
        completed_trade_count=60,
        bootstrap_samples=300,
        permutation_samples=1000,
    )

    assert result.common_rebalance_date_count == 5
    assert result.effective_independent_block_count == 1
    assert result.deflated_sharpe_probability is None
    dsr_gate = next(gate for gate in result.gates if gate.key == "deflated_sharpe_probability")
    assert dsr_gate.status == "insufficient"


def test_block_statistics_are_deterministic_for_overlapping_returns():
    values = [1.4 if (index // 2) % 3 else -0.9 for index in range(60)]
    first = _evaluation(values, rows_per_date=2, seed=73)
    second = _evaluation(values, rows_per_date=2, seed=73)

    assert first == second
    assert first.effective_independent_block_count == 20
    assert first.bootstrap_one_sided_95_lower_bound_pct is not None
    assert first.positive_edge_p_value is not None


def test_alternating_overlap_does_not_inflate_effective_sample_gate():
    values = [1.0 if index % 2 == 0 else -0.2 for index in range(30)]
    result = _evaluation(values, rows_per_date=3)

    assert result.common_rebalance_date_count == 30
    assert result.effective_independent_block_count == 10
    date_gate = next(gate for gate in result.gates if gate.key == "independent_rebalance_dates")
    assert date_gate.status == "insufficient"
    assert "10 independent time blocks" in date_gate.reason


def test_block_inference_cannot_be_less_conservative_than_iid():
    values = [1.4 if (index // 2) % 3 else -0.9 for index in range(60)]
    result = _evaluation(values, rows_per_date=2, seed=73)
    iid_lower = _iid_bootstrap_lower_bound(
        values,
        samples=1500,
        seed=73,
    )
    iid_p_value = _iid_positive_sign_flip_p_value(
        values,
        samples=5000,
        seed=74,
    )

    assert iid_lower is not None
    assert iid_p_value is not None
    assert result.bootstrap_one_sided_95_lower_bound_pct is not None
    assert result.positive_edge_p_value is not None
    assert result.bootstrap_one_sided_95_lower_bound_pct <= iid_lower
    assert result.positive_edge_p_value >= iid_p_value


def test_empty_inputs_are_rejected_without_fabricating_statistics():
    result = evaluate_ranking_v3_validation(
        [],
        [],
        completed_trade_count=0,
        bootstrap_samples=100,
        permutation_samples=100,
    )

    assert result.status == "fail"
    assert result.dates_are_common is False
    assert result.paired_mean_net_excess_pct is None
    assert result.deflated_sharpe_probability is None
    assert result.deployment_scope == "shadow_only"


def test_validation_is_deterministic_and_input_order_independent():
    baseline = _observations([0.0] * 30, rows_per_date=3)
    challenger = _observations(
        [0.7 + (index % 4) * 0.1 for index in range(30)],
        rows_per_date=3,
    )
    shuffled_baseline = list(baseline)
    shuffled_challenger = list(challenger)
    random.Random(99).shuffle(shuffled_baseline)
    random.Random(101).shuffle(shuffled_challenger)

    first = evaluate_ranking_v3_validation(
        baseline,
        challenger,
        completed_trade_count=90,
        bootstrap_samples=1000,
        permutation_samples=3000,
        seed=13,
    )
    second = evaluate_ranking_v3_validation(
        shuffled_baseline,
        shuffled_challenger,
        completed_trade_count=90,
        bootstrap_samples=1000,
        permutation_samples=3000,
        seed=13,
    )

    assert first == second
