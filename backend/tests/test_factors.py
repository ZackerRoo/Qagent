from datetime import date, timedelta

import pandas as pd

from qagent.factors.engine import build_factor_rankings
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


def test_factor_engine_ranks_strong_liquid_low_risk_stock_first():
    strong = [10 + index * 0.08 for index in range(140)]
    choppy = [10 + ((-1) ** index) * 0.9 + index * 0.01 for index in range(140)]
    weak = [18 - index * 0.05 for index in range(140)]
    bars = pd.concat(
        [
            _bars("CN:000001", strong, volume=2_000_000),
            _bars("CN:600519", choppy, volume=900_000),
            _bars("CN:300750", weak, volume=1_200_000),
        ],
        ignore_index=True,
    )

    rankings = build_factor_rankings(bars)
    by_symbol = {ranking.instrument_id: ranking for ranking in rankings}

    assert rankings[0].instrument_id == "CN:000001"
    assert by_symbol["CN:000001"].factor_score > by_symbol["CN:600519"].factor_score
    assert by_symbol["CN:000001"].factor_score > by_symbol["CN:300750"].factor_score
    assert by_symbol["CN:000001"].momentum_score > 0.5
    assert by_symbol["CN:000001"].trend_quality_score > 0.5
    assert by_symbol["CN:000001"].low_risk_score > by_symbol["CN:600519"].low_risk_score
    assert by_symbol["CN:000001"].factor_exposures


def test_factor_engine_marks_insufficient_history_and_reduces_completeness():
    short = _bars("CN:000001", [10, 10.2, 10.1, 10.3, 10.4], volume=1_000_000)

    [ranking] = build_factor_rankings(short)

    assert ranking.instrument_id == "CN:000001"
    assert ranking.data_completeness < 1
    assert "insufficient_history" in ranking.flags
    assert "120d_return" in ranking.missing_data


def test_factor_engine_uses_a_share_valuation_size_and_quality_inputs():
    steady = [10 + index * 0.04 for index in range(140)]
    speculative = [8 + index * 0.09 for index in range(140)]
    bars = pd.concat(
        [
            _bars("CN:000001", steady, volume=2_000_000),
            _bars("CN:300001", speculative, volume=2_500_000),
        ],
        ignore_index=True,
    )
    fundamentals = [
        FundamentalSnapshot(
            instrument_id="CN:000001",
            as_of_date=date(2026, 6, 30),
            pe_ratio=8,
            market_cap=80_000_000_000,
            return_on_equity_pct=18,
            gross_margin_pct=42,
            net_margin_pct=16,
            revenue_growth_pct=12,
        ),
        FundamentalSnapshot(
            instrument_id="CN:300001",
            as_of_date=date(2026, 6, 30),
            pe_ratio=90,
            market_cap=1_200_000_000,
            return_on_equity_pct=2,
            gross_margin_pct=12,
            net_margin_pct=-3,
            revenue_growth_pct=-5,
        ),
    ]

    rankings = build_factor_rankings(bars, fundamentals=fundamentals)
    by_symbol = {ranking.instrument_id: ranking for ranking in rankings}

    assert rankings[0].instrument_id == "CN:000001"
    assert by_symbol["CN:000001"].valuation_score > by_symbol["CN:300001"].valuation_score
    assert by_symbol["CN:000001"].size_score > by_symbol["CN:300001"].size_score
    assert by_symbol["CN:000001"].quality_score > by_symbol["CN:300001"].quality_score
    assert "shell_size_risk" in by_symbol["CN:300001"].flags


def test_factor_engine_penalizes_overheated_high_volatility_low_liquidity_names():
    base = [10 + index * 0.02 for index in range(120)]
    overheated = base + [20, 24, 28, 34, 42]
    calm = [10 + index * 0.03 for index in range(125)]
    bars = pd.concat(
        [
            _bars("CN:000001", calm, volume=2_000_000),
            _bars("CN:688001", overheated, volume=120_000),
        ],
        ignore_index=True,
    )

    rankings = build_factor_rankings(bars)
    by_symbol = {ranking.instrument_id: ranking for ranking in rankings}

    risky = by_symbol["CN:688001"]
    assert "overextended" in risky.flags
    assert "high_volatility" in risky.flags
    assert "low_liquidity" in risky.flags
    assert risky.risk_filter_score < by_symbol["CN:000001"].risk_filter_score
    assert risky.execution_penalty > by_symbol["CN:000001"].execution_penalty
