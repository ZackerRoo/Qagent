from datetime import date, timedelta

import pandas as pd

from qagent.cards.factor_watch import build_factor_watch_card
from qagent.domain.models import StrategyCalibration
from qagent.factors.models import FactorRanking
from qagent.research.market_intelligence import (
    apply_market_intelligence_to_cards,
    build_market_intelligence_center,
)
from qagent.strategies.models import StrategyHealth


def test_market_intelligence_center_covers_quality_regime_scheduler_calibration_events():
    cards = [
        _card("CN:688981", "中芯国际 688981.SH", 0.88, 1, "validated"),
        _card("CN:588000", "科创50ETF 588000.SH", 0.74, 2, "limited_sample"),
    ]
    bars_by_instrument = {card.instrument_id: _bars(card.instrument_id) for card in cards}

    center = build_market_intelligence_center(
        cards=cards,
        items=[],
        bars_by_instrument=bars_by_instrument,
        strategy_health=[
            StrategyHealth(
                strategy_id="factor_rotation_watch",
                name="Factor rotation watch",
                family="factor_rotation",
                readiness="validated",
                sample_count=24,
                win_rate_10d=62.0,
                avg_return_10d=2.4,
                avg_return_20d=4.8,
                max_loss_10d=-4.5,
                points=[],
            )
        ],
        data_health={
            "provider": "free",
            "market_cache": "enabled",
            "strategy_announcements": "3",
            "strategy_fundamentals": "2",
        },
    )

    assert center.data_quality.score > 0.6
    assert center.data_quality.adjustment_status in {"partial", "unknown", "ready"}
    assert center.market_environment.regime in {
        "risk_on",
        "constructive",
        "mixed",
        "risk_off",
        "thin",
    }
    assert center.market_environment.breadth.advance_ratio is not None
    assert center.strategy_scheduler.weights
    assert center.strategy_scheduler.weights[0].weight_pct > 0
    assert center.recommendation_calibration.summary
    assert center.recommendation_calibration.rules_applied
    assert center.event_hypotheses.summary
    assert center.event_hypotheses.hypotheses
    assert center.data_health["market_intelligence_cards"] == "2"
    checks = {item.area: item for item in center.data_quality.source_checks}
    assert {
        "adjusted_price",
        "suspension",
        "price_limit",
        "industry",
        "liquidity",
        "index_constituents",
        "fund_flow",
        "dragon_tiger",
        "announcements",
    }.issubset(checks)
    assert checks["adjusted_price"].severity in {"ok", "watch", "risk"}
    assert checks["liquidity"].coverage_ratio is not None
    assert checks["fund_flow"].recommended_action
    assert center.data_health["data_source_checks"] == str(len(center.data_quality.source_checks))


def test_market_intelligence_calibrates_cards_before_sorting():
    strong = _card("CN:688981", "中芯国际 688981.SH", 0.88, 1, "validated")
    weak = _card("CN:588000", "科创50ETF 588000.SH", 0.44, 2, "limited_sample")
    weak.data_caveats.extend(["provider: free", "missing adjusted close"])
    original_strong_score = strong.rank_score
    original_weak_score = weak.rank_score

    center = build_market_intelligence_center(
        cards=[strong, weak],
        items=[],
        bars_by_instrument={
            strong.instrument_id: _bars(strong.instrument_id),
            weak.instrument_id: _bars(weak.instrument_id, direction=-1),
        },
        strategy_health=[],
        data_health={"provider": "free", "provider_error_count": "1"},
    )
    calibrated = apply_market_intelligence_to_cards([strong, weak], center)

    assert calibrated[0].dynamic_score is not None
    assert calibrated[0].market_fit_score is not None
    assert calibrated[0].quality_score is not None
    assert calibrated[0].calibration_notes
    assert strong.rank_score != original_strong_score
    assert weak.rank_score < original_weak_score
    assert weak.rank_score <= calibrated[0].rank_score


def _card(
    instrument_id: str,
    label: str,
    factor_score: float,
    rank: int,
    readiness: str,
):
    card = build_factor_watch_card(
        instrument_id,
        _bars(instrument_id),
        FactorRanking(
            instrument_id=instrument_id,
            instrument_label=label,
            factor_score=factor_score,
            factor_rank=rank,
            percentile=factor_score,
            momentum_score=factor_score,
            trend_quality_score=max(0.1, factor_score - 0.05),
            liquidity_score=0.8,
            low_risk_score=0.7,
            reversal_score=0.55,
            execution_penalty=0.0,
            data_completeness=0.9,
            factor_exposures=[],
            flags=[],
            missing_data=[],
        ),
    )
    assert card is not None
    card.strategy_calibration = StrategyCalibration(
        strategy_id="factor_rotation_watch",
        readiness=readiness,
        sample_count=24 if readiness == "validated" else 7,
        win_rate_10d=62.0 if readiness == "validated" else 48.0,
        avg_return_10d=2.4 if readiness == "validated" else -0.4,
        avg_return_20d=4.8 if readiness == "validated" else 0.2,
        max_loss_10d=-4.5 if readiness == "validated" else -8.5,
        message="策略校准：测试样本。",
    )
    return card


def _bars(instrument_id: str, direction: int = 1) -> pd.DataFrame:
    start = date(2026, 1, 1)
    rows = []
    for index in range(80):
        close = 10 + direction * index * 0.04
        rows.append(
            {
                "instrument_id": instrument_id,
                "trade_date": start + timedelta(days=index),
                "open": close - 0.02,
                "high": close + 0.08,
                "low": close - 0.08,
                "close": close,
                "volume": 1_500_000 + index * 2_000,
                "provider": "free",
            }
        )
    return pd.DataFrame(rows)
