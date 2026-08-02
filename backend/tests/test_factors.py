from datetime import date, timedelta

import pandas as pd

from qagent.factors.engine import (
    A_SHARE_FACTOR_WEIGHTS,
    ETF_FACTOR_WEIGHTS,
    FACTOR_WEIGHTS,
    RESEARCH_FACTOR_WEIGHTS,
    build_factor_rankings,
)
from qagent.strategy_data.models import FundamentalSnapshot


def _bars(
    instrument_id: str,
    closes: list[float],
    volume: int = 1_000_000,
    *,
    asset_type: object = "stock",
) -> pd.DataFrame:
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
                "asset_type": asset_type,
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


def test_research_factors_are_observable_without_changing_ranking_weight():
    smooth = [10 * (1.003**index) for index in range(140)]
    volatile = [10 * (1.003**index) * (1.09 if index % 2 else 0.91) for index in range(140)]
    bars = pd.concat(
        [
            _bars("CN:000001", smooth, volume=2_000_000),
            _bars("CN:300001", volatile, volume=2_000_000),
        ],
        ignore_index=True,
    )
    fundamentals = [
        FundamentalSnapshot(
            instrument_id="CN:000001",
            as_of_date=date(2026, 6, 30),
            return_on_equity_pct=22,
            gross_margin_pct=48,
            operating_margin_pct=20,
            net_margin_pct=18,
            revenue_growth_pct=25,
            earnings_growth_pct=32,
        ),
        FundamentalSnapshot(
            instrument_id="CN:300001",
            as_of_date=date(2026, 6, 30),
            return_on_equity_pct=1,
            gross_margin_pct=10,
            operating_margin_pct=-4,
            net_margin_pct=-6,
            revenue_growth_pct=-8,
            earnings_growth_pct=-12,
        ),
    ]

    rankings = build_factor_rankings(bars, fundamentals=fundamentals)
    by_symbol = {ranking.instrument_id: ranking for ranking in rankings}
    strong = by_symbol["CN:000001"]
    weak = by_symbol["CN:300001"]

    assert strong.profitability_score > weak.profitability_score
    assert strong.growth_score > weak.growth_score
    assert strong.downside_risk_score > weak.downside_risk_score
    research_exposures = {
        exposure.factor_id: exposure
        for exposure in strong.factor_exposures
        if exposure.factor_id in RESEARCH_FACTOR_WEIGHTS
    }
    assert set(research_exposures) == set(RESEARCH_FACTOR_WEIGHTS)
    assert all(exposure.raw_value is not None for exposure in research_exposures.values())
    assert all(exposure.weight == 0 for exposure in research_exposures.values())


def test_market_adjusted_momentum_is_scored_within_asset_pool():
    common_returns = [
        0.002 + (0.008 if index % 4 == 0 else -0.002) for index in range(139)
    ]

    def prices(alpha: float) -> list[float]:
        values = [10.0]
        for market_return in common_returns:
            values.append(values[-1] * (1 + market_return + alpha))
        return values

    bars = pd.concat(
        [
            _bars("CN:000001", prices(0.0)),
            _bars("CN:000002", prices(0.0015)),
            _bars("CN:000003", prices(-0.001)),
        ],
        ignore_index=True,
    )

    rankings = build_factor_rankings(bars)
    by_symbol = {ranking.instrument_id: ranking for ranking in rankings}

    assert (
        by_symbol["CN:000002"].market_adjusted_momentum_score
        > by_symbol["CN:000003"].market_adjusted_momentum_score
    )
    exposure = next(
        item
        for item in by_symbol["CN:000002"].factor_exposures
        if item.factor_id == "market_adjusted_momentum"
    )
    assert exposure.raw_value is not None
    assert exposure.weight == 0


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


def test_factor_engine_rewards_smooth_regression_momentum_over_noisy_path():
    smooth = [10 * (1.004**index) for index in range(140)]
    noisy = [10 * (1.004**index) * (1.12 if index % 2 else 0.88) for index in range(140)]
    bars = pd.concat(
        [
            _bars("CN:000001", smooth, volume=2_000_000),
            _bars("CN:000002", noisy, volume=2_000_000),
        ],
        ignore_index=True,
    )

    rankings = build_factor_rankings(bars)
    by_symbol = {ranking.instrument_id: ranking for ranking in rankings}

    assert by_symbol["CN:000001"].trend_quality_score > by_symbol["CN:000002"].trend_quality_score
    trend_exposure = next(
        exposure
        for exposure in by_symbol["CN:000001"].factor_exposures
        if exposure.factor_id == "trend_quality"
    )
    assert "R-squared" in trend_exposure.explanation


def test_factor_engine_does_not_hide_missing_close_inside_regression_window():
    bars = _bars(
        "CN:000001",
        [10 * (1.003**index) for index in range(60)],
        volume=2_000_000,
    )
    bars.loc[bars.index[-10], "close"] = None

    [ranking] = build_factor_rankings(bars)

    assert "29d_trend_regression" in ranking.missing_data


