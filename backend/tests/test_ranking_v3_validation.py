from datetime import date, timedelta
import random

import pytest

from qagent.backtesting.ranking_v3_validation import (
    RankingV3ReturnObservation,
    evaluate_ranking_v3_validation,
    holm_bonferroni,
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
        bootstrap_samples=1500,
        permutation_samples=5000,
        seed=seed,
    )


def test_clusters_multiple_rows_by_date_and_never_reports_official_pass():
    result = _evaluation([0.8 + (index % 5) * 0.05 for index in range(30)])

    assert result.baseline_row_count == 90
    assert result.challenger_row_count == 90
    assert result.common_rebalance_date_count == 30
    assert result.paired_mean_net_excess_pct == pytest.approx(0.9)
    assert result.statistical_gate_status == "pass"
    assert result.status == "insufficient"
    assert result.deployment_scope == "shadow_only"
    assert result.official_release_allowed is False
    assert result.pbo_status == "unavailable"
    assert result.pbo_probability is None
    assert "model-return matrix" in result.pbo_reason


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
    common_gate = next(
        gate for gate in result.gates if gate.key == "common_rebalance_calendar"
    )
    assert common_gate.status == "fail"
    assert result.deployment_scope == "shadow_only"
    assert result.official_release_allowed is False
    assert result.pbo_status == "unavailable"


def test_distribution_concentration_cannot_inflate_independent_sample_count():
    result = _evaluation([1.0] * 5, rows_per_date=20)

    assert result.completed_trade_count == 100
    assert result.common_rebalance_date_count == 5
    assert result.statistical_gate_status == "insufficient"
    assert result.status == "insufficient"
    date_gate = next(
        gate for gate in result.gates if gate.key == "independent_rebalance_dates"
    )
    assert date_gate.status == "insufficient"


def test_fewer_than_sixty_completed_trades_is_insufficient():
    result = _evaluation([1.0] * 24, rows_per_date=2)

    assert result.completed_trade_count == 48
    assert result.statistical_gate_status == "insufficient"
    trade_gate = next(
        gate for gate in result.gates if gate.key == "completed_trades"
    )
    assert trade_gate.status == "insufficient"


def test_exact_24_date_and_60_trade_boundaries_pass_sample_gates():
    result = evaluate_ranking_v3_validation(
        _observations([0.0] * 24),
        _observations([0.7 + (index % 3) * 0.1 for index in range(24)]),
        completed_trade_count=60,
        bootstrap_samples=500,
        permutation_samples=1000,
        seed=17,
    )

    date_gate = next(
        gate for gate in result.gates if gate.key == "independent_rebalance_dates"
    )
    trade_gate = next(
        gate for gate in result.gates if gate.key == "completed_trades"
    )
    assert date_gate.status == "pass"
    assert trade_gate.status == "pass"


def test_23_date_and_59_trade_boundaries_are_insufficient():
    result = evaluate_ranking_v3_validation(
        _observations([0.0] * 23),
        _observations([1.0] * 23),
        completed_trade_count=59,
        bootstrap_samples=300,
        permutation_samples=500,
    )

    assert result.statistical_gate_status == "insufficient"
    assert {
        gate.key
        for gate in result.gates
        if gate.status == "insufficient"
    } >= {"independent_rebalance_dates", "completed_trades"}


def test_positive_mean_with_bootstrap_interval_crossing_zero_fails():
    values = [
        -3.0,
        3.1,
        -2.8,
        2.9,
        -2.6,
        2.7,
        -2.4,
        2.5,
        -2.2,
        2.3,
        -2.0,
        2.1,
        -1.8,
        1.9,
        -1.6,
        1.7,
        -1.4,
        1.5,
        -1.2,
        1.3,
        -1.0,
        1.1,
        -0.8,
        0.9,
        0.1,
        0.1,
        0.1,
        0.1,
        0.1,
        0.1,
    ]

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
    mean_gate = next(
        gate for gate in result.gates if gate.key == "positive_paired_mean"
    )
    assert mean_gate.status == "fail"


def test_holm_can_reject_an_unadjusted_p_value_that_passes():
    adjusted = holm_bonferroni([0.04, 0.2, 0.3])

    assert 0.04 <= 0.05
    assert adjusted[0] == pytest.approx(0.12)
    assert adjusted[0] > 0.05


def test_validation_applies_holm_family_adjustment():
    centered = [(-2.3 + index * 0.2) for index in range(24)]
    values = [value + 0.5 for value in centered]
    result = _evaluation(
        values,
        additional_p_values=[0.2] * 9,
        seed=91,
    )

    assert result.positive_edge_p_value is not None
    assert result.positive_edge_p_value <= 0.05
    assert result.holm_adjusted_positive_edge_p_value is not None
    assert (
        result.holm_adjusted_positive_edge_p_value
        > result.positive_edge_p_value
    )
    assert result.holm_adjusted_positive_edge_p_value > 0.05
    holm_gate = next(
        gate for gate in result.gates if gate.key == "holm_adjusted_positive_edge"
    )
    assert holm_gate.status == "fail"
    assert result.holm_family_size == 10


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


def test_prior_experiment_count_deflates_sharpe_probability():
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

    assert few_trials.deflated_sharpe_probability is not None
    assert many_trials.deflated_sharpe_probability is not None
    assert (
        many_trials.deflated_sharpe_probability
        < few_trials.deflated_sharpe_probability
    )
    dsr_gate = next(
        gate for gate in many_trials.gates if gate.key == "deflated_sharpe_probability"
    )
    assert dsr_gate.status == "fail"


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
    dsr_gate = next(
        gate for gate in result.gates if gate.key == "deflated_sharpe_probability"
    )
    assert dsr_gate.status == "insufficient"
    assert result.status == "insufficient"


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
