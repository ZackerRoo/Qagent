from datetime import date
from decimal import Decimal

import pandas as pd

from qagent.cards.entry_exit import build_breakout_plan
from qagent.domain.enums import Market, OpportunityStatus
from qagent.domain.models import OpportunityCard
from qagent.monitoring.signal_monitor import build_signal_monitor_center
from qagent.recommendations.decision import build_research_decision


def test_signal_monitor_classifies_trigger_stop_target_and_weakened_actions():
    cards = [
        _card("CN:000001", latest=Decimal("10.60")),
        _card("CN:000002", latest=Decimal("9.40")),
        _card("CN:000003", latest=Decimal("11.85")),
        _card("CN:000004", latest=Decimal("12.10")),
        _card("CN:000005", latest=Decimal("9.80"), rank_score=0.38),
    ]
    bars = {
        "CN:000001": _bars("CN:000001", close=10.60, high=10.70, low=10.20),
        "CN:000002": _bars("CN:000002", close=8.95, high=9.80, low=8.90),
        "CN:000003": _bars("CN:000003", close=11.80, high=11.88, low=11.40),
        "CN:000004": _bars("CN:000004", close=12.20, high=12.25, low=11.70),
        "CN:000005": _bars("CN:000005", close=9.80, high=9.90, low=9.60),
    }

    center = build_signal_monitor_center(cards, bars_by_instrument=bars, as_of=date(2026, 6, 30))
    by_id = {item.instrument_id: item for item in center.items}

    assert center.total == 5
    assert center.triggered_count >= 1
    assert center.stop_breached_count == 1
    assert center.near_target_count >= 1
    assert center.target_reached_count == 1
    assert center.weakened_count == 1
    assert by_id["CN:000001"].state == "entry_triggered"
    assert by_id["CN:000002"].state == "stop_breached"
    assert by_id["CN:000003"].state == "near_target"
    assert by_id["CN:000004"].state == "target_reached"
    assert by_id["CN:000005"].state == "recommendation_weakened"
    assert by_id["CN:000002"].severity == "block"
    assert by_id["CN:000004"].action == "接近或到达目标，考虑分批止盈或上移止损。"
    assert center.action_queue[0].severity == "block"
    assert "触发" in center.headline
    assert center.data_health["signal_monitor_total"] == "5"


def test_signal_monitor_returns_empty_center_without_cards():
    center = build_signal_monitor_center([], bars_by_instrument={}, as_of=date(2026, 6, 30))

    assert center.total == 0
    assert center.items == []
    assert center.headline == "暂无可监控推荐，先完成一次机会扫描。"


def _card(
    instrument_id: str,
    *,
    latest: Decimal,
    rank_score: float = 0.78,
) -> OpportunityCard:
    plan = build_breakout_plan(latest_close=latest, pivot=Decimal("10.00"), atr=Decimal("1.00"))
    card = OpportunityCard(
        card_id=f"card-{instrument_id}",
        instrument_id=instrument_id,
        instrument_label=instrument_id,
        market=Market.CN,
        status=OpportunityStatus.SETUP_READY,
        thesis="测试机会",
        score=0.76,
        entry_plan=plan.entry_plan,
        exit_plan=plan.exit_plan,
        risk_reward=plan.risk_reward,
        scenario=plan.scenario,
        strategy_score=0.7,
        rank_score=rank_score,
        factor_score=0.7,
    )
    card.decision = build_research_decision(card)
    return card


def _bars(instrument_id: str, *, close: float, high: float, low: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "instrument_id": instrument_id,
                "trade_date": date(2026, 6, 30),
                "open": close,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1_000_000,
                "provider": "fixture",
            }
        ]
    )
