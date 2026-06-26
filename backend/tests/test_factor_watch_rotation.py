from datetime import date, timedelta

import pandas as pd

from qagent.cards.factor_watch import build_factor_watch_card
from qagent.domain.enums import OpportunityStatus
from qagent.factors.models import FactorExposure, FactorRanking
from qagent.recommendations.rotation import sort_recommendation_cards


def test_factor_watch_card_exposes_etf_bucket_and_trade_plan():
    card = build_factor_watch_card(
        "CN:588000",
        _bars("CN:588000"),
        _ranking("CN:588000", "科创50ETF 588000.SH", 0.74, 3),
    )

    assert card is not None
    assert card.status == OpportunityStatus.WATCH
    assert card.primary_strategy_id == "factor_rotation_watch"
    assert card.asset_type == "ETF"
    assert card.opportunity_bucket == "etf_index"
    assert "ETF" in card.opportunity_tags
    assert card.entry_plan.trigger_price is not None
    assert card.exit_plan.initial_stop is not None
    assert card.exit_plan.target_1 is not None
    assert card.decision is not None
    assert card.decision.action in {"watch_trigger", "wait_pullback"}
    assert card.recommendation_summary is not None
    assert "科创50ETF" in card.recommendation_summary.headline


def test_recommendation_sorting_keeps_theme_and_etf_visible():
    bank = build_factor_watch_card(
        "CN:000001",
        _bars("CN:000001"),
        _ranking("CN:000001", "平安银行 000001.SZ", 0.92, 1),
    )
    broker = build_factor_watch_card(
        "CN:600030",
        _bars("CN:600030"),
        _ranking("CN:600030", "中信证券 600030.SH", 0.9, 2),
    )
    etf = build_factor_watch_card(
        "CN:588000",
        _bars("CN:588000"),
        _ranking("CN:588000", "科创50ETF 588000.SH", 0.76, 6),
    )
    memory = build_factor_watch_card(
        "CN:688008",
        _bars("CN:688008"),
        _ranking("CN:688008", "澜起科技 688008.SH", 0.72, 8),
    )

    ordered = sort_recommendation_cards([bank, broker, etf, memory])
    top_three = [card.instrument_id for card in ordered[:3]]

    assert "CN:588000" in top_three
    assert "CN:688008" in top_three
    assert ordered[0].rotation_note


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
