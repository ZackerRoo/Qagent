from datetime import date

from qagent.cards.generator import OpportunityCardGenerator
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.signals.engine import SignalEngine
from qagent.strategy_data.providers import FixtureStrategyDataProvider
from qagent.strategies.evaluator import StrategyEvaluator
from qagent.strategies.registry import default_strategy_registry


def test_card_generator_creates_setup_ready_card():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 3, 31))
    signals = SignalEngine().generate("US:TEST", bars)
    card = OpportunityCardGenerator().generate("US:TEST", signals, bars)
    assert card is not None
    assert card.entry_plan.confirmation
    assert card.exit_plan.invalidation
    assert card.data_caveats == ["fixture data"]
    assert card.signals
    assert card.signals[0].signal_type
    assert card.signals[0].evidence
    assert card.strategy_evaluations
    assert card.primary_strategy_id in {
        "breakout_volume_confirmation",
        "trend_momentum_stage2",
        "gf_dma_health",
    }
    assert card.strategy_score >= card.score
    assert any(strategy.status == "missing_data" for strategy in card.strategy_evaluations)
    assert card.decision.action in {"candidate_entry", "watch_trigger", "wait_pullback"}
    assert card.decision.conviction_score >= 0.5
    assert card.decision.suggested_risk_pct > 0
    assert card.decision.trigger_price == card.entry_plan.trigger_price
    assert card.decision.initial_stop == card.exit_plan.initial_stop
    assert card.decision.target_1 == card.exit_plan.target_1
    assert card.decision.failure_conditions
    assert card.decision.verification_checks
    assert "guarantee" not in card.decision.model_dump_json().lower()


def test_card_generator_reports_market_data_provider():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 3, 31))
    bars["provider"] = "yfinance"
    signals = SignalEngine().generate("US:TEST", bars)

    card = OpportunityCardGenerator().generate("US:TEST", signals, bars)

    assert card is not None
    assert card.data_caveats == ["provider: yfinance"]


def test_card_generator_uses_pead_trade_plan_when_pead_is_primary():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 3, 31))
    signals = SignalEngine().generate("US:TEST", bars)
    earnings_events = FixtureStrategyDataProvider().get_earnings_events(
        ["US:TEST"], start=date(2026, 3, 1), end=date(2026, 3, 31)
    )
    evaluations = StrategyEvaluator(default_strategy_registry()).evaluate(
        "US:TEST",
        signals,
        bars,
        context={
            "earnings_events": earnings_events,
            "available_data": [
                "earnings_actuals",
                "earnings_estimates",
                "announcement_timestamp",
            ],
        },
    )
    card = OpportunityCardGenerator().generate("US:TEST", signals, bars, evaluations)

    assert card is not None
    assert card.primary_strategy_id == "pead_earnings_drift"
    assert card.entry_plan.entry_type == "pead"
    assert "earnings-day low" in card.exit_plan.invalidation
    assert card.rank_score >= card.strategy_score
    assert any("PEAD" in reason for reason in card.rank_reasons)
    assert card.decision.action in {"candidate_entry", "watch_trigger"}
    assert card.decision.horizon == "swing"
