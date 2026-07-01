from datetime import date, timedelta

import pandas as pd

from qagent.cards.factor_watch import build_factor_watch_card
from qagent.domain.models import StrategyCalibration
from qagent.factors.models import FactorRanking
from qagent.research.action_center import build_manual_action_center
from qagent.research.market_intelligence import build_market_intelligence_center
from qagent.strategies.models import StrategyHealth


def test_manual_action_center_builds_user_operable_sections():
    cards = [
        _card("CN:688981", "中芯国际 688981.SH", 0.88, 1, "validated"),
        _card("CN:588000", "科创50ETF 588000.SH", 0.74, 2, "limited_sample"),
    ]
    market_intelligence = build_market_intelligence_center(
        cards=cards,
        items=[],
        bars_by_instrument={card.instrument_id: _bars(card.instrument_id) for card in cards},
        strategy_health=[
            StrategyHealth(
                strategy_id="factor_rotation_watch",
                name="Factor rotation watch",
                family="factor_rotation",
                readiness="validated",
                sample_count=31,
                win_rate_10d=63.0,
                avg_return_10d=2.1,
                avg_return_20d=4.2,
                max_loss_10d=-4.8,
                points=[],
            )
        ],
        data_health={
            "provider": "free",
            "market_cache": "enabled",
            "strategy_announcements": "2",
            "strategy_fundamentals": "1",
        },
    )

    center = build_manual_action_center(
        cards=cards,
        market_intelligence=market_intelligence,
        strategy_health=[
            StrategyHealth(
                strategy_id="factor_rotation_watch",
                name="Factor rotation watch",
                family="factor_rotation",
                readiness="validated",
                sample_count=31,
                win_rate_10d=63.0,
                avg_return_10d=2.1,
                avg_return_20d=4.2,
                max_loss_10d=-4.8,
                points=[],
            )
        ],
        data_health=market_intelligence.data_health,
    )

    assert center.headline
    assert center.today_actions
    assert {item.kind for item in center.alert_loop}.intersection(
        {"entry_trigger", "stop_guard", "target_1_reached", "signal_weakened"}
    )
    assert any(item.area == "adjusted_price" for item in center.data_source_roadmap)
    assert any(item.area == "fund_flow" for item in center.data_source_roadmap)
    assert center.strategy_effectiveness
    assert center.data_health["manual_action_cards"] == "2"


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
        sample_count=31 if readiness == "validated" else 8,
        win_rate_10d=63.0 if readiness == "validated" else 49.0,
        avg_return_10d=2.1 if readiness == "validated" else -0.2,
        avg_return_20d=4.2 if readiness == "validated" else 0.4,
        max_loss_10d=-4.8 if readiness == "validated" else -8.0,
        message="策略校准：测试样本。",
    )
    return card


def _bars(instrument_id: str) -> pd.DataFrame:
    start = date(2026, 1, 1)
    rows = []
    for index in range(80):
        close = 10 + index * 0.04
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
