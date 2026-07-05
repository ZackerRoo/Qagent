from datetime import date, timedelta

import pandas as pd

from qagent.factors.backtest import run_factor_backtest
from qagent.strategy_data.models import FundamentalSnapshot


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


def test_factor_backtest_summarizes_forward_returns_by_rank():
    strong = [10 + index * 0.08 for index in range(180)]
    mid = [12 + index * 0.03 for index in range(180)]
    weak = [20 - index * 0.02 for index in range(180)]
    bars = pd.concat(
        [
            _bars("CN:000001", strong, volume=2_000_000),
            _bars("CN:300750", mid, volume=1_500_000),
            _bars("CN:600519", weak, volume=1_000_000),
        ],
        ignore_index=True,
    )

    result = run_factor_backtest(bars, forward_days=10, step_days=20, top_n=3)

    assert result.rank_buckets
    assert [bucket.factor_rank for bucket in result.rank_buckets] == [1, 2, 3]
    assert all(bucket.sample_count > 0 for bucket in result.rank_buckets)
    assert result.rank_buckets[0].avg_forward_return_pct is not None
    assert result.rank_buckets[0].positive_rate is not None
    assert result.information_coefficient.sample_count > 0
    assert result.information_coefficient.mean_ic is not None
    assert result.information_coefficient.mean_rank_ic is not None
    assert result.information_coefficient.top_bottom_spread_pct is not None
    assert result.information_coefficient.top_bottom_spread_pct > 0
    assert result.quantile_buckets
    assert result.quantile_buckets[0].quantile == 1
    assert result.quantile_buckets[-1].quantile == 5
    assert result.quantile_buckets[0].avg_forward_return_pct is not None
    assert result.factor_ic
    assert {item.factor_id for item in result.factor_ic} >= {"momentum", "trend_quality", "risk_filter"}


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


def test_factor_backtest_uses_point_in_time_historical_fundamentals():
    cheap = [10 + index * 0.06 for index in range(180)]
    expensive = [11 + index * 0.04 for index in range(180)]
    future_turnaround = [12 + index * 0.03 for index in range(180)]
    bars = pd.concat(
        [
            _bars("CN:000001", cheap, volume=2_000_000),
            _bars("CN:600519", expensive, volume=2_000_000),
            _bars("CN:688981", future_turnaround, volume=2_000_000),
        ],
        ignore_index=True,
    )
    fundamentals = [
        FundamentalSnapshot(
            instrument_id="CN:000001",
            as_of_date=date(2026, 1, 10),
            pe_ratio=8,
            market_cap=90_000_000_000,
            return_on_equity_pct=18,
            gross_margin_pct=40,
            net_margin_pct=14,
            revenue_growth_pct=10,
        ),
        FundamentalSnapshot(
            instrument_id="CN:600519",
            as_of_date=date(2026, 1, 10),
            pe_ratio=55,
            market_cap=1_800_000_000_000,
            return_on_equity_pct=12,
            gross_margin_pct=30,
            net_margin_pct=8,
            revenue_growth_pct=4,
        ),
        FundamentalSnapshot(
            instrument_id="CN:688981",
            as_of_date=date(2026, 7, 1),
            pe_ratio=5,
            market_cap=120_000_000_000,
            return_on_equity_pct=24,
            gross_margin_pct=50,
            net_margin_pct=20,
            revenue_growth_pct=28,
        ),
    ]

    result = run_factor_backtest(
        bars,
        forward_days=10,
        step_days=20,
        top_n=2,
        fundamentals=fundamentals,
    )

    valuation = next(item for item in result.factor_ic if item.factor_id == "valuation")
    assert valuation.sample_count > 0
    assert result.data_health["historical_fundamentals"] == str(len(fundamentals))
    assert result.data_health["fundamental_mode"] == "point_in_time"
    assert all(signal.instrument_id != "CN:688981" for signal in result.signals)
