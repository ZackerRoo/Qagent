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


def test_card_generator_reports_market_data_provider():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 3, 31))
    bars["provider"] = "yfinance"
    signals = SignalEngine().generate("US:TEST", bars)

    card = OpportunityCardGenerator().generate("US:TEST", signals, bars)

    assert card is not None
    assert card.data_caveats == ["provider: yfinance"]
