from datetime import date, timedelta

import pandas as pd

from qagent.cards.factor_watch import build_factor_watch_card
from qagent.domain.models import StrategyCalibration
from qagent.factors.models import FactorExposure, FactorRanking
from qagent.market.rotation_radar import build_rotation_radar
from qagent.recommendations.portfolio import build_portfolio_plan
from qagent.recommendations.probability import apply_probability_calibration
from qagent.recommendations.quality_gate import apply_recommendation_quality_gate
from qagent.recommendations.signal_hub import build_signal_hub
from qagent.research.command_center import build_research_command_center
from qagent.strategies.models import StrategyHealth, StrategyHealthPoint


def test_research_command_center_summarizes_portfolio_validation_attribution_alerts():
    memory = _card("CN:688008", "澜起科技 688008.SH", 0.86, 1, "validated")
    foundry = _card("CN:688981", "中芯国际 688981.SH", 0.81, 2, "validated")
    etf = _card("CN:588000", "科创50ETF 588000.SH", 0.74, 4, "limited_sample")
    blocked = _card("CN:000063", "中兴通讯 000063.SZ", 0.9, 3, "validated")
    assert blocked.decision is not None
    blocked.decision.action = "avoid"
    blocked.decision.risk_status = "blocked"
    blocked.opportunity_bucket = "risk_filtered"

    cards = [memory, foundry, etf, blocked]
    apply_recommendation_quality_gate(cards)
    apply_probability_calibration(cards, [_health()])
    rotation = build_rotation_radar(cards)
    for card in cards:
        card.signal_hub = build_signal_hub(card, rotation_score=0.82, rotation_name="半导体")
    plan = build_portfolio_plan(cards, max_positions=3)
    center = build_research_command_center(
        cards=cards,
        portfolio_plan=plan,
        rotation_radar=rotation,
        strategy_health=[_health()],
        data_health={
            "provider": "fixture",
            "market_cache": "enabled",
            "a_share_data_readiness_score": "0.72",
            "a_share_price_limit": "ready",
            "a_share_liquidity": "ready",
            "a_share_announcements": "partial",
        },
    )

    assert center.portfolio_advisor.suggested_positions >= 1
    assert center.portfolio_advisor.positions[0].instrument_label
    assert center.portfolio_advisor.cash_reserve_pct >= 0
    assert center.recommendation_pool_quality.asset_mix["etf"] >= 1
    assert center.recommendation_pool_quality.risk_filtered_count == 1
    assert center.walk_forward_validation.out_of_sample is not None
    assert center.walk_forward_validation.out_of_sample.sample_count >= 1
    assert center.strategy_attribution.strategies
    assert center.strategy_attribution.strategies[0].contribution_pct > 0
    assert center.alert_digest.total_suggestions >= 8
    assert center.alert_digest.by_kind["signal_weakened"] >= 1
    assert center.daily_research_summary.next_actions
    assert center.user_acceptance_audit.readiness_score > 0
    assert any(check.key == "opportunity_selection" for check in center.user_acceptance_audit.checks)
    assert any(check.key == "backtest_or_followthrough" for check in center.user_acceptance_audit.checks)
    assert center.ranking_calibration_audit.diagnostics
    assert any(
        item.key == "rank_probability_alignment"
        for item in center.ranking_calibration_audit.diagnostics
    )
    assert center.data_reliability_audit.score > 0
    assert any(check.key == "a_share_price_limit" for check in center.data_reliability_audit.checks)
    assert any(check.key == "market_cache" for check in center.data_reliability_audit.checks)
    assert center.data_health["research_center_cards"] == "4"
    assert "research_acceptance_score" in center.data_health
    assert "research_data_reliability_score" in center.data_health


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
        _ranking(instrument_id, label, factor_score, rank),
    )
    assert card is not None
    card.strategy_calibration = StrategyCalibration(
        strategy_id="factor_rotation_watch",
        readiness=readiness,
        sample_count=24 if readiness == "validated" else 9,
        win_rate_10d=62.5 if readiness == "validated" else 51.2,
        avg_return_10d=2.8 if readiness == "validated" else 0.7,
        avg_return_20d=5.2 if readiness == "validated" else 1.4,
        max_loss_10d=-5.6,
        message=f"策略校准：{label} 样本已验证。",
    )
    return card


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
        percentile=0.92,
        momentum_score=factor_score,
        trend_quality_score=0.78,
        liquidity_score=0.84,
        low_risk_score=0.7,
        reversal_score=0.58,
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
    for index in range(90):
        close = 10 + index * 0.06
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


def _health() -> StrategyHealth:
    points = [
        StrategyHealthPoint(
            label="2026-01",
            sample_count=8,
            win_rate_10d=56.0,
            avg_return_10d=1.1,
            avg_return_20d=2.3,
            max_loss_10d=-6.2,
        ),
        StrategyHealthPoint(
            label="2026-02",
            sample_count=10,
            win_rate_10d=61.0,
            avg_return_10d=2.4,
            avg_return_20d=4.8,
            max_loss_10d=-5.1,
        ),
        StrategyHealthPoint(
            label="2026-03",
            sample_count=9,
            win_rate_10d=58.0,
            avg_return_10d=1.8,
            avg_return_20d=3.6,
            max_loss_10d=-6.4,
        ),
        StrategyHealthPoint(
            label="2026-04",
            sample_count=7,
            win_rate_10d=64.0,
            avg_return_10d=3.1,
            avg_return_20d=5.5,
            max_loss_10d=-4.8,
        ),
    ]
    return StrategyHealth(
        strategy_id="factor_rotation_watch",
        name="因子轮动观察",
        family="factor",
        readiness="validated",
        sample_count=sum(point.sample_count for point in points),
        win_rate_10d=60.0,
        avg_return_10d=2.1,
        avg_return_20d=4.2,
        max_loss_10d=-6.4,
        curve=points,
    )
