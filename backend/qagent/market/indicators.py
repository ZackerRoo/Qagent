from dataclasses import dataclass
from math import expm1
from numbers import Integral
from typing import Literal, Sequence

import numpy as np
import pandas as pd


TRADING_DAYS_PER_YEAR = 252

MomentumStatus = Literal["available", "unavailable"]
MomentumUnavailableReason = Literal[
    "insufficient_samples",
    "non_finite_close",
    "non_positive_close",
    "non_finite_result",
]


@dataclass(frozen=True)
class RegressionQualityMomentumResult:
    annualized_return: float | None
    r_squared: float | None
    quality_score: float | None
    sample_size: int
    status: MomentumStatus
    reason: MomentumUnavailableReason | None = None


def add_moving_averages(frame: pd.DataFrame, windows: tuple[int, ...]) -> pd.DataFrame:
    result = frame.copy()
    for window in windows:
        result[f"ma_{window}"] = result["close"].rolling(window=window).mean()
    return result


def add_volume_ratio(frame: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    result = frame.copy()
    average_volume = result["volume"].rolling(window=window).mean().shift(1)
    result["volume_ratio"] = (result["volume"] / average_volume).round(4)
    return result


def percent_distance(value: float, reference: float) -> float:
    if reference == 0:
        raise ValueError("reference cannot be zero")
    return round((value - reference) / reference * 100, 4)


def wilder_atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Wilder's average true range while preserving the input index."""
    if isinstance(period, bool) or not isinstance(period, Integral) or period < 1:
        raise ValueError("period must be a positive integer")

    required_columns = {"high", "low", "close"}
    missing_columns = sorted(required_columns.difference(frame.columns))
    if missing_columns:
        missing = ", ".join(missing_columns)
        raise ValueError(f"frame is missing required columns: {missing}")

    period = int(period)
    high = pd.to_numeric(frame["high"], errors="coerce").astype(float)
    low = pd.to_numeric(frame["low"], errors="coerce").astype(float)
    close = pd.to_numeric(frame["close"], errors="coerce").astype(float)
    previous_close = close.shift(1)

    intraday_range = high - low
    true_range = pd.concat(
        (
            intraday_range,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ),
        axis=1,
    ).max(axis=1, skipna=False)
    if not true_range.empty:
        true_range.iloc[0] = intraday_range.iloc[0]
    true_range = true_range.where(np.isfinite(true_range))

    atr = pd.Series(np.nan, index=frame.index, dtype=float, name="atr")
    seed_values: list[float] = []
    previous_atr: float | None = None

    for position, true_range_value in enumerate(true_range.to_numpy(dtype=float)):
        if not np.isfinite(true_range_value):
            seed_values.clear()
            previous_atr = None
            continue

        if previous_atr is None:
            seed_values.append(float(true_range_value))
            if len(seed_values) < period:
                continue
            previous_atr = sum(seed_values) / period
        else:
            previous_atr = (previous_atr * (period - 1) + float(true_range_value)) / period
        atr.iloc[position] = previous_atr

    return atr


def regression_quality_momentum(
    close: pd.Series | Sequence[float],
    window: int = 29,
) -> RegressionQualityMomentumResult:
    """Measure annualized log-price trend strength and its regression quality."""
    if isinstance(window, bool) or not isinstance(window, Integral) or window < 2:
        raise ValueError("window must be an integer greater than one")

    window = int(window)
    close_series = pd.Series(close, copy=False)
    sample_size = min(len(close_series), window)
    if sample_size < window:
        return _unavailable_momentum(sample_size, "insufficient_samples")

    sample = pd.to_numeric(close_series.iloc[-window:], errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(sample).all():
        return _unavailable_momentum(sample_size, "non_finite_close")
    if (sample <= 0).any():
        return _unavailable_momentum(sample_size, "non_positive_close")

    log_close = np.log(sample)
    time = np.arange(window, dtype=float)
    centered_time = time - time.mean()
    centered_log_close = log_close - log_close.mean()
    slope = float(np.dot(centered_time, centered_log_close) / np.dot(centered_time, centered_time))
    fitted = log_close.mean() + slope * centered_time
    residual_sum_squares = float(np.dot(log_close - fitted, log_close - fitted))
    total_sum_squares = float(np.dot(centered_log_close, centered_log_close))
    if np.isclose(total_sum_squares, 0.0):
        r_squared = 1.0 if np.isclose(residual_sum_squares, 0.0) else 0.0
    else:
        r_squared = 1.0 - residual_sum_squares / total_sum_squares
    r_squared = min(1.0, max(0.0, r_squared))

    try:
        annualized_return = expm1(slope * TRADING_DAYS_PER_YEAR)
    except OverflowError:
        return _unavailable_momentum(sample_size, "non_finite_result")
    quality_score = annualized_return * r_squared
    if not np.isfinite((annualized_return, r_squared, quality_score)).all():
        return _unavailable_momentum(sample_size, "non_finite_result")

    return RegressionQualityMomentumResult(
        annualized_return=annualized_return,
        r_squared=r_squared,
        quality_score=quality_score,
        sample_size=sample_size,
        status="available",
    )


def _unavailable_momentum(
    sample_size: int,
    reason: MomentumUnavailableReason,
) -> RegressionQualityMomentumResult:
    return RegressionQualityMomentumResult(
        annualized_return=None,
        r_squared=None,
        quality_score=None,
        sample_size=sample_size,
        status="unavailable",
        reason=reason,
    )
