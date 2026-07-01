from datetime import date, timedelta

import pandas as pd

from qagent.cards.factor_watch import build_factor_watch_card
from qagent.factors.models import FactorExposure, FactorRanking


def test_factor_watch_card_exposes_confidence_explanation_and_execution_plan():
    card = build_factor_watch_card(
        "CN:588000",
        _bars("CN:588000"),
        _ranking("CN:588000", "科创50ETF 588000.SH", 0.74, 3),
    )

    assert card is not None
    assert card.confidence_explanation is not None
    assert card.confidence_explanation.score == card.decision.conviction_score
    assert card.confidence_explanation.label in {"高可信", "中等可信", "低可信"}
    assert card.confidence_explanation.positive_drivers
    assert card.confidence_explanation.risk_drivers
    assert card.confidence_explanation.data_checks
    assert "必涨" not in card.confidence_explanation.model_dump_json()

    assert card.execution_plan is not None
    assert card.execution_plan.action == card.decision.action
    assert card.execution_plan.action_label in {"可候选买入", "等待触发", "等待回踩", "暂不参与", "观察"}
    assert str(card.entry_plan.trigger_price) in card.execution_plan.buy_zone
    assert str(card.exit_plan.initial_stop) in card.execution_plan.sell_plan
    assert card.execution_plan.next_checklist
    assert "guarantee" not in card.execution_plan.model_dump_json().lower()
    assert "必涨" not in card.execution_plan.model_dump_json()


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
        trend_quality_score=0.72,
        liquidity_score=0.8,
        low_risk_score=0.64,
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
    for index in range(70):
        close = 10 + index * 0.06
        rows.append(
            {
                "instrument_id": instrument_id,
                "trade_date": start + timedelta(days=index),
                "open": close - 0.04,
                "high": close + 0.12,
                "low": close - 0.14,
                "close": close,
                "volume": 1_500_000 + index * 1_000,
                "provider": "fixture",
            }
        )
    return pd.DataFrame(rows)
