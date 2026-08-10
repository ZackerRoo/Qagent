from datetime import date, timedelta

import pandas as pd

from qagent.historical_evidence.providers import REQUIRED_BENCHMARK_IDS
from qagent.market.benchmark_trend import (
    BenchmarkTrendState,
    build_benchmark_trend_snapshot,
)


def test_benchmark_trend_blocks_entries_when_three_indexes_are_below_ma60():
    bars = _benchmark_bars(below_count=3)

    snapshot = build_benchmark_trend_snapshot(
        bars,
        as_of=date(2025, 4, 1),
    )

    assert snapshot.state == BenchmarkTrendState.RISK_OFF
    assert snapshot.valid_benchmarks == 4
    assert snapshot.below_average_count == 3
    assert snapshot.entry_allowed is False
    assert snapshot.reason.endswith("常规仓位进入防守模式。")


def test_benchmark_trend_keeps_entries_when_market_is_mixed():
    bars = _benchmark_bars(below_count=2)

    snapshot = build_benchmark_trend_snapshot(
        bars,
        as_of=date(2025, 4, 1),
    )

    assert snapshot.state == BenchmarkTrendState.MIXED
    assert snapshot.above_average_count == 2
    assert snapshot.entry_allowed is True


def test_benchmark_trend_does_not_block_when_history_is_incomplete():
    bars = _benchmark_bars(below_count=4).groupby("instrument_id").head(30)

    snapshot = build_benchmark_trend_snapshot(
        bars,
        as_of=date(2025, 4, 1),
    )

    assert snapshot.state == BenchmarkTrendState.UNKNOWN
    assert snapshot.valid_benchmarks == 0
    assert snapshot.entry_allowed is True


def _benchmark_bars(*, below_count: int) -> pd.DataFrame:
    end = date(2025, 4, 1)
    dates = [end - timedelta(days=offset) for offset in range(59, -1, -1)]
    rows = []
    for index, instrument_id in enumerate(REQUIRED_BENCHMARK_IDS):
        below = index < below_count
        closes = [100 + step * 0.2 for step in range(59)]
        closes.append(80 if below else 120)
        rows.extend(
            {
                "instrument_id": instrument_id,
                "trade_date": trade_date,
                "close": close,
                "adjusted_close": close,
            }
            for trade_date, close in zip(dates, closes, strict=True)
        )
    return pd.DataFrame(rows)
