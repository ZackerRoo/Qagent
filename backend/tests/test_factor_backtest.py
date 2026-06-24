from datetime import date, timedelta

import pandas as pd

from qagent.factors.backtest import run_factor_backtest


def _bars(instrument_id: str, closes: list[float], volume: int = 1_000_000) -> pd.DataFrame:
    start = date(2026, 1, 1)
    rows = []
    for index, close in enumerate(closes):
        rows.append(
            {
                "instrument_id": instrument_id,
                "trade_date": start + timedelta(days=index),
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": volume,
                "provider": "fixture",
            }
        )
    return pd.DataFrame(rows)


def test_factor_backtest_evaluates_top_ranked_forward_returns():
    strong = [10 + index * 0.06 for index in range(180)]
    weak = [20 - index * 0.03 for index in range(180)]
    bars = pd.concat(
        [
            _bars("CN:000001", strong, volume=2_000_000),
            _bars("CN:600519", weak, volume=1_000_000),
        ],
        ignore_index=True,
    )

    result = run_factor_backtest(bars, forward_days=10, step_days=20, top_n=1)

    assert result.summary.sample_count > 0
    assert result.summary.positive_rate is not None
    assert result.summary.avg_forward_return_pct is not None
    assert result.summary.avg_forward_return_pct > 0
    assert result.signals
    assert {signal.instrument_id for signal in result.signals} == {"CN:000001"}


def test_factor_backtest_uses_shorter_history_when_development_data_is_limited():
    strong = [10 + index * 0.08 for index in range(70)]
    weak = [20 - index * 0.02 for index in range(70)]
    bars = pd.concat(
        [
            _bars("CN:000001", strong, volume=2_000_000),
            _bars("CN:600519", weak, volume=1_000_000),
        ],
        ignore_index=True,
    )

    result = run_factor_backtest(bars, forward_days=20, step_days=20, top_n=1)

    assert result.summary.sample_count > 0
    assert result.data_health["min_history_days"] == "40"
    assert result.signals[0].factor_rank == 1
