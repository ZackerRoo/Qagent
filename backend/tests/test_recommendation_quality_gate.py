from datetime import date, timedelta
from decimal import Decimal

import pandas as pd

from qagent.cards.factor_watch import build_factor_watch_card
from qagent.domain.models import StrategyCalibration
from qagent.factors.engine import build_factor_rankings
from qagent.factors.models import FactorRanking
from qagent.recommendations.quality_gate import apply_recommendation_quality_gate


def test_quality_gate_blocks_low_liquidity_and_marks_reason():
    card = _card(
        "CN:300001",
        factor_score=0.72,
        liquidity_score=0.08,
        trend_quality_score=0.6,
        low_risk_score=0.5,
        flags=["low_liquidity"],
    )

    apply_recommendation_quality_gate([card])

    assert card.recommendation_quality is not None
    assert card.recommendation_quality.tier == "risk_filtered"
    assert card.recommendation_quality.block_count >= 1
    assert any(check.code == "low_liquidity" for check in card.recommendation_quality.checks)
    assert card.rank_score < 0.55
    assert card.decision is not None
    assert card.decision.action == "avoid"
    assert card.pre_trade_risk is not None
    assert card.pre_trade_risk.status == "blocked"
    assert card.pre_trade_risk.can_buy is False
    assert any(check.code == "low_liquidity" for check in card.pre_trade_risk.checks)


def test_quality_gate_penalizes_overextended_a_share_momentum():
    strong_but_chasing = _card(
        "CN:688001",
        factor_score=0.92,
        momentum_score=0.98,
        trend_quality_score=0.9,
        low_risk_score=0.35,
        liquidity_score=0.8,
        flags=["overextended"],
    )
    balanced = _card(
        "CN:000001",
        factor_score=0.78,
        momentum_score=0.72,
        trend_quality_score=0.75,
        low_risk_score=0.86,
        liquidity_score=0.82,
    )

    apply_recommendation_quality_gate([strong_but_chasing, balanced])

    assert strong_but_chasing.recommendation_quality is not None
    assert balanced.recommendation_quality is not None
    assert (
        strong_but_chasing.recommendation_quality.warn_count
        + strong_but_chasing.recommendation_quality.block_count
        >= 1
    )
    assert any(
        check.code == "overextended"
        for check in strong_but_chasing.recommendation_quality.checks
    )
    assert balanced.recommendation_quality.score > strong_but_chasing.recommendation_quality.score
    assert balanced.rank_score > strong_but_chasing.rank_score


def test_quality_gate_adds_recommendation_score_v2_breakdown():
    card = _card(
        "CN:000001",
        factor_score=0.82,
        momentum_score=0.76,
        trend_quality_score=0.78,
        low_risk_score=0.84,
        liquidity_score=0.86,
    )

    apply_recommendation_quality_gate([card])

    assert card.recommendation_score is not None
    assert card.recommendation_score.version == "quality_v2"
    assert card.recommendation_score.final_score == card.rank_score
    assert card.recommendation_score.quality_score == card.recommendation_quality.score
    component_keys = {component.key for component in card.recommendation_score.components}
    assert {
        "factor_momentum",
        "trend_quality",
        "low_risk",
        "liquidity",
        "strategy_validation",
        "risk_reward",
        "market_context",
        "quality_penalties",
    }.issubset(component_keys)
    assert all(component.contribution >= 0 for component in card.recommendation_score.components)
    assert "推荐分" in card.recommendation_score.summary


def test_quality_gate_adds_account_level_position_scenario():
    card = _card(
        "CN:000001",
        factor_score=0.78,
        momentum_score=0.72,
        trend_quality_score=0.75,
        low_risk_score=0.86,
        liquidity_score=0.82,
    )

    apply_recommendation_quality_gate([card])

    scenario = card.position_scenario
    assert scenario is not None
    assert scenario.account_basis == "per_100k"
    assert scenario.entry_price == card.entry_plan.trigger_price
    assert scenario.stop_price == card.exit_plan.initial_stop
    assert scenario.target_1_price == card.exit_plan.target_1
    assert scenario.suggested_risk_pct == card.decision.suggested_risk_pct
    assert scenario.suggested_position_pct == card.decision.max_position_pct
    assert scenario.account_drawdown_if_stopped_pct <= scenario.suggested_risk_pct + 0.05
    assert scenario.account_gain_at_target_1_pct > 0
    assert scenario.min_lot == 100
    assert scenario.min_lot_cash is not None
    assert "10万元" in scenario.summary


