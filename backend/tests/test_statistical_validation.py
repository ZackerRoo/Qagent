from datetime import date, timedelta

import pytest

from qagent.backtesting.statistical_validation import (
    benjamini_hochberg,
    clustered_return_inference,
)


def _observations(values: list[float], *, trades_per_date: int = 3):
    start = date(2024, 1, 1)
    return [
        (start + timedelta(days=index), value)
        for index, value in enumerate(values)
        for _ in range(trades_per_date)
    ]


def test_clustered_inference_detects_repeatable_positive_edge():
    observations = _observations(
        [1.2, 0.8, 1.6, 0.4, 1.1, 0.7, 1.5, 0.6, 1.3, 0.9, 1.4, 0.5]
    )

    first = clustered_return_inference(
        observations,
        bootstrap_samples=500,
        permutation_samples=1000,
        seed=7,
    )
    second = clustered_return_inference(
        observations,
        bootstrap_samples=500,
        permutation_samples=1000,
        seed=7,
    )

    assert first.sample_count == 36
    assert first.cluster_count == 12
    assert first.confidence_low_pct is not None
    assert first.confidence_low_pct > 0
    assert first.positive_edge_p_value is not None
    assert first.positive_edge_p_value <= 0.05
    assert first.verdict == "positive"
    assert first == second


def test_clustered_inference_does_not_treat_same_day_trades_as_independent():
    observations = _observations([1.5, 1.0, 0.5, -0.5, -1.0], trades_per_date=20)

    result = clustered_return_inference(
        observations,
        bootstrap_samples=300,
        permutation_samples=500,
        seed=11,
    )

    assert result.sample_count == 100
    assert result.cluster_count == 5
    assert result.verdict == "insufficient"


def test_clustered_inference_detects_repeatable_negative_edge():
    result = clustered_return_inference(
        _observations(
            [-1.2, -0.8, -1.6, -0.4, -1.1, -0.7, -1.5, -0.6, -1.3, -0.9, -1.4, -0.5]
        ),
        bootstrap_samples=500,
        permutation_samples=1000,
        seed=13,
    )

    assert result.confidence_high_pct is not None
    assert result.confidence_high_pct < 0
    assert result.negative_edge_p_value is not None
    assert result.negative_edge_p_value <= 0.05
    assert result.verdict == "negative"


def test_benjamini_hochberg_preserves_order_and_monotonic_adjustment():
    adjusted = benjamini_hochberg([0.01, 0.04, None, 0.03, 0.5])

    assert adjusted[0] == pytest.approx(0.04)
    assert adjusted[1] == pytest.approx(0.053333, abs=1e-6)
    assert adjusted[2] is None
    assert adjusted[3] == pytest.approx(0.053333, abs=1e-6)
    assert adjusted[4] == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"bootstrap_samples": 0}, "bootstrap_samples"),
        ({"permutation_samples": 0}, "permutation_samples"),
        ({"confidence": 1.0}, "confidence"),
    ],
)
def test_clustered_inference_rejects_invalid_configuration(kwargs, message):
    with pytest.raises(ValueError, match=message):
        clustered_return_inference(_observations([1.0, -1.0]), **kwargs)
