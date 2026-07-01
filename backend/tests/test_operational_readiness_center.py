from datetime import date, timedelta

import pandas as pd

from qagent.cards.factor_watch import build_factor_watch_card
from qagent.domain.models import StrategyCalibration
from qagent.factors.models import FactorRanking
from qagent.monitoring.signal_monitor import build_signal_monitor_center
from qagent.recommendations.portfolio import build_portfolio_plan
from qagent.recommendations.quality_gate import apply_recommendation_quality_gate
from qagent.research.decision_quality import build_decision_quality_center
from qagent.research.market_intelligence import build_market_intelligence_center
from qagent.research.operational_readiness import build_operational_readiness_center
from qagent.strategies.models import StrategyHealth, StrategyHealthPoint


def test_operational_readiness_center_answers_new_user_decision_questions():
    top = _card("CN:688059", "华锐精密 688059.SH", 0.88, 1, "validated")
    second = _card("CN:588000", "科创50ETF 588000.SH", 0.76, 2, "limited_sample")
    previous = _card("CN:688059", "华锐精密 688059.SH", 0.81, 3, "validated")
    cards = [top, second]
    apply_recommendation_quality_gate(cards)
    bars_by_instrument = {card.instrument_id: _bars(card.instrument_id) for card in cards}
    health = [_health()]
    market = build_market_intelligence_center(
        cards=cards,
        items=[],
        bars_by_instrument=bars_by_instrument,
        strategy_health=health,
        data_health={
            "provider": "free",
            "market_cache": "enabled",
            "a_share_adjustment": "missing",
            "a_share_price_limit": "ready",
        },
    )
    monitor = build_signal_monitor_center(cards, bars_by_instrument=bars_by_instrument)
    plan = build_portfolio_plan(cards, max_positions=2)
    decision = build_decision_quality_center(
        cards=cards,
        market_intelligence=market,
        portfolio_plan=plan,
        signal_monitor=monitor,
        strategy_health=health,
        data_health={"provider": "free"},
    )

    center = build_operational_readiness_center(
        cards=cards,
        previous_cards=[previous],
        market_intelligence=market,
        decision_quality_center=decision,
        signal_monitor=monitor,
        strategy_health=health,
        data_health={"provider": "free", "market_cache": "enabled"},
        as_of=date(2026, 6, 30),
    )

    assert center.headline
    assert center.readiness_score > 0
    assert center.as_of == date(2026, 6, 30)
    assert {item.key for item in center.checks} == {
        "data_source_realism",
        "strategy_self_learning",
        "backtest_realism",
        "paper_account",
        "alert_system",
        "recommendation_stability",
    }
    assert center.check_by_key("data_source_realism").label == "数据源真实度"
    assert center.check_by_key("alert_system").evidence
    assert center.strategy_learning
    assert center.stability_audit
    top_question = next(item for item in center.user_questions if item.key == "top_recommendation")
    assert "华锐精密" in top_question.answer
    assert "688059.SH" in top_question.answer
    score_question = next(item for item in center.user_questions if item.key == "strategy_score")
    assert "策略分" in score_question.answer
    assert "排序分" in score_question.answer
    plan_question = next(item for item in center.user_questions if item.key == "trade_plan")
    assert "买点" in plan_question.answer
    assert "止损" in plan_question.answer
    assert "目标" in plan_question.answer
    assert center.data_health["operational_readiness_checks"] == "6"
    assert center.data_health["operational_readiness_questions"] == str(len(center.user_questions))


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
            liquidity_score=0.82,
            low_risk_score=0.76,
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
        sample_count=32 if readiness == "validated" else 8,
        win_rate_10d=61.0 if readiness == "validated" else 47.0,
        avg_return_10d=2.2 if readiness == "validated" else -0.3,
        avg_return_20d=4.4 if readiness == "validated" else 0.5,
        max_loss_10d=-4.8 if readiness == "validated" else -8.5,
        message="策略校准：测试样本。",
    )
    return card


def _bars(instrument_id: str) -> pd.DataFrame:
    start = date(2026, 1, 1)
    rows = []
    for index in range(90):
        close = 10 + index * 0.05
        rows.append(
            {
                "instrument_id": instrument_id,
                "trade_date": start + timedelta(days=index),
                "open": close - 0.03,
                "high": close + 0.1,
                "low": close - 0.1,
                "close": close,
                "volume": 1_500_000 + index * 2_000,
                "provider": "free",
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
            avg_return_20d=2.2,
            max_loss_10d=-6.2,
        ),
        StrategyHealthPoint(
            label="2026-02",
            sample_count=11,
            win_rate_10d=64.0,
            avg_return_10d=2.8,
            avg_return_20d=5.1,
            max_loss_10d=-4.9,
        ),
    ]
    return StrategyHealth(
        strategy_id="factor_rotation_watch",
        name="因子轮动观察",
        family="factor_rotation",
        readiness="validated",
        sample_count=sum(point.sample_count for point in points),
        win_rate_10d=61.0,
        avg_return_10d=2.2,
        avg_return_20d=4.4,
        max_loss_10d=-6.2,
        curve=points,
    )
