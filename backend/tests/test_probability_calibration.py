from datetime import date, timedelta

import pandas as pd

from qagent.cards.factor_watch import build_factor_watch_card
from qagent.factors.models import FactorRanking
from qagent.recommendations.probability import (
    apply_probability_calibration,
    probability_calibration_data_health,
)
from qagent.recommendations.quality_gate import apply_recommendation_quality_gate
from qagent.strategies.models import StrategyHealth


def test_probability_calibration_turns_scores_into_win_rate_and_adjusts_rank():
    strong = _card("CN:688059", "华锐精密 688059.SH", "trend_momentum_stage2", 0.82)
    weak = _card("CN:000001", "平安银行 000001.SZ", "factor_rotation_watch", 0.78)
    apply_recommendation_quality_gate([strong, weak])
    strong_before = strong.rank_score
    weak_before = weak.rank_score

    apply_probability_calibration(
        [strong, weak],
        [
            _health(
                "trend_momentum_stage2",
                sample_count=42,
                win_rate_10d=61.0,
                avg_return_10d=2.4,
                avg_return_20d=4.8,
                max_loss_10d=-5.2,
                readiness="validated",
            ),
            _health(
                "factor_rotation_watch",
                sample_count=28,
                win_rate_10d=39.0,
                avg_return_10d=-1.6,
                avg_return_20d=-2.8,
                max_loss_10d=-9.4,
                readiness="validated",
            ),
        ],
    )

    assert strong.probability_forecast is not None
    assert weak.probability_forecast is not None
    assert strong.probability_forecast.win_probability_10d > weak.probability_forecast.win_probability_10d
    assert strong.probability_forecast.expected_return_10d > weak.probability_forecast.expected_return_10d
    assert strong.probability_forecast.strategy_multiplier > 1
    assert weak.probability_forecast.strategy_multiplier < 1
    assert strong.rank_score > strong_before
    assert weak.rank_score < weak_before
    assert any("概率校准" in reason for reason in strong.rank_reasons)


def test_probability_calibration_marks_limited_samples_without_boosting():
    card = _card("CN:688981", "中芯国际 688981.SH", "factor_rotation_watch", 0.74)
    apply_recommendation_quality_gate([card])
    before = card.rank_score

    apply_probability_calibration(
        [card],
        [
            _health(
                "factor_rotation_watch",
                sample_count=4,
                win_rate_10d=67.0,
                avg_return_10d=3.2,
                avg_return_20d=5.6,
                max_loss_10d=-4.1,
                readiness="limited_sample",
            )
        ],
    )

    assert card.probability_forecast is not None
    assert card.probability_forecast.confidence == "limited_sample"
    assert card.probability_forecast.strategy_multiplier < 1
    assert card.rank_score <= before
    health = probability_calibration_data_health([card])
    assert health["probability_calibration_cards"] == "1"
    assert health["strategy_auto_weighting_applied"] == "1"


def _card(
    instrument_id: str,
    label: str,
    strategy_id: str,
    factor_score: float,
):
    card = build_factor_watch_card(
        instrument_id,
        _bars(instrument_id),
        FactorRanking(
            instrument_id=instrument_id,
            instrument_label=label,
            factor_score=factor_score,
            factor_rank=1,
            percentile=factor_score,
            momentum_score=factor_score,
            trend_quality_score=max(0.1, factor_score - 0.04),
            liquidity_score=0.82,
            low_risk_score=0.68,
            reversal_score=0.52,
            execution_penalty=0.0,
            data_completeness=0.9,
            factor_exposures=[],
            flags=[],
            missing_data=[],
        ),
    )
    assert card is not None
    card.primary_strategy_id = strategy_id
    card.strategy_evaluations[0].strategy_id = strategy_id
    return card


def _bars(instrument_id: str) -> pd.DataFrame:
    start = date(2026, 1, 1)
    rows = []
    for index in range(100):
        close = 20 + index * 0.08
        rows.append(
            {
                "instrument_id": instrument_id,
                "trade_date": start + timedelta(days=index),
                "open": close - 0.05,
                "high": close + 0.14,
                "low": close - 0.14,
                "close": close,
                "volume": 2_100_000 + index * 3_500,
                "provider": "fixture",
            }
        )
    return pd.DataFrame(rows)


def _health(
    strategy_id: str,
    *,
    sample_count: int,
    win_rate_10d: float,
    avg_return_10d: float,
    avg_return_20d: float,
    max_loss_10d: float,
    readiness: str,
) -> StrategyHealth:
    return StrategyHealth(
        strategy_id=strategy_id,
        name=strategy_id,
        family="test",
        readiness=readiness,
        sample_count=sample_count,
        win_rate_10d=win_rate_10d,
        avg_return_10d=avg_return_10d,
        avg_return_20d=avg_return_20d,
        max_loss_10d=max_loss_10d,
        curve=[],
    )
