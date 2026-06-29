from datetime import date, timedelta

import pandas as pd

from qagent.cards.factor_watch import build_factor_watch_card
from qagent.domain.models import StrategyCalibration
from qagent.factors.models import FactorExposure, FactorRanking
from qagent.recommendations.signal_hub import build_signal_hub


def test_signal_hub_combines_validation_timeline_and_alert_suggestions():
    card = build_factor_watch_card(
        "CN:688008",
        _bars("CN:688008"),
        _ranking("CN:688008", "澜起科技 688008.SH", 0.82, 2),
    )
    assert card is not None
    card.strategy_calibration = StrategyCalibration(
        strategy_id="factor_rotation_watch",
        readiness="validated",
        sample_count=18,
        win_rate_10d=61.1,
        avg_return_10d=2.6,
        avg_return_20d=4.8,
        max_loss_10d=-5.2,
        message="策略校准：样本18个，10日胜率61.10%。",
    )

    hub = build_signal_hub(card, rotation_score=0.84, rotation_name="存储芯片")

    assert hub.trust_score >= 0.7
    assert hub.label in {"高可信", "中等可信"}
    assert hub.rotation_context == "存储芯片"
    assert {component.key for component in hub.components}.issuperset(
        {"rotation", "strategy", "factor", "history", "risk", "execution"}
    )
    assert hub.similar_validation.sample_count == 18
    assert hub.similar_validation.win_rate_10d == 61.1
    assert any(event.key == "entry_trigger" for event in hub.timeline)
    assert any(event.key == "target_1" for event in hub.timeline)
    assert any(item.kind == "signal_weakened" for item in hub.alert_suggestions)


def _ranking(
    instrument_id: str,
    label: str,
    factor_score: float,
    rank: int,
) -> FactorRanking:
    return FactorRanking(
        instrument_id=instrument_id,
        instrument_label=label,
        factor_score=factor_score,
        factor_rank=rank,
        percentile=0.9,
        momentum_score=factor_score,
        trend_quality_score=0.75,
        liquidity_score=0.82,
        low_risk_score=0.68,
        reversal_score=0.55,
        execution_penalty=0.0,
        data_completeness=1.0,
        factor_exposures=[
            FactorExposure(
                factor_id="momentum",
                label="Momentum",
                raw_value=0.12,
                score=factor_score,
                weight=0.3,
                explanation="Momentum contribution.",
            )
        ],
        flags=[],
        missing_data=[],
    )


def _bars(instrument_id: str) -> pd.DataFrame:
    start = date(2026, 1, 1)
    rows = []
    for index in range(80):
        close = 10 + index * 0.05
        rows.append(
            {
                "instrument_id": instrument_id,
                "trade_date": start + timedelta(days=index),
                "open": close - 0.03,
                "high": close + 0.1,
                "low": close - 0.12,
                "close": close,
                "volume": 1_500_000 + index * 2_000,
                "provider": "fixture",
            }
        )
    return pd.DataFrame(rows)