def test_factor_engine_ranks_asset_buckets_without_cross_contamination():
    stock_bars = pd.concat(
        [
            _bars(
                "CN:STOCK-1",
                [10 + index * 0.06 for index in range(140)],
                volume=2_000_000,
                asset_type="equity",
            ),
            _bars(
                "CN:STOCK-2",
                [16 - index * 0.03 for index in range(140)],
                volume=700_000,
                asset_type=1,
            ),
        ],
        ignore_index=True,
    )
    etf_bars = pd.concat(
        [
            _bars(
                "CN:ETF-1",
                [8 + index * 0.04 for index in range(140)],
                volume=3_000_000,
                asset_type="fund",
            ),
            _bars(
                "CN:ETF-2",
                [12 + ((-1) ** index) * 0.4 for index in range(140)],
                volume=1_000_000,
                asset_type=5,
            ),
        ],
        ignore_index=True,
    )

    stock_only = {item.instrument_id: item for item in build_factor_rankings(stock_bars)}
    etf_only = {item.instrument_id: item for item in build_factor_rankings(etf_bars)}
    mixed = {
        item.instrument_id: item
        for item in build_factor_rankings(pd.concat([etf_bars, stock_bars], ignore_index=True))
    }

    for instrument_id, ranking in {**stock_only, **etf_only}.items():
        assert mixed[instrument_id].model_dump() == ranking.model_dump()


def test_factor_engine_uses_etf_weights_without_fundamental_completeness_penalty():
    closes = [10 + index * 0.04 for index in range(140)]
    etf = build_factor_rankings(_bars("CN:ETF", closes, asset_type="index_fund"))[0]
    stock = build_factor_rankings(_bars("CN:STOCK", closes, asset_type="stock"))[0]

    etf_weights = {exposure.factor_id: exposure.weight for exposure in etf.factor_exposures}

    assert etf.data_completeness == 1.0
    assert stock.data_completeness == 0.82
    assert {"valuation_ep", "market_cap", "quality_fundamentals"} <= set(etf.missing_data)
    assert etf_weights == {
        "valuation": 0.0,
        "size": 0.0,
        "quality": 0.0,
        **ETF_FACTOR_WEIGHTS,
    }


def test_factor_engine_infers_etf_from_code_without_asset_type_column():
    bars = _bars(
        "CN:588200",
        [10 + index * 0.04 for index in range(140)],
    ).drop(columns=["asset_type"])

    [ranking] = build_factor_rankings(bars)
    weights = {
        exposure.factor_id: exposure.weight for exposure in ranking.factor_exposures
    }

    assert ranking.data_completeness == 1.0
    assert weights == {
        "valuation": 0.0,
        "size": 0.0,
        "quality": 0.0,
        **ETF_FACTOR_WEIGHTS,
    }


def test_factor_engine_asset_type_override_takes_precedence():
    bars = _bars(
        "CN:000001",
        [10 + index * 0.04 for index in range(140)],
        asset_type="stock",
    )

    [ranking] = build_factor_rankings(
        bars,
        asset_types={"CN:000001": "etf"},
    )
    weights = {
        exposure.factor_id: exposure.weight for exposure in ranking.factor_exposures
    }

    assert ranking.data_completeness == 1.0
    assert weights == {
        "valuation": 0.0,
        "size": 0.0,
        "quality": 0.0,
        **ETF_FACTOR_WEIGHTS,
    }


def test_factor_engine_keeps_unknown_separate_from_stock_weights():
    unknown_bars = _bars(
        "CN:UNKNOWN",
        [10 + index * 0.04 for index in range(140)],
        asset_type="unknown",
    )
    unknown_only = build_factor_rankings(unknown_bars)[0]
    rankings = build_factor_rankings(
        pd.concat(
            [
                _bars(
                    "CN:STOCK",
                    [18 - index * 0.03 for index in range(140)],
                    asset_type="stock",
                ),
                unknown_bars,
            ],
            ignore_index=True,
        )
    )
    weights_by_id = {
        ranking.instrument_id: {
            exposure.factor_id: exposure.weight for exposure in ranking.factor_exposures
        }
        for ranking in rankings
    }

    assert weights_by_id["CN:STOCK"] == A_SHARE_FACTOR_WEIGHTS
    assert weights_by_id["CN:UNKNOWN"] == FACTOR_WEIGHTS
    unknown_mixed = next(ranking for ranking in rankings if ranking.instrument_id == "CN:UNKNOWN")
    assert unknown_mixed.factor_score == unknown_only.factor_score


def test_factor_engine_output_is_stable_for_shuffled_mixed_asset_input():
    bars = pd.concat(
        [
            _bars(
                "CN:STOCK",
                [10 + index * 0.04 for index in range(140)],
                asset_type="stock",
            ),
            _bars(
                "CN:ETF",
                [15 - index * 0.02 for index in range(140)],
                asset_type="etf",
            ),
            _bars(
                "CN:UNKNOWN",
                [9 + ((-1) ** index) * 0.2 for index in range(140)],
                asset_type="unknown",
            ),
        ],
        ignore_index=True,
    )

    ordered = build_factor_rankings(bars)
    shuffled = build_factor_rankings(bars.sample(frac=1.0, random_state=17))

    assert [item.model_dump() for item in ordered] == [item.model_dump() for item in shuffled]
