from datetime import date

from qagent.cards.generator import OpportunityCardGenerator
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.recommendations.decision import build_research_decision
from qagent.signals.engine import SignalEngine


def test_research_decision_scores_action_and_risk_controls():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 3, 31))
    signals = SignalEngine().generate("US:TEST", bars)
    card = OpportunityCardGenerator().generate("US:TEST", signals, bars)

    assert card is not None
    decision = build_research_decision(card)

    assert decision.action in {"candidate_entry", "watch_trigger", "wait_pullback"}
    assert decision.action_label
    assert 0 <= decision.conviction_score <= 1
    assert decision.components.strategy_quality >= 0
    assert decision.components.risk_reward >= 0
    assert decision.components.data_quality >= 0
    assert decision.components.execution_quality >= 0
    assert decision.suggested_risk_pct > 0
    assert decision.max_position_pct >= decision.suggested_risk_pct
    assert decision.trigger_price == card.entry_plan.trigger_price
    assert decision.initial_stop == card.exit_plan.initial_stop
    assert decision.target_1 == card.exit_plan.target_1
    assert decision.failure_conditions
    assert decision.verification_checks
    assert "guarantee" not in decision.model_dump_json().lower()
    assert "必涨" not in decision.model_dump_json()


def test_research_decision_avoids_weak_or_data_limited_cards():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 1, 15))
    signals = SignalEngine().generate("US:TEST", bars)
    card = OpportunityCardGenerator().generate("US:TEST", signals, bars)

    assert card is None or build_research_decision(card).action in {
        "watch_trigger",
        "wait_pullback",
        "avoid",
    }


def test_research_decision_blocks_when_risk_vetoes_are_material():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 3, 31))
    signals = SignalEngine().generate("US:TEST", bars)
    card = OpportunityCardGenerator().generate("US:TEST", signals, bars)

    assert card is not None
    card.risk_reward = 0.75
    card.factor_flags = ["overextended", "low_liquidity"]

    decision = build_research_decision(card)

    assert decision.risk_status == "blocked"
    assert decision.action == "avoid"
    assert decision.suggested_risk_pct == 0
    assert {veto.code for veto in decision.risk_vetoes}.issuperset(
        {"poor_risk_reward", "low_liquidity"}
    )
