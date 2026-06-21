from datetime import date
from decimal import Decimal

from qagent.strategy_data.providers import FixtureStrategyDataProvider


def test_fixture_strategy_data_provider_returns_complete_us_earnings_event():
    provider = FixtureStrategyDataProvider()

    events = provider.get_earnings_events(
        ["US:TEST"],
        start=date(2026, 3, 1),
        end=date(2026, 3, 31),
    )

    assert len(events) == 1
    event = events[0]
    assert event.instrument_id == "US:TEST"
    assert event.announcement_date == date(2026, 3, 31)
    assert event.actual_eps == Decimal("1.34")
    assert event.estimated_eps == Decimal("1.05")
    assert event.has_pead_inputs is True


def test_fixture_strategy_data_provider_marks_incomplete_a_share_estimates():
    provider = FixtureStrategyDataProvider()

    events = provider.get_earnings_events(
        ["CN:000001"],
        start=date(2026, 3, 1),
        end=date(2026, 3, 31),
    )

    assert len(events) == 1
    assert events[0].instrument_id == "CN:000001"
    assert events[0].has_pead_inputs is False
