from qagent.strategies.health import summarize_strategy_health
from qagent.strategies.registry import default_strategy_registry


def test_strategy_health_summarizes_base_rate_and_forward_returns():
    registry = default_strategy_registry()
    rows = [
        {
            "strategy_id": "trend_momentum_stage2",
            "return_10d": 4.0,
            "return_20d": 7.5,
        },
        {
            "strategy_id": "trend_momentum_stage2",
            "return_10d": -2.0,
            "return_20d": 1.0,
        },
        {
            "strategy_id": "trend_momentum_stage2",
            "return_10d": 1.0,
            "return_20d": 3.5,
        },
    ]

    health = summarize_strategy_health(registry, rows)
    trend = {item.strategy_id: item for item in health}["trend_momentum_stage2"]

    assert trend.sample_count == 3
    assert trend.win_rate_10d == 66.67
    assert trend.avg_return_10d == 1.0
    assert trend.avg_return_20d == 4.0
    assert trend.max_loss_10d == -2.0
    assert trend.readiness == "limited_sample"


def test_strategy_health_includes_period_curve_when_signal_dates_are_available():
    registry = default_strategy_registry()
    rows = [
        {
            "strategy_id": "trend_momentum_stage2",
            "signal_date": "2026-01-05",
            "return_10d": 4.0,
            "return_20d": 7.5,
        },
        {
            "strategy_id": "trend_momentum_stage2",
            "signal_date": "2026-01-18",
            "return_10d": -2.0,
            "return_20d": 1.0,
        },
        {
            "strategy_id": "trend_momentum_stage2",
            "signal_date": "2026-02-02",
            "return_10d": 3.0,
            "return_20d": 5.0,
        },
    ]

    health = summarize_strategy_health(registry, rows)
    trend = {item.strategy_id: item for item in health}["trend_momentum_stage2"]

    assert [point.label for point in trend.curve] == ["2026-01", "2026-02"]
    assert trend.curve[0].sample_count == 2
    assert trend.curve[0].win_rate_10d == 50.0
    assert trend.curve[0].avg_return_10d == 1.0
    assert trend.curve[0].max_loss_10d == -2.0
    assert trend.curve[1].sample_count == 1
    assert trend.curve[1].win_rate_10d == 100.0


def test_strategy_health_keeps_missing_data_strategies_visible():
    registry = default_strategy_registry()
    health = summarize_strategy_health(registry, [])
    by_id = {item.strategy_id: item for item in health}

    assert by_id["pead_earnings_drift"].readiness == "missing_data"
    assert by_id["pead_earnings_drift"].sample_count == 0
    assert "earnings_actuals" in by_id["pead_earnings_drift"].missing_data
