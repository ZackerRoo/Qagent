from datetime import date, timedelta
from types import SimpleNamespace

from qagent.backtesting.temporal_validation import build_temporal_validation


def _signals(count: int, returns: list[float]):
    start = date(2025, 1, 1)
    return [
        SimpleNamespace(
            signal_date=start + timedelta(days=index),
            return_10d=returns[index % len(returns)],
        )
        for index in range(count)
    ]


def test_temporal_validation_uses_chronological_embargoed_windows_and_bootstrap_ci():
    signals = _signals(80, [1.0, 1.5, 2.0, 2.5])

    first = build_temporal_validation(
        signals,
        return_horizon_days=10,
        embargo_days=5,
        bootstrap_samples=400,
        seed=7,
    )
    second = build_temporal_validation(
        signals,
        return_horizon_days=10,
        embargo_days=5,
        bootstrap_samples=400,
        seed=7,
    )

    windows = {window.key: window for window in first.windows}
    train = windows["train"]
    validation = windows["validation"]
    out_of_sample = windows["out_of_sample"]
    assert train.end_date < validation.start_date
    assert validation.end_date < out_of_sample.start_date
    assert (validation.start_date - train.end_date).days > 5
    assert (out_of_sample.start_date - validation.end_date).days > 5
    assert out_of_sample.sample_count >= 10
    assert out_of_sample.confidence_low_pct is not None
    assert out_of_sample.confidence_low_pct > 0
    assert first.verdict == "positive"
    assert first.out_of_sample == out_of_sample
    assert first.model_dump() == second.model_dump()


def test_temporal_validation_reports_insufficient_when_embargo_removes_holdout():
    result = build_temporal_validation(
        _signals(6, [1.0, -1.0]),
        return_horizon_days=10,
        embargo_days=10,
        bootstrap_samples=100,
        seed=11,
    )

    assert result.verdict == "insufficient"
    assert result.out_of_sample is None or result.out_of_sample.sample_count < 10
    assert result.warnings
    assert result.data_health["temporal_validation"] == "insufficient"
