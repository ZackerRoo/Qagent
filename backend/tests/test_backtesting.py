from datetime import date

from qagent.backtesting.engine import build_backtest_scan_dates, run_historical_backtest
from qagent.providers.fixtures import FixtureMarketDataProvider


def test_build_backtest_scan_dates_uses_available_trading_dates():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 1, 30))

    scan_dates = build_backtest_scan_dates(
        bars,
        start=date(2026, 1, 15),
        end=date(2026, 1, 30),
        step_days=5,
    )

    assert scan_dates == [
        date(2026, 1, 15),
        date(2026, 1, 22),
        date(2026, 1, 29),
    ]


def test_run_historical_backtest_returns_signal_outcomes_and_strategy_performance():
    result = run_historical_backtest(
        instrument_ids=["US:TEST", "CN:000001"],
        provider=FixtureMarketDataProvider(),
        start=date(2026, 1, 30),
        end=date(2026, 3, 20),
        step_days=5,
        max_signals=20,
    )

    assert result.summary.provider == "fixture"
    assert result.summary.symbols == ["US:TEST", "CN:000001"]
    assert result.summary.scan_count > 0
    assert result.summary.evaluated_signals > 0
    assert result.summary.completed_signals > 0
    assert result.summary.target_hit_rate is not None
    assert result.summary.positive_rate_10d is not None
    assert result.summary.avg_return_10d is not None
    assert result.performance
    assert result.signals
    assert result.benchmark.label == "Equal-weight scanned universe"
    assert result.benchmark.benchmark_return_10d is not None
    assert result.benchmark.excess_return_10d is not None
    assert result.benchmark.verdict in {"outperform", "inline", "underperform", "insufficient_sample"}
    assert result.environment_breakdown
    assert {item.regime for item in result.environment_breakdown}.issubset({"up", "range", "down"})
    assert sum(item.sample_count for item in result.environment_breakdown) >= result.summary.completed_signals
    assert all(signal.signal_date <= date(2026, 3, 20) for signal in result.signals)
    assert all(signal.snapshot_id.startswith("backtest-") for signal in result.signals)
    assert result.data_health["lookahead_guard"] == "bars_limited_to_scan_date"
