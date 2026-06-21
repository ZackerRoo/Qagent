from datetime import date

from qagent.cards.generator import OpportunityCardGenerator
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.signals.engine import SignalEngine


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


def test_card_generator_reports_market_data_provider():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 3, 31))
    bars["provider"] = "yfinance"
    signals = SignalEngine().generate("US:TEST", bars)

    card = OpportunityCardGenerator().generate("US:TEST", signals, bars)

    assert card is not None
    assert card.data_caveats == ["provider: yfinance"]
