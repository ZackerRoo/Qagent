from datetime import date, timedelta

import pandas as pd

from qagent.factors.engine import build_factor_rankings


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
