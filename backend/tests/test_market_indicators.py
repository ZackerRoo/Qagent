from dataclasses import FrozenInstanceError
from math import exp, expm1

import numpy as np
import pandas as pd
import pytest

from qagent.market.indicators import regression_quality_momentum, wilder_atr


def test_regression_quality_momentum_recovers_exponential_growth() -> None:
    daily_log_return = 0.01
    close = pd.Series([100 * exp(daily_log_return * day) for day in range(29)])

    result = regression_quality_momentum(close)

    assert result.status == "available"
    assert result.reason is None
    assert result.sample_size == 29
    assert result.annualized_return == pytest.approx(expm1(daily_log_return * 252))
    assert result.r_squared == pytest.approx(1.0)
    assert result.quality_score == pytest.approx(result.annualized_return)


def test_regression_quality_momentum_noise_reduces_r_squared() -> None:
    time = np.arange(29, dtype=float)
    smooth = pd.Series(100 * np.exp(0.01 * time))
    noisy = smooth * pd.Series(1 + 0.08 * np.sin(time * 1.7))

    smooth_result = regression_quality_momentum(smooth)
    noisy_result = regression_quality_momentum(noisy)

    assert smooth_result.r_squared == pytest.approx(1.0)
    assert noisy_result.status == "available"
    assert noisy_result.r_squared is not None
    assert noisy_result.r_squared < smooth_result.r_squared


@pytest.mark.parametrize(
    ("close", "expected_reason", "expected_size"),
    [
        (pd.Series([100.0] * 28), "insufficient_samples", 28),
        (pd.Series([100.0] * 28 + [0.0]), "non_positive_close", 29),
        (pd.Series([100.0] * 28 + [-1.0]), "non_positive_close", 29),
        (pd.Series([100.0] * 28 + [np.nan]), "non_finite_close", 29),
        (pd.Series([100.0] * 28 + [np.inf]), "non_finite_close", 29),
    ],
)
def test_regression_quality_momentum_returns_explicit_unavailable_result(
    close: pd.Series,
    expected_reason: str,
    expected_size: int,
) -> None:
    result = regression_quality_momentum(close)

    assert result.status == "unavailable"
    assert result.reason == expected_reason
    assert result.sample_size == expected_size
    assert result.annualized_return is None
    assert result.r_squared is None
    assert result.quality_score is None


def test_regression_quality_momentum_result_is_immutable() -> None:
    result = regression_quality_momentum(pd.Series([100.0] * 29))

    with pytest.raises(FrozenInstanceError):
        result.status = "unavailable"  # type: ignore[misc]


def test_wilder_atr_matches_known_values() -> None:
    frame = pd.DataFrame(
        {
            "high": [10.0, 12.0, 13.0, 15.0, 14.0],
            "low": [8.0, 9.0, 11.0, 12.0, 11.0],
            "close": [9.0, 11.0, 12.0, 13.0, 12.0],
        }
    )

    result = wilder_atr(frame, period=3)

    expected = pd.Series(
        [np.nan, np.nan, 7 / 3, 23 / 9, 73 / 27],
        name="atr",
    )
    pd.testing.assert_series_equal(result, expected)


def test_wilder_atr_keeps_values_nan_until_period_is_available() -> None:
    frame = pd.DataFrame(
        {
            "high": [11.0, 12.0, 13.0],
            "low": [9.0, 10.0, 11.0],
            "close": [10.0, 11.0, 12.0],
        },
        index=[3, 5, 8],
    )

    result = wilder_atr(frame, period=4)

    assert result.index.tolist() == [3, 5, 8]
    assert result.isna().all()


@pytest.mark.parametrize("period", [0, -1, 1.5, True])
def test_wilder_atr_rejects_invalid_period(period: object) -> None:
    frame = pd.DataFrame({"high": [2.0], "low": [1.0], "close": [1.5]})

    with pytest.raises(ValueError, match="period must be a positive integer"):
        wilder_atr(frame, period=period)  # type: ignore[arg-type]


@pytest.mark.parametrize("missing_column", ["high", "low", "close"])
def test_wilder_atr_rejects_missing_columns(missing_column: str) -> None:
    frame = pd.DataFrame({"high": [2.0], "low": [1.0], "close": [1.5]}).drop(columns=missing_column)

    with pytest.raises(ValueError, match=rf"required columns: {missing_column}"):
        wilder_atr(frame)
