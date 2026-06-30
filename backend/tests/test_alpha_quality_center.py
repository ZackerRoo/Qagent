from datetime import date, timedelta

import pandas as pd

from qagent.cards.factor_watch import build_factor_watch_card
from qagent.domain.models import StrategyCalibration
from qagent.factors.models import FactorRanking
from qagent.market.rotation_radar import build_rotation_radar
from qagent.recommendations.quality_gate import apply_recommendation_quality_gate
from qagent.research.alpha_quality import build_alpha_quality_center
from qagent.strategies.models import StrategyHealth, StrategyHealthPoint


def test_alpha_quality_center_turns_recommendations_into_buyability_policy():
    leader = _card("CN:688059", "华锐精密 688059.SH", 0.9, 1, "validated")
    leader.market_context.industry = "半导体"
    leader.market_context.themes = ["半导体", "硬科技", "科创板"]
    follower = _card("CN:688981", "中芯国际 688981.SH", 0.78, 2, "limited_sample")
    follower.market_context.industry = "半导体"
    follower.market_context.themes = ["半导体", "国产替代"]
    cards = [leader, follower]
    apply_recommendation_quality_gate(cards)
    radar = build_rotation_radar(cards, limit=5)

    center = build_alpha_quality_center(
        cards=cards,
        rotation_radar=radar,
        strategy_health=[_health()],
        data_health={"provider": "free", "operational_readiness_score": "0.72"},
        as_of=date(2026, 6, 30),
    )

    assert center.headline
    assert center.alpha_score > 0
    assert center.buyability_gate.verdict in {"可小仓位验证", "等待触发", "暂不参与"}
    assert center.buyability_gate.min_rank_score >= 0.6
    assert "华锐精密" in center.current_leader.instrument_label
    assert center.current_leader.strategy_score_text
    assert center.current_leader.buy_discipline
    assert center.current_leader.invalidation_rules
    assert center.strategy_tuning
    assert center.strategy_tuning[0].action in {"加权", "保持", "降权", "收集样本"}
    assert center.theme_confirmation
    assert any(item.name == "半导体" for item in center.theme_confirmation)
    assert center.data_health["alpha_quality_cards"] == "2"
    assert center.data_health["alpha_quality_strategies"] == str(len(center.strategy_tuning))
    assert center.data_health["alpha_quality_themes"] == str(len(center.theme_confirmation))


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
            liquidity_score=0.84,
            low_risk_score=0.72,
            reversal_score=0.56,
            execution_penalty=0.0,
            data_completeness=0.92,
            factor_exposures=[],
            flags=[],
            missing_data=[],
        ),
    )
    assert card is not None
    card.strategy_calibration = StrategyCalibration(
        strategy_id="trend_momentum_stage2",
        readiness=readiness,
        sample_count=36 if readiness == "validated" else 7,
        win_rate_10d=59.0 if readiness == "validated" else 45.0,
        avg_return_10d=2.4 if readiness == "validated" else -0.2,
        avg_return_20d=4.8 if readiness == "validated" else 0.1,
        max_loss_10d=-5.4 if readiness == "validated" else -8.3,
        message="策略校准：测试样本。",
    )
    return card


def _bars(instrument_id: str) -> pd.DataFrame:
    start = date(2026, 1, 1)
    rows = []
    for index in range(90):
        close = 10 + index * 0.06
        rows.append(
            {
                "instrument_id": instrument_id,
                "trade_date": start + timedelta(days=index),
                "open": close - 0.04,
                "high": close + 0.12,
                "low": close - 0.12,
                "close": close,
                "volume": 1_800_000 + index * 3_000,
                "provider": "free",
            }
        )
    return pd.DataFrame(rows)


def _health() -> StrategyHealth:
    points = [
        StrategyHealthPoint(
            label="2026-01",
            sample_count=10,
            win_rate_10d=57.0,
            avg_return_10d=1.4,
            avg_return_20d=2.7,
            max_loss_10d=-5.8,
        ),
        StrategyHealthPoint(
            label="2026-02",
            sample_count=12,
            win_rate_10d=61.0,
            avg_return_10d=2.9,
            avg_return_20d=5.1,
            max_loss_10d=-5.2,
        ),
    ]
    return StrategyHealth(
        strategy_id="trend_momentum_stage2",
        name="二阶段趋势动量",
        family="trend_momentum",
        readiness="validated",
        sample_count=sum(point.sample_count for point in points),
        win_rate_10d=60.0,
        avg_return_10d=2.3,
        avg_return_20d=4.9,
        max_loss_10d=-5.8,
        curve=points,
    )