def test_quality_gate_warns_when_a_share_permission_is_required():
    card = _card(
        "CN:688001",
        factor_score=0.78,
        momentum_score=0.72,
        trend_quality_score=0.75,
        low_risk_score=0.86,
        liquidity_score=0.82,
    )

    apply_recommendation_quality_gate([card])

    assert card.pre_trade_risk is not None
    assert card.pre_trade_risk.status == "warning"
    assert card.pre_trade_risk.can_buy is True
    assert any(check.code == "star_market_permission" for check in card.pre_trade_risk.checks)
    assert "权限" in card.pre_trade_risk.next_action


def test_a_share_factor_ranking_weights_low_risk_and_quality_not_only_momentum():
    bars = pd.concat(
        [
            _bars("CN:000001", drift=0.012, volatility=0.002, volume=1_500_000),
            _bars("CN:688001", drift=0.028, volatility=0.035, volume=1_400_000),
            _bars("CN:300001", drift=0.01, volatility=0.003, volume=50_000),
        ],
        ignore_index=True,
    )

    rankings = build_factor_rankings(bars)
    by_id = {ranking.instrument_id: ranking for ranking in rankings}

    assert by_id["CN:000001"].low_risk_score > by_id["CN:688001"].low_risk_score
    assert by_id["CN:000001"].factor_rank < by_id["CN:688001"].factor_rank
    assert "low_liquidity" in by_id["CN:300001"].flags


def _card(
    instrument_id: str,
    factor_score: float,
    momentum_score: float | None = None,
    trend_quality_score: float = 0.6,
    liquidity_score: float = 0.6,
    low_risk_score: float = 0.6,
    flags: list[str] | None = None,
):
    ranking = FactorRanking(
        instrument_id=instrument_id,
        instrument_label=instrument_id,
        factor_score=factor_score,
        factor_rank=1,
        percentile=factor_score,
        momentum_score=momentum_score if momentum_score is not None else factor_score,
        trend_quality_score=trend_quality_score,
        liquidity_score=liquidity_score,
        low_risk_score=low_risk_score,
        reversal_score=0.5,
        execution_penalty=0.0,
        data_completeness=0.9,
        factor_exposures=[],
        flags=flags or [],
        missing_data=[],
    )
    card = build_factor_watch_card(instrument_id, _bars(instrument_id), ranking)
    assert card is not None
    card.strategy_calibration = StrategyCalibration(
        strategy_id="factor_rotation_watch",
        readiness="validated",
        sample_count=32,
        win_rate_10d=58.0,
        avg_return_10d=1.4,
        avg_return_20d=2.8,
        max_loss_10d=-5.2,
        message="策略校准：测试样本。",
    )
    if "low_liquidity" in (flags or []):
        card.scenario.no_chase_pct = 3.0
    if "overextended" in (flags or []):
        card.entry_plan.no_chase_above = card.entry_plan.trigger_price
        card.scenario.no_chase_pct = 0.7
    return card


def _bars(
    instrument_id: str,
    drift: float = 0.012,
    volatility: float = 0.004,
    volume: int = 1_000_000,
) -> pd.DataFrame:
    start = date(2026, 1, 1)
    rows = []
    close = Decimal("10")
    for index in range(140):
        wave = Decimal(str(((index % 6) - 2.5) * volatility))
        close = close * (Decimal("1") + Decimal(str(drift)) + wave)
        rows.append(
            {
                "instrument_id": instrument_id,
                "trade_date": start + timedelta(days=index),
                "open": float(close * Decimal("0.99")),
                "high": float(close * Decimal("1.02")),
                "low": float(close * Decimal("0.98")),
                "close": float(close),
                "volume": volume + index * 200,
                "provider": "fixture",
            }
        )
    return pd.DataFrame(rows)
